"""The ban-list history can be read (#17): the daily job records a change of legality when it sees one, and now a card
lookup (REST and the get_card_oracle tool) and a legality check (REST and the deck_legality tool) hand the recorded
changes to the assistant, so a later answer can say what changed and when, and say what the record does not cover."""

from datetime import date

import pytest

from tests.test_agents import call_tool, make_token
from tests.test_catalog_api import BOLT, card as bolt_card
from tests.test_deck_api import CARDS, oid
from tests.test_mcp_catalog import bot  # noqa: F401  (an agent's HTTP client)
from vault import catalog_sync as cs
from vault.models import LegalityChange

V1 = "/api/v1"
RIDER = oid(40)


def banned_thing(legal):
    return {"object": "card", "id": "p-rider", "oracle_id": RIDER, "name": "Test Rider", "layout": "normal", "mana_cost": "{1}", "cmc": 1.0,
            "type_line": "Creature — Human", "oracle_text": "", "colors": [], "color_identity": [], "keywords": [],
            "legalities": {"commander": legal, "modern": "legal"}, "edhrec_rank": 5, "digital": False}


@pytest.fixture
def history(signed_in, app):
    """Three daily loads: Test Rider is legal, then banned in Commander (day 2), then legal again (day 3); the Bolt is untouched."""
    with app.state.db.sessions() as db:
        base = CARDS + [bolt_card(BOLT, "Lightning Bolt", "Lightning Bolt deals 3 damage to any target.")]
        for day, legal in ((1, "legal"), (2, "banned"), (3, "legal")):
            cs.sync_oracle_cards(db, base + [banned_thing(legal)], today=date(2026, 9 + (day > 3), day))
        cs.record_source(db, "oracle_cards", version="oracle-cards-3", rows=1)
        cs.record_source(db, "oracle_tags", version="t", rows=1)
        db.commit()
    return signed_in


def test_a_card_lookup_lists_the_recorded_changes_newest_first_with_what_they_mean(history):
    body = history.get(f"{V1}/catalog/cards", params={"name": "Test Rider"}).json()
    assert [(c["format"], c["old"], c["new"], c["observed_on"]) for c in body["legality_changes"]] == [
        ("commander", "banned", "legal", "2026-09-03"), ("commander", "legal", "banned", "2026-09-02")]
    assert "the day the Vault saw the change" in body["legality_changes_note"]
    assert "before the Vault first loaded" in body["legality_changes_note"]
    computed = [b for b in body["provenance"] if b["kind"] == "computed"]
    assert computed and "legality change log" in computed[0]["origin"] and computed[0]["inputs"][0]["source"] == "Scryfall"


def test_a_card_that_never_changed_has_an_empty_history_not_a_missing_field(history):
    body = history.get(f"{V1}/catalog/cards", params={"name": "Lightning Bolt"}).json()
    assert body["legality_changes"] == [] and body["legality_changes_note"]


def test_a_legality_check_lists_the_changes_of_the_cards_in_that_format_only(history):
    deck = "Commander\n1 Test Commander\n\nDeck\n1 Test Rider\n1 Lightning Bolt\n"
    result = history.post(f"{V1}/decks/legality", json={"text": deck, "format": "commander"}).json()["result"]
    assert [(c["card"], c["old"], c["new"]) for c in result["changes"]] == [("Test Rider", "banned", "legal"), ("Test Rider", "legal", "banned")]
    assert "observed_on" in result["changes"][0] and result["changes_note"]
    modern = history.post(f"{V1}/decks/legality", json={"text": deck, "format": "modern"}).json()["result"]
    assert modern["changes"] == []  # nothing changed in Modern


def test_the_tools_carry_the_same_history(history, app, bot):  # noqa: F811
    token = make_token(history)
    card = call_tool(bot, token, "get_card_oracle", name="Test Rider")["structuredContent"]
    assert [c["new"] for c in card["legality_changes"]] == ["legal", "banned"]
    legal = call_tool(bot, token, "deck_legality", text="Deck\n1 Test Rider\n", format="commander")["structuredContent"]
    assert [c["card"] for c in legal["result"]["changes"]] == ["Test Rider", "Test Rider"]


def test_the_stored_log_is_what_the_endpoints_read(history, app):
    with app.state.db.sessions() as db:
        assert db.query(LegalityChange).count() == 2  # one ban, one unban: the Bolt and the untouched formats leave no rows


def test_the_tool_descriptions_name_the_history_without_steering():
    from vault.api import mcp

    tools = {t.name: t.description for t in mcp.TOOLS}
    assert "legality_changes" in tools["get_card_oracle"] and "`changes`" in tools["deck_legality"]
    for name in ("get_card_oracle", "deck_legality"):
        assert not any(w in tools[name].lower() for w in ("always", "never ask", "no need to confirm", "do not ask")), name
