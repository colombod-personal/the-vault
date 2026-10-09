"""#353: routes that call Scryfall or Archidekt, or parse a big file, have a per-person limit a minute; Archidekt reads share one
interval for the whole process; a person keeps at most max_decks saved decks."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from twins.universe import Universe
from vault.app import create_app
from vault.config import Settings

V1 = "/api/v1"
CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def make(database_url, universe):
    clients = []

    def build(**limits):
        settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True,
                            archidekt_interval=limits.pop("archidekt_interval", 0), **limits)
        client = TestClient(create_app(settings, serve_static=False, transport=universe.transport))
        client.__enter__()
        clients.append(client)
        assert client.post("/api/auth/dev-login").status_code == 200
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def deck(universe, n=1):
    return [universe.archidekt.add_deck(f"Deck {i}", "ann", [(1, "Sol Ring"), (1, "Llanowar Elves")])["id"] for i in range(n)]


def test_previews_and_imports_share_a_per_person_limit(make):
    client = make(import_limit=3)
    files = {"file": ("export.csv", CSV, "text/csv")}
    assert [client.post(f"{V1}/imports/preview", files=files).status_code for _ in range(2)] == [200, 200]
    assert client.post(f"{V1}/imports", files=files).status_code in (200, 201)  # the third of the shared allowance
    over = client.post(f"{V1}/imports/preview", files=files)
    assert over.status_code == 429 and over.headers["retry-after"]
    assert client.post(f"{V1}/imports", files=files).status_code == 429
    other = TestClient(client.app)
    other.post("/api/auth/dev-login", params={"email": "bob@example.com"})
    assert other.post(f"{V1}/imports/preview", files=files).status_code == 200  # someone else has their own


def test_a_lookup_with_refresh_is_limited_and_one_without_is_not(make):
    client = make(lookup_refresh_limit=2)
    body = {"identifiers": [{"set": "c21", "collector_number": "263"}]}
    codes = [client.post(f"{V1}/cards/lookup", json={**body, "refresh": True}).status_code for _ in range(3)]
    assert 429 not in codes[:2] and codes[2] == 429
    assert client.post(f"{V1}/cards/lookup", json=body).status_code != 429  # the Vault's own copy: free


def test_archidekt_reads_import_link_and_refresh_share_a_per_person_limit(make, universe):
    ids = deck(universe, 4)
    client = make(archidekt_limit=3)
    assert client.get(f"{V1}/archidekt/decks/{ids[0]}").status_code == 200
    saved = client.post(f"{V1}/decks/import-link", json={"url": f"https://archidekt.com/decks/{ids[1]}"})
    assert saved.status_code == 201
    assert client.post(f"{V1}/decks/{saved.json()['deck']['id']}/refresh", json={}).status_code == 200
    for call in (lambda: client.get(f"{V1}/archidekt/decks/{ids[2]}"),
                 lambda: client.post(f"{V1}/decks/import-link", json={"url": f"https://archidekt.com/decks/{ids[3]}"}),
                 lambda: client.post(f"{V1}/decks/{saved.json()['deck']['id']}/refresh", json={})):
        res = call()
        assert res.status_code == 429 and res.headers["retry-after"]


def test_reads_of_different_decks_are_spaced_by_the_shared_interval(make, universe):
    ids = deck(universe, 6)
    client = make(archidekt_interval=0.25, archidekt_limit=100)
    started = time.monotonic()
    for deck_id in ids:
        assert client.get(f"{V1}/archidekt/decks/{deck_id}").status_code == 200
    assert time.monotonic() - started >= 5 * 0.25  # six reads, five gaps
    started = time.monotonic()
    for deck_id in ids:  # cached copies make no call to Archidekt, so they wait for no one
        assert client.get(f"{V1}/archidekt/decks/{deck_id}").status_code == 200
    assert time.monotonic() - started < 5 * 0.25


def test_a_person_keeps_at_most_max_decks_and_the_message_says_so(make, universe):
    ids = deck(universe, 2)
    client = make(max_decks=3)
    for n in range(3):
        assert client.post(f"{V1}/decks", json={"name": f"Deck {n}", "text": "1 Sol Ring"}).status_code == 201
    full = client.post(f"{V1}/decks", json={"name": "One more", "text": "1 Sol Ring"})
    assert full.status_code == 409 and "3 saved decks" in full.json()["detail"]
    link = client.post(f"{V1}/decks/import-link", json={"url": f"https://archidekt.com/decks/{ids[0]}"})
    assert link.status_code == 409 and "Delete one first" in link.json()["detail"]
    first = client.get(f"{V1}/decks").json()["items"][0]["id"]
    assert client.delete(f"{V1}/decks/{first}").status_code in (200, 204)
    assert client.post(f"{V1}/decks", json={"name": "Room again", "text": "1 Sol Ring"}).status_code == 201
