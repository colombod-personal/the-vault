"""Card lookup and the sets catalog: the Vault answers, so the browser never calls Scryfall's API."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from twins import Universe
from vault.app import create_app
from vault.catalog import Catalog
from vault.config import Settings
from vault.db import Database

V1 = "/api/v1"
SOL_RING = "736f0d31-9052-5d04-b5d5-727ebc7f5cc9"


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, f"calls left the twin universe: {u.escapes}"


@pytest.fixture
def app(database_url, tmp_path, universe):
    settings = Settings(database_url=database_url, session_secret="test", dev_login=True,
                        base_url="http://testserver")
    return create_app(settings, serve_static=False, transport=universe.transport)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        yield c


def collection_calls(universe):
    return [c for c in universe.scryfall.calls if c.path == "/cards/collection"]


def lookup(client, *identifiers, **extra):
    return client.post(f"{V1}/cards/lookup", json={"identifiers": list(identifiers), **extra}, headers=extra.pop("headers", {}))


def test_a_miss_is_fetched_once_then_served_by_the_vault(client, universe):
    first = lookup(client, {"id": SOL_RING}, {"set": "mkm", "collector_number": "167"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert {c["name"] for c in body["data"]} == {"Sol Ring", "A Killer Among Us"}
    assert body["not_found"] == [] and body["unavailable"] is False
    sol = next(c for c in body["data"] if c["id"] == SOL_RING)
    assert sol["set"] == "c21" and sol["image_uris"]["normal"].startswith("https://cards.scryfall.io/")
    assert isinstance(sol["prices"]["usd"], str) and sol["prices_as_of"]
    assert len(collection_calls(universe)) == 1

    again = lookup(client, {"name": "sol ring"}, {"set": "MKM", "collector_number": "167"}).json()
    assert {c["name"] for c in again["data"]} == {"Sol Ring", "A Killer Among Us"}
    assert len(collection_calls(universe)) == 1  # answered from the Vault's own table


def test_refresh_fetches_new_prices(client, universe):
    lookup(client, {"id": SOL_RING})
    universe.scryfall.set_price(SOL_RING, usd=9.99)
    assert lookup(client, {"id": SOL_RING}).json()["data"][0]["prices"]["usd"] != "9.99"
    assert lookup(client, {"id": SOL_RING}, refresh=True).json()["data"][0]["prices"]["usd"] == "9.99"
    assert len(collection_calls(universe)) == 2


def test_unknown_cards_are_not_found(client):
    body = lookup(client, {"name": "Definitely Not A Card"}, {"id": SOL_RING}).json()
    assert body["not_found"] == [{"name": "Definitely Not A Card"}] and [c["name"] for c in body["data"]] == ["Sol Ring"]


def test_scryfall_down_is_reported_not_an_error(client, universe):
    universe.scryfall.fail_next("/cards/collection", 503, times=5)
    body = lookup(client, {"id": SOL_RING})
    assert body.status_code == 200
    assert body.json() == {**body.json(), "data": [], "unavailable": True}


def test_bad_requests(client):
    assert lookup(client).status_code == 422
    assert lookup(client, *[{"id": SOL_RING}] * 76).status_code == 422
    assert lookup(client, {}).status_code == 422


def test_lookup_needs_a_signed_in_user(app):
    with TestClient(app) as anonymous:
        assert lookup(anonymous, {"id": SOL_RING}).status_code == 401


def test_read_only_token_and_mcp_can_look_up(client, app, universe):
    token = client.post(f"{V1}/me/tokens", json={"name": "bot", "scopes": ["read"]}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as bot:
        res = bot.post(f"{V1}/cards/lookup", json={"identifiers": [{"id": SOL_RING}]}, headers=h)
        assert res.status_code == 200 and res.json()["data"][0]["name"] == "Sol Ring"
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "lookup_cards", "arguments": {"identifiers": [{"name": "Lightning Bolt"}]}}}
        result = bot.post("/api/mcp", json=call, headers=h).json()["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["data"][0]["name"] == "Lightning Bolt"


def test_sets_catalog_is_public_and_cached(app, universe):
    with TestClient(app) as anonymous:
        res = anonymous.get(f"{V1}/catalog/sets")
        assert res.status_code == 200, res.text
        assert "max-age=86400" in res.headers["cache-control"]
        body = res.json()
        assert body["count"] == len(body["items"]) > 0
        assert {"code", "name", "icon_svg_uri"} <= set(body["items"][0])
        assert body["aliases"]["gk2_orzhov"] == "gk2"  # Dragon Shield's set codes, for the set icons
        anonymous.get(f"{V1}/catalog/sets")
    assert len([c for c in universe.scryfall.calls if c.path == "/sets"]) == 1


def test_sets_catalog_when_scryfall_is_down(app, universe):
    universe.scryfall.fail_next("/sets", 503, times=5)
    with TestClient(app) as anonymous:
        res = anonymous.get(f"{V1}/catalog/sets")
    assert res.status_code == 503 and res.headers["retry-after"]


def test_front_face_names_match_and_wildcards_do_not(client, universe):
    universe.scryfall.add_card("Fire // Ice", "mh2", "290")
    assert lookup(client, {"name": "Fire // Ice"}).json()["data"][0]["name"] == "Fire // Ice"
    calls = len(collection_calls(universe))
    assert lookup(client, {"name": "fire"}).json()["data"][0]["name"] == "Fire // Ice"  # front face, from the Vault
    assert len(collection_calls(universe)) == calls
    assert lookup(client, {"name": "%"}).json()["not_found"] == [{"name": "%"}]


# -- concurrency: a request that waited for the Scryfall lock must not repeat another's work -----

class HookLock:
    """Stands in for Catalog._lock: runs ``before`` when acquired, as if another request had
    held the lock and finished just before this one got it."""

    def __init__(self, before):
        self.before, self.inner = before, threading.Lock()

    def __enter__(self):
        self.inner.acquire()
        self.before()

    def __exit__(self, *exc):
        self.inner.release()


def test_a_lookup_that_waited_for_the_lock_uses_the_cards_stored_meanwhile(database_url, tmp_path, universe):
    db = Database(database_url)
    db.migrate()
    catalog = Catalog(universe.transport)
    other = Catalog(universe.transport)

    def the_other_request_finishes():
        with Session(db.engine) as s:
            other.lookup(s, [{"id": SOL_RING}])

    catalog._lock = HookLock(the_other_request_finishes)
    with Session(db.engine) as s:
        body = catalog.lookup(s, [{"id": SOL_RING}])
    assert [c["name"] for c in body["data"]] == ["Sol Ring"]
    assert len(collection_calls(universe)) == 1  # only the other request asked Scryfall


def test_a_sets_refresh_that_waited_for_the_lock_uses_the_list_fetched_meanwhile(universe):
    catalog = Catalog(universe.transport)
    other = Catalog(universe.transport)
    catalog._lock = HookLock(lambda: setattr(catalog, "_sets", (time.monotonic(), other.sets())))
    assert catalog.sets()
    assert len([c for c in universe.scryfall.calls if c.path == "/sets"]) == 1


def test_sets_catalog_is_paged(app, universe):
    with TestClient(app) as anonymous:
        first = anonymous.get(f"{V1}/catalog/sets", params={"limit": 2}).json()
        assert first["count"] == 2 and first["total"] > 2 and first["_links"]["next"]
        codes, url = [], f"{V1}/catalog/sets?limit=2"
        while url:
            page = anonymous.get(url).json()
            codes += [s["code"] for s in page["items"]]
            url = page["_links"].get("next", {}).get("href")
        assert len(codes) == first["total"] == len(set(codes))
        assert anonymous.get(f"{V1}/catalog/sets", params={"limit": 501}).json()["count"] <= 500
    assert len([c for c in universe.scryfall.calls if c.path == "/sets"]) == 1  # one fetch served every page


def test_a_dragon_shield_set_code_finds_the_card(client, universe):
    # The collection keeps Dragon Shield's own code (GK2_ORZHOV); lookups translate it, upstream and in the Vault.
    ident = {"set": "GK2_ORZHOV", "collector_number": "29"}
    body = lookup(client, ident).json()
    assert [c["name"] for c in body["data"]] == ["Belfry Spirit"] and body["not_found"] == []
    again = lookup(client, ident).json()
    assert [c["name"] for c in again["data"]] == ["Belfry Spirit"]
    assert len(collection_calls(universe)) == 1  # the second time, from the Vault's own table
    missing = {"set": "GK2_ORZHOV", "collector_number": "9999"}
    assert lookup(client, missing).json()["not_found"] == [missing]  # reported as asked


@pytest.mark.parametrize("ident", [{"set": "mkm"}, {"collector_number": "167"}, {}, {"set": "mkm", "name": None}])
def test_an_incomplete_identifier_is_refused_before_any_lookup(client, universe, ident):
    res = lookup(client, ident)
    assert res.status_code == 422, res.text
    assert collection_calls(universe) == []
