"""The printing picker finds printings the Vault does not hold, live at Scryfall (#208): owned first, paged, nothing stored
until one is picked and applied, one Scryfall request per question, a clear note when Scryfall is down, and no image bytes
through the Vault."""

import pytest
from fastapi.testclient import TestClient

from twins import Universe
from vault import catalog_sync as cs
from vault.app import create_app
from vault.config import Settings
from vault.models import Card, Entry, User

V1 = "/api/v1"
NAME = "Sol Ring"


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def twin_app(database_url, universe):
    """The app wired to the twin Scryfall (using this fixture also switches the test-suite's 'no live Scryfall' stub off)."""
    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver")
    app = create_app(settings, serve_static=False, transport=universe.transport)
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def client(twin_app):
    with TestClient(twin_app) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        yield c


def seed(twin_app, universe, extra=0):
    """Sol Ring: the twin has CMR, C21 and ``extra`` more printings; the Vault holds only C21, and the person owns that one."""
    printings = [universe.scryfall.add_card(NAME, "c21", "263", finishes=("nonfoil", "foil"), released_at="2021-04-23"),
                 universe.scryfall.add_card(NAME, "cmr", "472", released_at="2020-11-20")]
    printings += [universe.scryfall.add_card(NAME, f"x{n:02d}", str(n), released_at=f"2022-{1 + n % 12:02d}-01") for n in range(extra)]
    oracle_id = printings[0]["oracle_id"]
    with twin_app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, [{"object": "card", "id": "p-1", "oracle_id": oracle_id, "name": NAME, "layout": "normal",
                                   "mana_cost": "{1}", "cmc": 1.0, "type_line": "Artifact", "oracle_text": "", "colors": [],
                                   "color_identity": [], "keywords": [], "legalities": {"commander": "legal"}, "digital": False}])
        c21 = printings[0]
        db.add(Card(scryfall_id=c21["id"], oracle_id=oracle_id, name=NAME, set_code="c21", set_name="Commander 2021",
                    collector_number="263", finishes=["nonfoil", "foil"], artist="Twin Artist",
                    image_normal=c21["image_uris"]["normal"], image_small=c21["image_uris"]["small"],
                    scryfall_uri=c21["scryfall_uri"]))
        user = db.query(User).first()
        db.add(Entry(user_id=user.id, name=NAME, set_code="c21", collector_number="263", scryfall_id=c21["id"], quantity=1,
                     finish="nonfoil"))
        db.commit()
    universe.scryfall.calls.clear()
    return printings


def token(client):
    return client.post(f"{V1}/me/tokens", json={"name": "agent", "scopes": ["read", "write"]}).json()["token"]


def ask(client, **line):
    body = {"lines": [{"action": "add", "name": NAME, "quantity": 1, **line}]}
    res = client.post(f"{V1}/collection/changes/preview", json=body)
    assert res.status_code == 200, res.text
    return res.json()["lines"][0]


def card_count(twin_app):
    with twin_app.state.db.sessions() as db:
        return db.query(Card).count()


def searches(universe):
    return [c for c in universe.scryfall.calls if c.path == "/cards/search"]


def test_printings_the_vault_does_not_hold_are_offered_owned_first_and_nothing_is_stored(client, twin_app, universe):
    seed(twin_app, universe)
    before = card_count(twin_app)
    line = ask(client)
    assert line["status"] == "choose_printing"
    sets = [c["set"] for c in line["choose_from"]]
    assert sets[0] == "c21" and line["choose_from"][0]["owned"] == 1  # owned first
    assert "cmr" in sets  # the Vault never held CMR: it came from Scryfall
    assert line["printings"]["source"] == "Scryfall (live)" and line["printings"]["scryfall_unavailable"] is False
    assert card_count(twin_app) == before  # nothing new stored by a preview
    assert all(c["image"]["url"].startswith("https://cards.scryfall.io/") for c in line["choose_from"] if c.get("image"))


def test_one_scryfall_request_per_question_and_asking_again_within_minutes_makes_none(client, twin_app, universe):
    seed(twin_app, universe)
    ask(client)
    (call,) = searches(universe)
    assert call.query["unique"] == "prints" and call.query["q"].startswith("oracleid:")
    ask(client)
    ask(client, printings_page=1)
    assert len(searches(universe)) == 1  # kept in memory, not asked again
    assert not [c for c in universe.scryfall.calls if c.host == "cards.scryfall.io"]  # no image was fetched by the Vault


def test_the_printings_are_paged_and_the_next_page_is_asked_for_with_printings_page(client, twin_app, universe):
    seed(twin_app, universe, extra=30)  # 32 printings, 1 owned: 31 to choose from, 20 a page
    first = ask(client)
    assert first["printings"]["not_owned"] == 31 and first["printings"]["pages"] == 2 and first["printings"]["page"] == 1
    assert len(first["choose_from"]) == 1 + 20 and "printings_page 2" in first["printings"]["next"]
    second = ask(client, printings_page=2)
    assert second["printings"]["page"] == 2 and len(second["choose_from"]) == 1 + 11 and second["printings"]["next"] is None
    keys = [(c["set"], c["number"]) for c in first["choose_from"] + second["choose_from"]]
    assert len(keys) == len(set(keys)) + 1  # the owned printing is listed on both pages and no other repeats
    assert len(searches(universe)) == 1


def test_when_scryfall_is_down_the_answer_says_so_and_still_offers_what_the_vault_holds(client, twin_app, universe):
    seed(twin_app, universe)
    with twin_app.state.db.sessions() as db:
        db.add(Card(scryfall_id="sol-held", oracle_id=db.query(Card).first().oracle_id, name=NAME, set_code="cmm",
                    set_name="Commander Masters", collector_number="400"))
        db.commit()
    universe.scryfall.outage = True
    line = ask(client)
    assert line["status"] == "choose_printing"
    assert line["printings"]["scryfall_unavailable"] is True and "did not answer" in line["printings"]["source"]
    assert [c["set"] for c in line["choose_from"]] == ["c21", "cmm"]  # owned, then the one the Vault already holds


def test_picking_a_live_printing_stores_just_that_one_when_it_is_applied(client, twin_app, universe):
    seed(twin_app, universe)
    base = card_count(twin_app)
    line = {"action": "add", "name": NAME, "quantity": 1, "set": "cmr", "number": "472", "finish": "nonfoil"}
    preview = client.post(f"{V1}/collection/changes/preview", json={"lines": [line]}).json()
    assert preview["ready"] is True and card_count(twin_app) == base + 1  # a lookup by set and number stores that printing only
    applied = client.post(f"{V1}/collection/changes/apply", json={"lines": [line], "confirmation": preview["confirmation"]})
    assert applied.status_code == 201, applied.text
    with twin_app.state.db.sessions() as db:
        assert db.query(Card).count() == base + 1
        assert sum(e.quantity for e in db.query(Entry).filter(Entry.set_code == "cmr")) == 1


def test_an_image_that_is_not_on_scryfalls_server_is_never_offered(client, twin_app, universe):
    printings = seed(twin_app, universe)
    printings[1]["image_uris"] = {"small": "https://evil.example/s.jpg", "normal": "https://evil.example/n.jpg"}
    line = ask(client)
    cmr = next(c for c in line["choose_from"] if c["set"] == "cmr")
    assert cmr["image"] is None


def test_no_route_serves_or_fetches_card_images(twin_app):
    paths = {getattr(r, "path", "") for r in twin_app.routes}
    assert not [p for p in paths if "image" in p.lower() or "/img" in p.lower()], paths
