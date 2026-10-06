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


def oracle(oracle_id, name, identity):
    return {"object": "card", "id": "p-" + oracle_id[:4], "oracle_id": oracle_id, "name": name, "layout": "normal",
            "mana_cost": "", "cmc": 0.0, "type_line": "Legendary Creature", "oracle_text": "", "colors": identity,
            "color_identity": identity, "keywords": [], "legalities": {"commander": "legal"}, "digital": False}


@pytest.fixture
def catalog(app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, [oracle("55555555-5555-5555-5555-555555555555", "Sliver Overlord", ["U", "B"]),
                                  oracle("66666666-6666-6666-6666-666666666666", "The First Sliver", ["W", "U", "B", "R", "G"])])
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
