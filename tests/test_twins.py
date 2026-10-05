"""The Vault against the Scryfall and Archidekt twins, and the twin universe itself."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from mtg_toolkits.http import ApiError
from mtg_toolkits.scryfall import ScryfallClient

from jobs import sync_prices
from twins import Universe
from twins.server import create_server
from vault import outbound
from vault.app import create_app
from vault.config import Settings

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


def scryfall(universe, **kwargs):
    return ScryfallClient(client=universe.client(), min_interval=0, **kwargs)


# -- Scryfall ---------------------------------------------------------------------------------

def test_library_client_against_the_scryfall_twin(universe):
    with scryfall(universe) as sf:
        sf.slow_interval = 0
        assert sf.named("sol ring").set_code == "c21"
        assert sf.card_by_set_number("mh3", "512").finishes == ["etched"]
        delver = sf.named("Delver of Secrets")
        assert delver.name == "Delver of Secrets // Insectile Aberration" and delver.image_uris["normal"]
        found, missing = sf.collection([{"name": "Lightning Bolt"}, {"set": "c21", "collector_number": "263"},
                                        {"name": "Not A Card"}])
        assert [c.name for c in found] == ["Lightning Bolt", "Sol Ring"] and missing == [{"name": "Not A Card"}]
        assert [c.name for c in sf.search("set:dom")] == ["Llanowar Elves"]
        with pytest.raises(ApiError) as err:
            sf.named("Nothing Like This")
        assert err.value.status_code == 404


def test_scryfall_rules_are_enforced(universe):
    http = universe.client()
    assert http.get("https://api.scryfall.com/cards/named", params={"exact": "Sol Ring"},
                    headers={"User-Agent": ""}).json()["code"] == "bad_request"  # User-Agent and Accept required
    ok = {"User-Agent": "test/1", "Accept": "application/json"}
    too_many = {"identifiers": [{"name": "Sol Ring"}] * 76}
    res = http.post("https://api.scryfall.com/cards/collection", json=too_many, headers=ok)
    assert res.status_code == 422 and res.json()["object"] == "error"


def test_scryfall_rate_limit_and_lockout(universe):
    now = [100.0]
    universe.scryfall.enforce_rate_limits, universe.scryfall.clock = True, lambda: now[0]
    http = universe.client(headers={"User-Agent": "test/1", "Accept": "application/json"})
    get = lambda: http.get("https://api.scryfall.com/cards/search", params={"q": "sol"}).status_code  # noqa: E731
    assert get() == 200
    now[0] += 0.2
    assert get() == 429  # search allows one call per 500 ms
    now[0] += 5
    assert get() == 429  # ...and going faster locks you out for 30 s
    now[0] += 30
    assert get() == 200


def test_daily_price_sync_through_the_twin(database_url, tmp_path, monkeypatch, universe):
    """The real job: bulk-data listing -> download the .jsonl.gz -> match -> prices in the API."""
    db = database_url
    settings = Settings(database_url=db, session_secret="t", base_url="http://testserver", dev_login=True)
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as client:
        client.post("/api/auth/dev-login")
        client.post("/api/v1/imports", files={"file": ("e.csv", CSV, "text/csv")})
        monkeypatch.setenv("DATABASE_URL", db)
        sync_prices.main([], transport=universe.transport)
        paths = [c.path for c in universe.scryfall.calls]
        assert paths[0] == "/bulk-data" and paths[1].endswith(".jsonl.gz")
        cards = {c["name"]: c for c in client.get("/api/v1/collection/cards").json()["items"]}
        sol_ring = cards["Sol Ring"]  # a foil copy in the fixture: priced at Scryfall's usd_foil
        assert (sol_ring["finish"], sol_ring["price"]["market"], sol_ring["price"]["source"]) == ("foil", 3.4, "scryfall")
        # prices move at Scryfall; the next sync picks them up
        sol = universe.scryfall.find("Sol Ring")
        universe.scryfall.set_price(sol["id"], usd_foil=5.0)
        sync_prices.main([], transport=universe.transport)
        cards = {c["name"]: c for c in client.get("/api/v1/collection/cards").json()["items"]}
        assert cards["Sol Ring"]["price"]["market"] == 5.0


# -- Archidekt --------------------------------------------------------------------------------

def test_archidekt_decks_through_the_twin(database_url, tmp_path, universe):
    public = universe.archidekt.add_deck("Elves", "ann", [(1, "Llanowar Elves", None, None, "Ramp"), (1, "Sol Ring")])
    private = universe.archidekt.add_deck("Secret", "ann", [(1, "Sol Ring")], private=True)
    settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True)
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as client:
        client.post("/api/auth/dev-login")
        deck = client.get(f"/api/v1/archidekt/decks/{public['id']}").json()
        assert deck["name"] == "Elves" and deck["cards"][0]["card"]["edition"]["editioncode"] == "dom"
        assert client.get(f"/api/v1/archidekt/decks/{private['id']}").status_code == 404
        universe.archidekt.outage = True
        assert client.get(f"/api/v1/archidekt/decks/{public['id']}").status_code == 502


def test_archidekt_reads_are_rate_limited_per_person_and_for_everyone(database_url, universe):
    """Archidekt's terms forbid automated requests, so the Vault reads one public deck per request,
    at most a few a minute per person and a capped number for everyone; a refused read never reaches
    Archidekt."""
    deck = universe.archidekt.add_deck("Elves", "ann", [(1, "Sol Ring")])
    settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True,
                        archidekt_rate_limit=3, archidekt_global_rate_limit=5)
    url = f"/api/v1/archidekt/decks/{deck['id']}"
    reads = lambda: len([c for c in universe.archidekt.calls if c.path.startswith("/api/decks/")])  # noqa: E731
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as client:
        client.post("/api/auth/dev-login?email=ann@example.com")
        assert [client.get(url).status_code for _ in range(4)] == [200, 200, 200, 429]
        assert reads() == 3
        refused = client.get(url)
        assert "Archidekt" in refused.json()["detail"] and int(refused.headers["Retry-After"]) >= 1
        client.post("/api/auth/dev-login?email=bo@example.com")  # someone else: their own allowance, until the shared cap
        assert [client.get(url).status_code for _ in range(3)] == [200, 200, 429]
        assert reads() == 5


def test_archidekt_search_pages_like_drf(universe):
    for i in range(60):
        universe.archidekt.add_deck(f"Deck {i}", "bo", [(1, "Sol Ring")])
    from mtg_toolkits.archidekt import ArchidektClient

    with ArchidektClient(client=universe.client(), min_interval=0) as client:
        assert len(list(client.search_decks(ownerUsername="bo"))) == 60  # follows "next" across pages


# -- the universe -----------------------------------------------------------------------------

def test_the_universe_is_sealed():
    universe = Universe()
    with pytest.raises(httpx.ConnectError):
        universe.client().get("https://example.com/")
    assert universe.escapes == ["https://example.com/"]


def test_twin_server_for_browsers_and_agents():
    universe = Universe()
    server = TestClient(create_server(universe, "http://twins.test"))
    res = server.get("/h/api.scryfall.com/cards/named", params={"exact": "Sol Ring"},
                     headers={"User-Agent": "t", "Accept": "application/json"})
    assert res.json()["name"] == "Sol Ring" and res.headers["access-control-allow-origin"] == "*"
    assert res.json()["image_uris"]["normal"].startswith("https://cards.scryfall.io/")  # servers get the real shape
    browser = server.get("/h/api.scryfall.com/cards/named", params={"exact": "Sol Ring"},
                         headers={"User-Agent": "t", "Accept": "*/*", "X-Twin-Browser": "1"}).json()
    image = browser["image_uris"]["normal"]
    assert image.startswith("http://twins.test/h/cards.scryfall.io/")  # browsers load images from the twin
    assert server.get(image.removeprefix("http://twins.test")).headers["content-type"] == "image/svg+xml"
    assert server.post("/_twins/api/scryfall/outage", json={"on": True}).json() == {"ok": True}
    assert server.get("/h/api.scryfall.com/sets").status_code == 503
    server.post("/_twins/api/reset")
    token = server.post("/_twins/api/apple/native-token", json={"aud": "com.example", "sub": "a-1"}).json()["id_token"]
    assert token.count(".") == 2
    assert "digital twin universe" in server.get("/_twins").text
    assert server.get("/h/example.com/").status_code == 502


def test_vault_routes_outbound_calls_to_the_twin_server():
    settings = Settings(twins_url="http://localhost:9000", base_url="http://localhost:8000")
    routed = outbound.transport(settings)._rewrite(httpx.Request("GET", "https://appleid.apple.com/auth/keys?x=1"))
    assert str(routed.url) == "http://localhost:9000/h/appleid.apple.com/auth/keys?x=1"
    assert outbound.browser_url(settings, "https://accounts.google.com/o/oauth2/v2/auth?a=b") == \
        "http://localhost:9000/h/accounts.google.com/o/oauth2/v2/auth?a=b"
    with pytest.raises(RuntimeError, match="local development only"):
        Settings(database_url="postgresql://u@db/vault", twins_url="http://localhost:9000",
                 base_url="https://vault.example", session_secret="s").check()
