"""Combos on demand from Commander Spellbook, through its twin: attributed to them and linked, one call
per question, nothing stored, and a failing upstream is a clear 502 that never breaks anything else."""

import pytest
from fastapi.testclient import TestClient

from tests.test_deck_api import CARDS
from twins import Universe
from vault import catalog_sync as cs
from vault.app import create_app
from vault.config import Settings

V1 = "/api/v1/decks"
DECK = "Commander\n1 Test Commander\n\nDeck\n1 Thassa's Oracle\n1 Test Rock\n"


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def client(database_url, universe):
    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver")
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        extra = [dict(CARDS[0], oracle_id=f"9000000{i}-0000-0000-0000-000000000000", name=n)
                 for i, n in enumerate(["Thassa's Oracle", "Demonic Consultation"])]
        with app.state.db.sessions() as db:
            cs.sync_oracle_cards(db, CARDS + extra)
            cs.record_source(db, "oracle_cards", version="v", rows=1)
            db.commit()
        yield c
    app.state.db.engine.dispose()


def test_combos_in_the_deck_and_one_card_short_are_attributed_to_commander_spellbook(client, universe):
    deck = "Deck\n1 Thassa's Oracle\n1 Demonic Consultation\n1 Test Rock\n"
    r = client.post(f"{V1}/combos", json={"text": deck}).json()
    combo = r["result"]["included"][0]
    assert combo["cards"] == ["Thassa's Oracle", "Demonic Consultation"] or combo["cards"] == ["Demonic Consultation", "Thassa's Oracle"]
    assert combo["missing"] == []
    assert combo["url"].startswith("https://commanderspellbook.com/combo/") and "Win the game" in combo["produces"]
    assert combo["description"].startswith("Cast Demonic Consultation")
    blocks = r["provenance"]
    assert blocks[0]["kind"] == "source" and blocks[0]["source"] == "Commander Spellbook" and blocks[0]["notice"]
    assert blocks[1]["kind"] == "computed" and blocks[1]["inputs"][0]["source"] == "Commander Spellbook"
    short = client.post(f"{V1}/combos", json={"text": "Deck\n1 Thassa's Oracle\n1 Test Rock\n"}).json()["result"]
    assert short["included"] == [] and short["almost_included"][0]["missing"] == ["Demonic Consultation"]
    assert any("community" in n for n in short["notes"])


def test_one_call_goes_out_per_question_and_the_deck_is_not_stored(client, universe):
    client.post(f"{V1}/combos", json={"text": DECK})
    calls = [c for c in universe.spellbook.calls if c.path == "/find-my-combos"]
    assert len(calls) == 1 and calls[0].headers["user-agent"].startswith("the-vault/")
    assert client.get("/api/v1/decks").json()["total"] == 0


def test_a_failing_upstream_is_a_502_and_the_rest_still_works(client, universe):
    universe.spellbook.fail_next("/find-my-combos", 500)
    res = client.post(f"{V1}/combos", json={"text": DECK})
    assert res.status_code == 502 and "Commander Spellbook" in res.json()["detail"]
    assert client.post(f"{V1}/stats", json={"text": DECK}).status_code == 200
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 200


def test_a_malformed_answer_is_a_502_not_a_crash(client, universe):
    universe.spellbook.fail_next("/find-my-combos", 200, body={"unexpected": True})
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 502


def test_the_answer_says_it_lists_only_combos_commander_spellbook_knows(client, universe):
    """A real council run (2026-10-05) told a pod a deck had 'no infinite combos' because this found none, while the deck held a
    token engine Spellbook does not list (#172)."""
    result = client.post(f"{V1}/combos", json={"text": "Deck\n1 Test Rock\n"}).json()["result"]
    assert result["included"] == [] and "Only combos known to Commander Spellbook" in result["limits"]
    assert "never tell a player it is combo-free" in result["limits"]
    from vault.api import mcp
    assert "finding none does not mean the deck has no infinite combos" in mcp.BY_NAME["find_combos"].description
