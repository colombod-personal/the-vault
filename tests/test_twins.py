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
        assert deck["deck"]["name"] == "Elves" and "1 Llanowar Elves" in deck["text"] and deck["deck"]["author"] == "ann"
        assert client.get(f"/api/v1/archidekt/decks/{private['id']}").status_code == 404
        universe.archidekt.outage = True
        again = client.get(f"/api/v1/archidekt/decks/{public['id']}")  # a recent copy is served: Archidekt is not asked
        assert again.status_code == 200 and again.json()["vault_cache"]["from_cache"] is True
        unseen = universe.archidekt.add_deck("Never read", "ann", [(1, "Sol Ring")])
        assert client.get(f"/api/v1/archidekt/decks/{unseen['id']}").status_code == 502  # nothing cached: the outage shows


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


def test_repeat_reads_of_a_deck_reach_archidekt_once_and_say_how_old_they_are(database_url, universe):
    from datetime import datetime, timedelta, timezone

    from vault import archidekt_cache
    from vault.models import ArchidektDeckCache

    deck = universe.archidekt.add_deck("Elves", "ann", [(1, "Sol Ring")])
    settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True)
    app = create_app(settings, serve_static=False, transport=universe.transport)
    path = f"/api/v1/archidekt/decks/{deck['id']}"

    def requests():
        return [c for c in universe.archidekt.calls if c.path.rstrip("/").endswith(f"/decks/{deck['id']}")]

    with TestClient(app) as client:
        client.post("/api/auth/dev-login")
        first = client.get(path).json()
        assert first["vault_cache"]["from_cache"] is False and len(requests()) == 1
        for _ in range(3):  # the deck page, the graph overlay, the AI tool and the library tiles all read it again
            again = client.get(path).json()
            assert again["vault_cache"]["from_cache"] is True and again["deck"]["name"] == "Elves"
        assert len(requests()) == 1
        assert client.get(path, params={"refresh": "true"}).json()["vault_cache"]["from_cache"] is True  # under a minute old
        assert len(requests()) == 1
        with app.state.db.sessions() as db:  # age the copy: a refresh now asks Archidekt again
            row = db.get(ArchidektDeckCache, deck["id"])
            row.fetched_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            db.commit()
        assert client.get(path).json()["vault_cache"]["from_cache"] is True  # still inside the 10 minutes
        refreshed = client.get(path, params={"refresh": "true"}).json()
        assert refreshed["vault_cache"]["from_cache"] is False and len(requests()) == 2
        with app.state.db.sessions() as db:  # old entries are deleted when the next one is written
            old = db.get(ArchidektDeckCache, deck["id"])
            old.fetched_at = datetime.now(timezone.utc) - archidekt_cache.RETENTION - timedelta(days=1)
            db.commit()
        other = universe.archidekt.add_deck("Other", "ann", [(1, "Sol Ring")])
        client.get(f"/api/v1/archidekt/decks/{other['id']}")
        with app.state.db.sessions() as db:
            assert db.get(ArchidektDeckCache, deck["id"]) is None


def test_the_archidekt_twin_has_every_field_the_real_api_sends():
    """#231: the twin was a third the size of a real deck and lacked 5 deck fields, 22 card-analysis fields and 25 price
    fields, so tests never met the shop prices or the size that real Archidekt answers carry. The key sets come from a
    real deck captured on 2026-10-06 (tests/fixtures/archidekt_real_keys.json); the nightly conformance run keeps it honest."""
    import json
    from pathlib import Path
    real = json.loads((Path(__file__).parent / "fixtures" / "archidekt_real_keys.json").read_text(encoding="utf-8"))
    deck = Universe().archidekt.add_deck("Elves", "ann", [(1, "Llanowar Elves", None, None, "Ramp"), (1, "Sol Ring")])
    entry = deck["cards"][0]
    got = {"deck": deck, "entry": entry, "card": entry["card"], "oracleCard": entry["card"]["oracleCard"],
           "edition": entry["card"]["edition"], "prices": entry["card"]["prices"]}
    for part, keys in got.items():
        assert set(keys) == set(real[part]), (part, sorted(set(real[part]) ^ set(keys)))
    assert len(json.dumps(deck["cards"])) // len(deck["cards"]) > 2000  # real: 3,318 bytes a card, so size limits are exercised


def test_a_real_sized_archidekt_deck_comes_back_small_with_its_identity_and_no_shop_prices(database_url, universe):
    """#219: one real deck answered 313,760 characters, mostly other shops' per-card prices. The twin is now as heavy."""
    import json

    cards = [(1, "Sliver Overlord", None, None, "Commander")] + [(1, f"Test Card {i:02d}", None, None, "Ramp") for i in range(99)]
    deck = universe.archidekt.add_deck("Sliver Swarm", "ann", cards, deck_format=3)
    assert len(json.dumps(deck)) > 200_000  # the realistic twin: the size that broke the assistants
    settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True)
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as client:
        client.post("/api/auth/dev-login")
        answer = client.get(f"/api/v1/archidekt/decks/{deck['id']}").json()
    text = json.dumps(answer)
    assert len(text) < 12_000, len(text)
    assert list(answer)[0] == "deck" and answer["deck"]["overview"]["format"] == "commander"
    assert answer["deck"]["overview"]["commanders"] == ["Sliver Overlord"] and answer["deck"]["overview"]["cards"] == 100
    assert answer["credit"]["source"] == "Archidekt" and answer["deck"]["url"] == f"https://archidekt.com/decks/{deck['id']}"
    for shop_price in ("ckFoil", "cmMinimum", "tcgLand", "scgSku", "cardTrader", "\"prices\""):  # no shop's price, id or SKU
        assert shop_price not in text, shop_price
