"""The Commander Bracket hint (vault.brackets, #171): computed from the published bracket rules with every input listed
and labelled, roles from Oracle text as a labelled fallback to Scryfall's tags (#18), and the Commander expert and the casual
table briefs that use it."""

from pathlib import Path

import pytest

from tests.test_agents import call_tool, make_token
from tests.test_catalog_api import BOLT, load
from tests.test_deck_api import CARDS, DECK, PRICES, TAGS, card, oid
from tests.test_mcp_catalog import bot  # noqa: F401
from twins import Universe
from vault import brackets
from vault import catalog_sync as cs
from vault import combos as spellbook
from vault import experts

V1 = "/api/v1/decks"
ROOT = Path(__file__).resolve().parent.parent

EXTRA = [
    card(21, "Changer One", "Artifact", [], 1, game_changer=True),
    card(22, "Changer Two", "Artifact", [], 1, game_changer=True),
    card(23, "Changer Three", "Artifact", [], 1, game_changer=True),
    card(24, "Changer Four", "Artifact", [], 1, game_changer=True),
    card(25, "Test Armageddon", "Sorcery", ["W"], 4, text="Destroy all lands."),
    card(26, "Test Time Warp", "Instant", ["U"], 5, text="Target player takes an extra turn after this one."),
    card(27, "Test Tutor", "Sorcery", ["B"], 2, text="Search your library for a card, put that card into your hand, then shuffle."),
    card(28, "Test Divination", "Sorcery", ["U"], 3, text="Draw two cards."),
    card(29, "Tagged Rock", "Artifact", [], 2, rank=40, text="{T}: Add {C}."),
    card(30, "Thassa's Oracle", "Creature — Merfolk Wizard", ["U"], 2),
    card(31, "Demonic Consultation", "Instant", ["B"], 1),
]


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def app(settings, universe):
    from vault.app import create_app

    app = create_app(settings, serve_static=False, transport=universe.transport)
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def loaded(universe, database_url):
    from fastapi.testclient import TestClient

    from vault.app import create_app
    from vault.config import Settings

    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver")
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        with app.state.db.sessions() as db:
            cs.sync_oracle_cards(db, CARDS + EXTRA)
            tags = [{"id": "t-ramp", "slug": "ramp", "label": "ramp", "parent_ids": [], "child_ids": [],
                     "taggings": [{"oracle_id": oid(n), "weight": "strong"} for n in (3, 7, 8, 10, 29)]}]
            cs.sync_oracle_tags(db, tags)
            for name in ("oracle_cards", "oracle_tags"):
                cs.record_source(db, name, version=name + "-1", rows=1)
            db.commit()
        c.app = app
        yield c
    app.state.db.engine.dispose()


def stats(client, text, **extra):
    res = client.post(f"{V1}/stats", json={"text": text, **extra})
    assert res.status_code == 200, res.text
    return res.json()


def deck_with(*names):
    return "Commander\n1 Test Commander\n\nDeck\n" + "".join(f"1 {n}\n" for n in names)


def test_a_deck_with_none_of_the_inputs_has_floor_one_and_lists_every_input(loaded):
    answer = stats(loaded, DECK)
    b = answer["result"]["bracket"]
    wizards = [i for i in answer["provenance"][0]["inputs"] if i["source"] == "Wizards of the Coast"]
    assert len(wizards) == 1 and wizards[0]["url"] == brackets.SOURCES[0]["url"] and wizards[0]["as_of"] == "2026-10-07"
    assert "Commander Brackets" in wizards[0]["origin"] and wizards[0]["notice"]  # the Fan Content notice travels with Wizards' material
    assert b["floor"] == 1 and "Bracket 1" in b["floor_means"]
    assert set(b["inputs"]) == {"game_changers", "mass_land_denial", "extra_turns", "tutors", "two_card_combos"}
    assert all(i["rule"] for i in b["inputs"].values())
    assert b["label"].startswith("Computed by the Vault") and "not a placement" in b["label"]
    assert b["inputs"]["two_card_combos"]["checked"] is False and "include_combos" in b["inputs"]["two_card_combos"]["reason"]
    assert any("not checked" in w for w in b["why"])  # the floor may be higher, and it says so
    assert b["not_computed"] and b["rules_read_on"] == "2026-10-07" and "provenance" in b["sources"]


def test_game_changers_set_floor_three_up_to_three_and_four_above(loaded):
    one = stats(loaded, deck_with("Changer One", "Test Rock"))["result"]
    assert one["bracket"]["floor"] == 3 and one["bracket"]["inputs"]["game_changers"]["count"] == 1
    assert one["game_changers"]["bracket_floor"] == 3  # the older field is unchanged
    three = stats(loaded, deck_with("Changer One", "Changer Two", "Changer Three"))["result"]["bracket"]
    assert three["floor"] == 3 and three["inputs"]["game_changers"]["count"] == 3
    four = stats(loaded, deck_with("Changer One", "Changer Two", "Changer Three", "Changer Four"))["result"]["bracket"]
    assert four["floor"] == 4 and four["inputs"]["game_changers"]["floor"] == 4
    assert "game changers (4) sets a floor of 4" in four["why"][0]


def test_mass_land_denial_found_by_its_text_sets_floor_four_and_says_how_it_was_found(loaded):
    b = stats(loaded, deck_with("Test Armageddon"))["result"]["bracket"]
    assert b["floor"] == 4
    found = b["inputs"]["mass_land_denial"]["cards"][0]
    assert found["name"] == "Test Armageddon" and found["basis"] == "computed" and found["rule"] == "mass-land-denial"
    assert "Wizards" in b["inputs"]["mass_land_denial"]["rule"]


def test_an_extra_turn_card_sets_floor_two_and_is_only_counted(loaded):
    b = stats(loaded, deck_with("Test Time Warp"))["result"]["bracket"]
    assert b["floor"] == 2 and b["inputs"]["extra_turns"]["count"] == 1
    assert any("chained" in n for n in b["not_computed"])  # what it cannot see is said in the answer


def test_tutors_are_listed_but_set_no_floor_because_wizards_removed_the_limit(loaded):
    b = stats(loaded, deck_with("Test Tutor"))["result"]["bracket"]
    assert b["floor"] == 1 and b["inputs"]["tutors"]["count"] == 1 and b["inputs"]["tutors"]["floor"] is None
    assert "removed tutor limits" in b["inputs"]["tutors"]["rule"]
    assert b["inputs"]["tutors"]["cards"][0]["basis"] == "computed"


def test_the_floor_is_the_highest_input(loaded):
    b = stats(loaded, deck_with("Changer One", "Test Time Warp", "Test Armageddon"))["result"]["bracket"]
    assert b["floor"] == 4 and len(b["why"]) >= 3 and b["why"][0].startswith("mass land denial")


def test_two_card_combos_from_commander_spellbook_set_floor_three_only_when_asked(loaded, universe):
    deck = deck_with("Thassa's Oracle", "Demonic Consultation", "Test Rock")
    quiet = stats(loaded, deck)
    assert not [c for c in universe.spellbook.calls if c.path == "/find-my-combos"]  # the deck is not sent unless asked
    assert quiet["result"]["bracket"]["inputs"]["two_card_combos"]["checked"] is False
    asked = stats(loaded, deck, include_combos=True)
    b = asked["result"]["bracket"]
    assert b["floor"] == 3 and b["inputs"]["two_card_combos"]["checked"] is True
    combo = b["inputs"]["two_card_combos"]["combos"][0]
    assert sorted(combo["cards"]) == ["Demonic Consultation", "Thassa's Oracle"] and "Win the game" in combo["produces"]
    assert combo["url"].startswith("https://commanderspellbook.com/combo/") and combo["source"] == "Commander Spellbook"
    inputs = {i["source"] for i in asked["provenance"][0]["inputs"]}
    assert {"Scryfall", "Scryfall Tagger", "Wizards of the Coast", "Commander Spellbook"} <= inputs
    assert asked["provenance"][0]["kind"] == "computed"
    assert len([c for c in universe.spellbook.calls if c.path == "/find-my-combos"]) == 1


def test_a_combo_that_is_not_two_cards_or_not_infinite_sets_no_floor(loaded, universe):
    universe.spellbook.add_combo(["Thassa's Oracle", "Demonic Consultation", "Test Rock"], ["Win the game"], "Three cards.")
    universe.spellbook.add_combo(["Test Rock", "Test Burn"], ["Gain 1 life"], "Two cards that do not loop.")
    deck = deck_with("Test Rock", "Test Burn")
    results = spellbook.two_card_combos({"included": [universe.spellbook.combos[-1], universe.spellbook.combos[-2]]})
    assert results == []
    assert stats(loaded, deck, include_combos=True)["result"]["bracket"]["floor"] == 1


def test_when_commander_spellbook_cannot_answer_the_rest_of_the_answer_stands(loaded, universe):
    universe.spellbook.fail_next("/find-my-combos", 500)
    r = stats(loaded, deck_with("Changer One", "Thassa's Oracle"), include_combos=True)["result"]["bracket"]
    assert r["floor"] == 3 and r["inputs"]["two_card_combos"]["checked"] is False and "Commander Spellbook" in r["inputs"]["two_card_combos"]["reason"]


def test_roles_from_oracle_text_are_computed_and_labelled_where_tagger_has_no_tag(loaded):
    roles = stats(loaded, deck_with("Test Divination", "Tagged Rock"))["result"]["roles"]
    draw = roles["draw"]
    assert draw["count"] == 1 and draw["computed"] == 1 and draw["from_tagger"] == 0
    assert draw["cards"][0] == {"name": "Test Divination", "quantity": 1, "tag_weight": None, "basis": "computed", "rule": "draw-cards"}
    ramp = roles["ramp"]  # Tagger tags the rock and the text rule would too: Tagger wins, nothing is counted twice
    assert ramp["count"] == 1 and ramp["from_tagger"] == 1 and ramp["computed"] == 0
    assert ramp["cards"][0]["basis"] == "scryfall_tagger" and ramp["cards"][0]["tag_weight"] == "strong" and "rule" not in ramp["cards"][0]


def test_a_card_lookup_shows_its_computed_roles_with_the_rule_and_a_computed_provenance_block(loaded):
    body = loaded.get("/api/v1/catalog/cards", params={"name": "Test Divination"}).json()
    assert [(r["role"], r["rule"], r["basis"]) for r in body["computed_roles"]] == [("draw", "draw-cards", "computed")]
    assert body["computed_roles"][0]["known_to_get_wrong"] and body["tags"] == []
    assert any(b["kind"] == "computed" and "Oracle text" in b["origin"] for b in body["provenance"])
    rock = loaded.get("/api/v1/catalog/cards", params={"name": "Tagged Rock"}).json()
    assert rock["computed_roles"] == [] and rock["tags"][0]["tag"] == "ramp"


# -- the expert and the casual table use it ----------------------------------------------------------------

@pytest.mark.parametrize("agent", ["vault-commander-expert", "vault-casual-table"])
def test_the_commander_expert_and_the_casual_table_are_told_to_use_the_bracket_hint(agent):
    brief = (ROOT / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    assert "deck_stats" in brief and "include_combos" in brief and "bracket" in brief and "floor" in brief
    assert "computed" in brief
    assert "bracket floor\n   from them alone" not in brief and "are not counted by the tool" not in brief  # the old claim is gone


def test_the_council_for_commander_hands_both_briefs_with_the_bracket_instruction():
    council = experts.council("commander", "review my deck")
    panel = {m["id"]: m["brief"] for m in council["panel"]}
    for agent in ("vault-commander-expert", "vault-casual-table"):
        assert "include_combos" in panel[agent] and "bracket" in panel[agent], agent
    assert "include_combos" in council["how_to_run"] and "computed floor" in council["how_to_run"]
    assert "include_combos" in council["chair"]  # the chair's own rules (skills/expert-council)


def test_the_tool_takes_include_combos_and_the_mcp_answer_has_the_bracket(loaded, universe, bot):  # noqa: F811
    token = make_token(loaded)
    tool = next(t for t in __import__("vault.api.mcp", fromlist=["TOOLS"]).TOOLS if t.name == "deck_stats")
    assert "include_combos" in tool.properties and "bracket" in tool.description
    assert not any(w in tool.description.lower() for w in ("always", "never ask", "no need to confirm"))
    result = call_tool(bot, token, "deck_stats", text=deck_with("Thassa's Oracle", "Demonic Consultation"), include_combos=True)["structuredContent"]
    assert result["result"]["bracket"]["floor"] == 3


def test_the_hints_constants_name_the_pages_they_were_read_from():
    assert all(s["url"].startswith("https://magic.wizards.com/en/") for s in brackets.SOURCES)
    assert brackets.RULES_READ.isoformat() == "2026-10-07"
