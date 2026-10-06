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


def test_the_same_link_saves_once_and_updating_is_asked_for(archidekt_app):
    client, app, deck, universe = archidekt_app
    link = f"https://archidekt.com/decks/{deck['id']}/elves_for_all"
    first = client.post(f"{V1}/decks/import-link", json={"url": link})
    assert first.status_code == 201 and first.json()["created"] is True
    saved = first.json()["deck"]
    assert saved["name"] == "Elves" and saved["source"] == "archidekt" and saved["source_author"] == "ann"
    assert saved["text"].startswith("Commander\n1 Sliver Overlord\nDeck\n")
    again = client.post(f"{V1}/decks/import-link", json={"url": f"archidekt.com/decks/{deck['id']}"}).json()  # another spelling of the link
    assert again["created"] is False and again["updated"] is False and again["deck"]["id"] == saved["id"] and "already saved" in again["note"]
    assert len(client.get(f"{V1}/decks").json()["items"]) == 1
    universe.archidekt.add_deck("Elves", "ann", [(1, "Sol Ring")], deck_id=deck["id"])  # the deck changed on Archidekt
    refreshed = client.post(f"{V1}/decks/import-link", json={"url": link, "update": True, "name": "Elves, tuned"}).json()
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
