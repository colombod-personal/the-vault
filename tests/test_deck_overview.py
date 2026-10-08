"""Deck answers lead with what the deck is (vault/deck_overview.py, #216): format, commander(s), card count and colour
identity, with the AI tools' deck list leaving the card text out."""

import pytest

from test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from vault import catalog_sync as cs
from vault import deck_overview

SLIVERS = """Commander
1 Sliver Overlord
1 The First Sliver

Deck
1 Sol Ring
97 Forest

Sideboard
1 Swords to Plowshares

Maybeboard
2 Opt"""


def oracle(oracle_id, name, identity, type_line="Legendary Creature"):
    return {"object": "card", "id": "p-" + oracle_id[:4], "oracle_id": oracle_id, "name": name, "layout": "normal",
            "mana_cost": "", "cmc": 0.0, "type_line": type_line, "oracle_text": "", "colors": identity,
            "color_identity": identity, "keywords": [], "legalities": {"commander": "legal"}, "digital": False}


@pytest.fixture
def catalog(app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, [oracle("55555555-5555-5555-5555-555555555555", "Sliver Overlord", ["U", "B"]),
                                  oracle("66666666-6666-6666-6666-666666666666", "The First Sliver", ["W", "U", "B", "R", "G"]),
                                  oracle("77777777-7777-7777-7777-777777777777", "Sol Ring", [], "Artifact"),
                                  oracle("88888888-8888-8888-8888-888888888888", "Forest", ["G"], "Basic Land — Forest")])
        for source in ("oracle_cards", "oracle_tags", "oracle_prices"):  # the analysis tools refuse to run on an unloaded catalog
            cs.record_source(db, source, version=source + "-1", rows=2)
        db.commit()


def test_commanders_and_counts_are_read_from_the_list():
    seen = deck_overview.overview(SLIVERS, None)
    assert seen["commanders"] == ["Sliver Overlord", "The First Sliver"] and seen["cards"] == 100
    assert seen["format"] == "commander" and seen["format_from"] == "the list names a commander"
    assert seen["sideboard"] == 1 and seen["maybeboard"] == 2


@pytest.mark.parametrize("text", ["1 Sliver Overlord *CMDR*\n1 Sol Ring",  # Moxfield's marker
                                  "1x Sliver Overlord (scg) 34 [Commander{top}]\n1x Sol Ring (c21) 263 [Ramp]"])  # Archidekt's category
def test_other_ways_of_naming_the_commander(text):
    assert deck_overview.overview(text, None)["commanders"] == ["Sliver Overlord"]


@pytest.mark.parametrize("line", ["1 Atraxa, Praetors' Voice (CMM) 1068 *CMDR*", "1 Atraxa, Praetors' Voice (CMM) *CMDR*",
                                  "1 Atraxa, Praetors' Voice (CMM) 1068 *F* *CMDR*", "1 Atraxa, Praetors' Voice *CMDR*"])
def test_a_moxfield_commander_line_keeps_no_set_code_or_number(line):  # #332
    seen = deck_overview.overview(line + "\n1 Sol Ring (C21) 263\n1 Island", None)
    assert seen["commanders"] == ["Atraxa, Praetors' Voice"] and seen["cards"] == 3


def test_a_stored_format_wins_and_a_list_without_commander_has_no_guess():
    assert deck_overview.overview(SLIVERS, "oathbreaker")["format"] == "oathbreaker"
    plain = deck_overview.overview("4 Lightning Bolt\n20 Mountain", None)
    assert plain["format"] is None and plain["format_from"] is None and plain["commanders"] == [] and plain["cards"] == 24
    assert deck_overview.overview("4 Lightning Bolt", "modern")["format_from"] == "set on the deck"


def test_colour_identity_needs_every_commander_known():
    known = {"sliver overlord": ["U", "B"], "the first sliver": ["W", "U", "B", "R", "G"]}
    assert deck_overview.overview(SLIVERS, None, known)["color_identity"] == "WUBRG"
    assert deck_overview.overview("Commander\n1 Sliver Overlord", None, known)["color_identity"] == "UB"
    assert deck_overview.overview(SLIVERS, None, {"sliver overlord": ["U", "B"]})["color_identity"] is None


def test_deck_answers_carry_the_overview_and_the_ai_list_leaves_the_text_out(agent, bot, catalog):
    write = make_token(agent, scopes=["read", "write"])
    saved = call_tool(bot, write, "save_deck", name="Sliver Swarm", text=SLIVERS)["structuredContent"]
    plain = call_tool(bot, write, "save_deck", name="Burn", text="4 Lightning Bolt\n20 Mountain", format="modern")["structuredContent"]
    listed = call_tool(bot, write, "list_decks")["structuredContent"]["items"]
    by_name = {d["name"]: d for d in listed}
    assert by_name["Sliver Swarm"]["text"] is None  # name, format and commanders first; card lines are get_deck's
    assert by_name["Sliver Swarm"]["overview"]["commanders"] == ["Sliver Overlord", "The First Sliver"]
    assert by_name["Sliver Swarm"]["overview"]["color_identity"] == "WUBRG"
    assert by_name["Burn"]["overview"]["format"] == "modern" and by_name["Burn"]["format"] == "modern"
    assert {b["source"] for b in call_tool(bot, write, "list_decks")["structuredContent"]["provenance"]} >= {"Scryfall"}
    one = call_tool(bot, write, "get_deck", deck_id=saved["id"])["structuredContent"]
    assert one["text"] == SLIVERS and one["overview"]["cards"] == 100
    assert agent.get(f"{V1}/decks").json()["items"][0]["text"]  # the web app's list is unchanged: it keeps the text
    assert plain["format"] == "modern"


def test_the_format_can_be_set_kept_and_cleared(agent):
    deck = agent.post(f"{V1}/decks", json={"name": "Slivers", "text": SLIVERS}).json()
    assert deck["format"] is None and deck["overview"]["format"] == "commander"
    path = f"{V1}/decks/{deck['id']}"
    assert agent.put(path, json={"name": "Slivers", "text": SLIVERS, "format": "predh"}).json()["overview"]["format"] == "predh"
    kept = agent.put(path, json={"name": "Slivers v2", "text": SLIVERS}).json()  # omitted: kept
    assert kept["format"] == "predh"
    cleared = agent.put(path, json={"name": "Slivers v2", "text": SLIVERS, "format": None}).json()
    assert cleared["format"] is None and cleared["overview"]["format_from"] == "the list names a commander"
    assert agent.put(path, json={"name": "x", "text": SLIVERS, "format": "not-a-format"}).status_code == 422


def test_the_lists_shape_gives_a_reading_for_brawl_and_limited():
    brawl = deck_overview.overview("Commander\n1 Sliver Overlord\n\nDeck\n59 Island", None)
    assert brawl["format"] == "brawl" and "Brawl" in brawl["format_from"]
    limited = deck_overview.overview("23 Grizzly Bears\n17 Forest", None)
    assert limited["format"] == "limited" and "Limited" in limited["format_from"]
    assert deck_overview.overview("4 Lightning Bolt\n56 Mountain", None)["format"] is None  # 60 cards fit many formats


def test_archidekt_commander_decks_store_their_format():
    from vault import deck_import
    raw = {"name": "Slivers", "deckFormat": 3, "owner": {"username": "ann"},
           "cards": [{"quantity": 1, "categories": ["Commander"], "card": {"oracleCard": {"name": "Sliver Overlord"}}},
                     {"quantity": 1, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Sol Ring"}}}]}
    assert deck_import.to_decklist(raw)["format"] == "commander"
    assert deck_import.to_decklist({**raw, "deckFormat": 99})["format"] is None  # unchecked numbers are not guessed


def test_archidekt_credit_requires_a_valid_domain_boundary():
    from types import SimpleNamespace

    assert deck_overview.archidekt_credit(
        SimpleNamespace(source_url="https://www.archidekt.com/decks/1", source_fetched_at=None, source_author=None)
    )["source"] == "Archidekt"
    assert deck_overview.archidekt_credit(
        SimpleNamespace(source_url="https://notarchidekt.com/decks/1", source_fetched_at=None, source_author=None)
    ) is None


ANALYSIS = {  # tool -> the arguments that make it run on a saved deck
    "deck_stats": {}, "simulate_draws": {"format": "commander", "samples": 1}, "deck_legality": {"format": "commander"},
    "find_upgrades": {"format": "commander", "budget_usd": 50}, "validate_deck_changes": {"format": "commander", "adds": [], "cuts": []},
    "shopping_list": {},
}


def test_every_analysis_answer_names_the_deck_its_format_and_its_commanders(agent, bot, catalog):
    """#216: deck_stats and the rest used to answer 'Deck: 100 cards' with no name, format or commander."""
    write = make_token(agent, scopes=["read", "write"])
    saved = call_tool(bot, write, "save_deck", name="Sliver Swarm", text=SLIVERS)["structuredContent"]
    for tool, args in ANALYSIS.items():
        out = call_tool(bot, write, tool, deck_id=saved["id"], **args)
        body = out.get("structuredContent") or {}
        deck = body.get("deck")
        assert deck, (tool, out["content"][0]["text"][:300])
        assert deck["id"] == saved["id"] and deck["name"] == "Sliver Swarm", tool
        assert deck["overview"]["format"] == "commander" and deck["overview"]["commanders"] == ["Sliver Overlord", "The First Sliver"], tool
        assert deck["overview"]["cards"] == 100 and deck["overview"]["color_identity"] == "WUBRG", tool
        assert list(body)[0] == "deck", tool  # first in the answer, so it is read first
    pasted = call_tool(bot, write, "deck_stats", text="4 Lightning Bolt\n20 Mountain")["structuredContent"]["deck"]
    assert pasted["name"] is None and pasted["id"] is None and pasted["overview"]["cards"] == 24  # a pasted list has no name


def test_every_deck_tool_description_says_to_lead_with_the_deck(agent, bot):
    from vault.api import mcp
    for name in mcp.DECK_ANALYSIS:
        assert "names the deck" in mcp.BY_NAME[name].description, name


def test_get_deck_for_assistants_leads_with_the_deck_and_stays_small(agent, bot, catalog):
    """#232: get_deck answered 74,617 characters for one deck, 95% of it per-card ownership, with the identity after it."""
    import json
    write = make_token(agent, scopes=["read", "write"])
    saved = call_tool(bot, write, "save_deck", name="Sliver Swarm", text=SLIVERS)["structuredContent"]
    lean = call_tool(bot, write, "get_deck", deck_id=saved["id"])["structuredContent"]
    keys = list(lean)
    assert keys[:5] == ["_links", "id", "name", "format", "overview"] and keys.index("text") > keys.index("coverage")
    assert lean["overview"]["commanders"] == ["Sliver Overlord", "The First Sliver"] and lean["text"] == SLIVERS
    assert lean["summary"]["need"] == 100 and lean["summary"]["have"] + lean["summary"]["missing"] == 100
    assert lean["coverage"]["cards_total"] == len(lean["coverage"]["cards"]) + lean["coverage"]["fully_owned"] or lean["coverage"]["shown"]
    assert len(lean["coverage"]["cards"]) <= 40 and not any(c["owned_printings"] for c in lean["coverage"]["cards"])
    assert len(json.dumps(lean)) < 15_000, len(json.dumps(lean))
    full = call_tool(bot, write, "get_deck", deck_id=saved["id"], all_cards=True)["structuredContent"]
    assert len(full["coverage"]["cards"]) >= len(lean["coverage"]["cards"]) and full["coverage"].get("cards_total") is None


def test_the_websites_deck_answer_is_unchanged(agent):
    deck = agent.post(f"{V1}/decks", json={"name": "Slivers", "text": SLIVERS}).json()
    web = agent.get(f"{V1}/decks/{deck['id']}").json()  # no detail: every card, as the website reads it
    assert len(web["coverage"]["cards"]) >= 4 and "cards_total" not in web["coverage"] or web["coverage"]["cards_total"] is None
    assert web["summary"] is None


def test_the_simulation_warns_when_a_deck_has_three_or_more_colours_it_does_not_check(agent, bot, catalog):
    write = make_token(agent, scopes=["read", "write"])
    five = call_tool(bot, write, "save_deck", name="Five", text=SLIVERS)["structuredContent"]
    out = call_tool(bot, write, "simulate_draws", deck_id=five["id"], format="commander", samples=0)["structuredContent"]
    assert "5 colours (WUBRG)" in out["result"]["colour_warning"] and "any land pays for any spell" in out["result"]["colour_warning"]
    green = call_tool(bot, write, "save_deck", name="Green", text="Commander\n1 Sol Ring\n\nDeck\n99 Forest")["structuredContent"]
    quiet = call_tool(bot, write, "simulate_draws", deck_id=green["id"], format="commander", samples=0)["structuredContent"]
    assert "colour_warning" not in quiet["result"]


def test_a_hundred_card_deck_with_nothing_owned_stays_under_the_cap(agent, bot, app):
    """The size that mattered: 95 different cards, none owned (the biggest answer a deck can make)."""
    import json
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, [oracle(f"{i:08d}-0000-0000-0000-000000000000", f"Test Card {i:02d}", ["G"], "Creature")
                                  for i in range(1, 96)])
        for source in ("oracle_cards", "oracle_tags", "oracle_prices"):
            cs.record_source(db, source, version=source + "-1", rows=95)
        db.commit()
    text = "Commander\n1 Test Card 01\n\nDeck\n" + "\n".join(f"1 Test Card {i:02d}" for i in range(2, 96))
    write = make_token(agent, scopes=["read", "write"])
    saved = call_tool(bot, write, "save_deck", name="Big", text=text)["structuredContent"]
    lean = call_tool(bot, write, "get_deck", deck_id=saved["id"])["structuredContent"]
    full = call_tool(bot, write, "get_deck", deck_id=saved["id"], all_cards=True)["structuredContent"]
    assert len(lean["coverage"]["cards"]) == 40 and lean["coverage"]["cards_total"] == 95 and lean["coverage"]["fully_owned"] == 0
    assert "the 40 dearest of the 95 cards not fully owned" in lean["coverage"]["shown"]
    assert len(json.dumps(lean)) < 15_000 < len(json.dumps(full)), (len(json.dumps(lean)), len(json.dumps(full)))


def test_an_archidekt_deck_saved_before_sections_were_kept_says_how_to_get_its_commander(agent, bot, catalog):
    """#280: three of the owner's four decks answered 'Not set / Not detected' with nothing to say what to do about it."""
    write = make_token(agent, scopes=["read", "write"])
    old = agent.post(f"{V1}/decks", json={"name": "Old import", "text": "1 Sol Ring\n99 Forest", "source_url": "https://archidekt.com/decks/123"}).json()
    new = agent.post(f"{V1}/decks", json={"name": "Sliver Swarm", "text": SLIVERS, "source_url": "https://archidekt.com/decks/456"}).json()
    pasted = agent.post(f"{V1}/decks", json={"name": "Pasted", "text": "1 Sol Ring\n99 Forest"}).json()
    lookalike = agent.post(f"{V1}/decks", json={"name": "Not Archidekt", "text": "1 Sol Ring\n99 Forest",
                                                "source_url": "https://evilarchidekt.com/decks/9"}).json()
    items = {d["name"]: d for d in call_tool(bot, write, "list_decks")["structuredContent"]["items"]}
    assert "refresh_deck" in items["Old import"]["overview"]["note"] and items["Old import"]["overview"]["commanders"] == []
    assert not items["Sliver Swarm"]["overview"].get("note")  # it has its commander
    assert not items["Pasted"]["overview"].get("note") and not items["Not Archidekt"]["overview"].get("note")  # nothing to re-read from
    one = call_tool(bot, write, "get_deck", deck_id=old["id"])["structuredContent"]
    assert "refresh_deck" in one["overview"]["note"]
    stats = call_tool(bot, write, "deck_stats", deck_id=old["id"])["structuredContent"]
    assert "refresh_deck" in stats["deck"]["overview"]["note"]
    assert new["id"] and pasted["id"] and lookalike["id"]
