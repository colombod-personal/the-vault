"""Saving a public Archidekt deck from its link (vault/deck_import.py, POST /api/v1/decks/import-link, #96 step 3): the
server converts it with its sections, the same link never duplicates, and only Archidekt links are fetched."""

import pytest
from fastapi.testclient import TestClient

from twins.universe import Universe
from vault import deck_import
from vault.app import create_app
from vault.config import Settings

V1 = "/api/v1"
RAW = {"name": "Elves", "owner": {"username": "ann"}, "cards": [
    {"quantity": 1, "categories": ["Commander"], "card": {"oracleCard": {"name": "Sliver Overlord"}}},
    {"quantity": 1, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Sol Ring"}}},
    {"quantity": 2, "categories": ["Creature"], "card": {"oracleCard": {"name": "Llanowar Elves"}}},
    {"quantity": 1, "categories": ["Creature"], "card": {"oracleCard": {"name": "Llanowar Elves"}}},  # a second printing
    {"quantity": 1, "categories": ["Sideboard"], "card": {"oracleCard": {"name": "Pearl Medallion"}}},
    {"quantity": 1, "categories": ["Maybeboard"], "card": {"oracleCard": {"name": "Heart Sliver"}}},
    {"quantity": 1, "categories": ["Considering"], "card": {"oracleCard": {"name": "Blur Sliver"}}},
    {"quantity": 1, "categories": [], "card": {"oracleCard": {"name": ""}}},
]}


def test_the_deck_is_written_out_with_its_sections_and_one_line_per_card():
    out = deck_import.to_decklist(RAW)
    assert out["name"] == "Elves" and out["author"] == "ann"
    assert out["text"] == ("Commander\n1 Sliver Overlord\nDeck\n1 Sol Ring\n3 Llanowar Elves\nSideboard\n1 Pearl Medallion\n"
                           "Maybeboard\n1 Heart Sliver\n1 Blur Sliver")
    assert out["counts"] == {"Commander": 1, "Deck": 4, "Sideboard": 1, "Maybeboard": 2}


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def archidekt_app(database_url, universe):
    deck = universe.archidekt.add_deck("Elves", "ann", [(1, "Llanowar Elves", None, None, "Ramp"), (1, "Sol Ring"),
                                                        (1, "Sliver Overlord", None, None, "Commander")])
    settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True)
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as client:
        client.post("/api/auth/dev-login")
        yield client, app, deck, universe


def age_cache(app, seconds=120):
    """Make the cached Archidekt copy older than a refresh's one-minute floor."""
    from datetime import timedelta
    from sqlalchemy import update
    from vault.models import ArchidektDeckCache
    with app.state.db.sessions() as db:
        db.execute(update(ArchidektDeckCache).values(fetched_at=ArchidektDeckCache.fetched_at - timedelta(seconds=seconds)))
        db.commit()


def test_the_same_link_saves_once_and_updating_is_asked_for(archidekt_app):
    client, app, deck, universe = archidekt_app
    link = f"https://archidekt.com/decks/{deck['id']}/elves_for_all"
    first = client.post(f"{V1}/decks/import-link", json={"url": link})
    assert first.status_code == 201 and first.json()["created"] is True
    saved = first.json()["deck"]
    assert saved["name"] == "Elves" and saved["source_url"] == f"https://archidekt.com/decks/{deck['id']}" and saved["source_author"] == "ann"
    assert saved["text"].startswith("Commander\n1 Sliver Overlord\nDeck\n")
    again = client.post(f"{V1}/decks/import-link", json={"url": f"archidekt.com/decks/{deck['id']}"}).json()  # another spelling of the link
    assert again["created"] is False and again["updated"] is False and again["deck"]["id"] == saved["id"] and "already saved" in again["note"]
    assert len(client.get(f"{V1}/decks").json()["items"]) == 1
    universe.archidekt.add_deck("Elves", "ann", [(1, "Sol Ring")], deck_id=deck["id"])  # the deck changed on Archidekt
    age_cache(app)
    preview = client.post(f"{V1}/decks/import-link", json={"url": link, "update": True, "name": "Elves, tuned"}).json()
    assert preview["updated"] is False and preview["changes"] and preview["fingerprint"]  # an update shows what changes first
    refreshed = client.post(f"{V1}/decks/import-link", json={"url": link, "update": True, "confirm": True, "name": "Elves, tuned",
                                                             "fingerprint": preview["fingerprint"]}).json()
    assert refreshed["updated"] is True and refreshed["deck"]["id"] == saved["id"] and refreshed["deck"]["name"] == "Elves, tuned"
    assert len(client.get(f"{V1}/decks").json()["items"]) == 1


def test_only_archidekt_links_are_fetched_and_a_missing_deck_is_a_404(archidekt_app):
    client, *_ = archidekt_app
    moxfield = client.post(f"{V1}/decks/import-link", json={"url": "https://moxfield.com/decks/abcDEF"})
    assert moxfield.status_code == 400 and "Archidekt" in moxfield.json()["detail"]
    assert client.post(f"{V1}/decks/import-link", json={"url": "https://example.com/decks/12345678"}).status_code == 400
    assert client.post(f"{V1}/decks/import-link", json={"url": "https://archidekt.com/decks/99999999"}).status_code == 404


def test_importing_is_a_write_and_each_person_gets_their_own_copy(archidekt_app):
    client, app, deck, _ = archidekt_app
    from test_agents import make_token

    read = make_token(client)  # a read-only token
    with TestClient(app) as bot:
        res = bot.post(f"{V1}/decks/import-link", json={"url": f"https://archidekt.com/decks/{deck['id']}"},
                       headers={"Authorization": f"Bearer {read['token']}"})
        assert res.status_code == 403
    client.post(f"{V1}/decks/import-link", json={"url": f"https://archidekt.com/decks/{deck['id']}"})
    with TestClient(app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        assert bob.get(f"{V1}/decks").json()["items"] == []
        assert bob.post(f"{V1}/decks/import-link", json={"url": f"https://archidekt.com/decks/{deck['id']}"}).json()["created"] is True


def test_refresh_shows_the_changes_and_replaces_only_what_was_previewed(archidekt_app):
    client, app, deck, universe = archidekt_app
    saved = client.post(f"{V1}/decks/import-link", json={"url": f"https://archidekt.com/decks/{deck['id']}"}).json()["deck"]
    path = f"{V1}/decks/{saved['id']}/refresh"
    same = client.post(path, json={}).json()
    assert same["unchanged"] is True and same["changes"] == []
    universe.archidekt.add_deck("Elves", "ann", [(2, "Sol Ring"), (1, "Elvish Mystic"),
                                                 (1, "Sliver Overlord", None, None, "Commander")], deck_id=deck["id"])
    age_cache(app)
    preview = client.post(path, json={}).json()
    by_card = {c["card"]: c for c in preview["changes"]}
    assert by_card["Sol Ring"] == {"section": "Deck", "card": "Sol Ring", "before": 1, "after": 2}
    assert by_card["Llanowar Elves"]["after"] == 0 and by_card["Elvish Mystic"]["before"] == 0
    assert preview["summary"]["added"] == 1 and preview["summary"]["removed"] == 1 and "Nothing has changed" in preview["note"]
    assert client.get(f"{V1}/decks/{saved['id']}").json()["text"] == saved["text"]  # the preview changed nothing
    assert client.post(path, json={"confirm": True}).status_code == 409  # no fingerprint: refused
    assert client.post(path, json={"confirm": True, "fingerprint": "not-the-one"}).status_code == 409
    done = client.post(path, json={"confirm": True, "fingerprint": preview["fingerprint"]}).json()
    assert done["refreshed"] is True and done["summary"] == preview["summary"]
    text = client.get(f"{V1}/decks/{saved['id']}").json()["text"]
    assert "2 Sol Ring" in text and "Llanowar Elves" not in text
    assert client.get(f"{V1}/decks/{saved['id']}").json()["source_author"] == "ann"  # the credit stays


def test_refresh_needs_an_archidekt_link(archidekt_app):
    client, *_ = archidekt_app
    pasted = client.post(f"{V1}/decks", json={"name": "Pasted", "text": "1 Sol Ring"}).json()
    assert "no stored link" in client.post(f"{V1}/decks/{pasted['id']}/refresh", json={}).json()["detail"]
    mox = client.post(f"{V1}/decks", json={"name": "Mox", "text": "1 Sol Ring", "source_url": "https://moxfield.com/decks/abc"}).json()
    res = client.post(f"{V1}/decks/{mox['id']}/refresh", json={})
    assert res.status_code == 400 and "Moxfield" in res.json()["detail"]
