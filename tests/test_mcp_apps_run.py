"""The MCP Apps views are run, not just parsed: each view's script executes under node (tests/mcp_view_harness.js) against a tiny
DOM and a fake host, on what the real tools answered, and the test reads what a person would see and which tools the view
called. (A stub is not a browser: no layout, no CSS, no sandbox. It catches a view that throws on real data, shows the wrong
thing, or calls the wrong tool with the wrong arguments, which `node --check` cannot.)"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_agents import call_tool, make_token
from tests.test_catalog_api import BOLT  # noqa: F401  (fixtures below need the catalog helpers)
from tests.test_deck_api import VALID
from tests.test_mcp_catalog import agent, bot  # noqa: F401  (fixtures)
from vault.api import mcp_ui
from vault.deck_tools import FORMATS

HARNESS = Path(__file__).parent / "mcp_view_harness.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is needed to run the views")


def run(view, tmp_path, input, result, tools=None, steps=None, **extra):
    import re

    script = re.search(r"<script>(.*)</script>", mcp_ui.html(view), re.S).group(1)
    (tmp_path / "view.js").write_text(script, encoding="utf-8")
    (tmp_path / "scenario.json").write_text(json.dumps({"input": input, "result": result, "tools": tools or {}, "steps": steps or [], **extra}), encoding="utf-8")
    done = subprocess.run(["node", str(HARNESS), str(tmp_path / "view.js"), str(tmp_path / "scenario.json")], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["errors"] == [], out["errors"]
    return out["snapshots"]


def answer(bot, token, tool, **args):
    res = call_tool(bot, token, tool, **args)
    assert res["isError"] is False, res
    return res["structuredContent"]


def control(snap, tag, text=None, type=None):
    found = [c for c in snap["controls"] if c["tag"] == tag and (text is None or text in c["text"]) and (type is None or c["type"] == type)]
    assert found, (tag, text, [c["text"] for c in snap["controls"]])
    return found[0]


@pytest.fixture
def saved(agent):
    """A saved deck of the person's (its id is what an assistant is steered to pass), and an agent token that can read it."""
    deck = agent.post("/api/v1/decks", json={"name": "Test deck", "text": VALID, "format": "commander"})
    assert deck.status_code == 201, deck.text
    return deck.json()["id"]


@pytest.fixture
def token(agent):
    return make_token(agent)


def test_every_view_runs_on_what_the_real_tool_answered_and_ends_with_provenance(agent, bot, token, saved, tmp_path):
    answers = {
        "card": ("get_card_oracle", {"name": "Lightning Bolt"}), "deck": ("deck_stats", {"deck_id": saved}),
        "combos": ("find_combos", {"deck_id": saved}), "upgrades": ("find_upgrades", {"deck_id": saved, "format": "commander", "budget_usd": 5, "roles": ["ramp"]}),
        "steps": ("present_steps", {"title": "T", "steps": [{"text": "First.", "rules": ["100.1"]}]}), "shopping": ("shopping_list", {"deck_id": saved}),
    }
    assert set(answers) | {"printings"} == set(mcp_ui.VIEWS)
    for view, (tool, args) in answers.items():
        result = answer(bot, token, tool, **args)
        first = run(view, tmp_path, args, result, tools={"deck_legality": {"result": {"format": "commander", "legal": True, "issues": [], "not_checked": ["x"]}, "provenance": []},
                                                         "find_combos": result if view == "combos" else {}})[0]
        assert "not produced or endorsed by Scryfall" in first["text"], view
        assert "Loading..." not in first["text"], view
        if view in ("deck", "upgrades", "shopping", "combos"):  # #216: the panel opens with the deck (name, format, commander), as run
            dot = chr(0xb7)
            assert first["text"].startswith(f"Test deck{chr(10)}Commander {dot} Commander: Test Commander {dot} 100 cards"), (view, first["text"][:120])
        assert "ui/initialize" in first["requests"] and "ui/notifications/initialized" in first["notifications"], view


def test_the_deck_dashboard_shows_legality_on_opening_for_a_saved_deck_and_for_a_pasted_list(agent, bot, token, saved, tmp_path):
    for args in ({"deck_id": saved}, {"text": VALID}):
        stats = answer(bot, token, "deck_stats", **args)
        legality = answer(bot, token, "deck_legality", format="commander", **args)
        first = run("deck", tmp_path, args, stats, tools={"deck_legality": legality})[0]
        assert [c["name"] for c in first["calls"]] == ["deck_legality"], args  # asked on opening, without a click
        assert first["calls"][0]["arguments"] == {**args, "format": "commander"}  # the deck's own format, by id or by text
        assert "Legal in commander" in first["text"] or "Not legal in commander" in first["text"]
        assert control(first, "select", "Choose a format")["value"] == "commander"  # preselected


def test_the_format_chooser_offers_every_format_the_server_checks_and_a_list_without_a_format_is_not_guessed(agent, bot, token, tmp_path):
    sixty = "Deck\n" + "\n".join(["4 Test Rock", "4 Test Burn", "52 Test Mountain"]) + "\n"
    stats = answer(bot, token, "deck_stats", text=sixty)
    first = run("deck", tmp_path, {"text": sixty}, stats)[0]
    options = control(first, "select", "Choose a format")["text"].split("|")
    assert options == ["Choose a format", *FORMATS] and len(FORMATS) == 20
    assert first["calls"] == [] and "Choose a format to check this deck against." in first["text"]  # nothing is assumed
    legality = answer(bot, token, "deck_legality", text=sixty, format="modern")
    after = run("deck", tmp_path, {"text": sixty}, stats, tools={"deck_legality": legality},
                steps=[{"action": "change", "tag": "select", "value": "modern"}, {"action": "click", "tag": "button", "text": "Check"}])[-1]
    assert after["calls"] == [{"name": "deck_legality", "arguments": {"text": sixty, "format": "modern"}}] and "Legal in modern" in after["text"]


def test_a_host_that_does_not_forward_the_tool_input_still_gets_legality_for_a_saved_deck(agent, bot, token, saved, tmp_path):
    stats = answer(bot, token, "deck_stats", deck_id=saved)  # the answer's own deck block carries the id
    legality = answer(bot, token, "deck_legality", deck_id=saved, format="commander")
    first = run("deck", tmp_path, {}, stats, tools={"deck_legality": legality})[0]
    assert first["calls"] == [{"name": "deck_legality", "arguments": {"deck_id": saved, "format": "commander"}}]


def test_the_deck_dashboard_finds_combos_on_demand_and_credits_commander_spellbook(agent, bot, token, saved, tmp_path):
    stats = answer(bot, token, "deck_stats", deck_id=saved)
    combos = answer(bot, token, "find_combos", deck_id=saved)
    legality = answer(bot, token, "deck_legality", deck_id=saved, format="commander")
    snaps = run("deck", tmp_path, {"deck_id": saved}, stats, tools={"deck_legality": legality, "find_combos": combos},
                steps=[{"action": "click", "tag": "button", "text": "Find combos"}])
    assert [c["name"] for c in snaps[0]["calls"]] == ["deck_legality"]  # a third party is asked only when the person asks
    assert snaps[1]["calls"][-1] == {"name": "find_combos", "arguments": {"deck_id": saved}}
    assert "Commander Spellbook" in snaps[1]["text"] and "combo(s) in this deck" in snaps[1]["text"]
    assert "never tell a player it is combo-free" in snaps[1]["text"]  # the limits the tool states are shown with the result


def test_the_combos_view_lists_each_combo_with_its_source_and_a_link_through_the_host(tmp_path):
    result = {"deck": {"id": 1, "name": "D", "overview": {"format": "commander", "commanders": ["A"], "cards": 100, "color_identity": "UB"}},
              "result": {"included": [{"id": "1-2", "url": "https://commanderspellbook.com/combo/1-2", "cards": ["Thassa's Oracle", "Demonic Consultation"],
                                       "missing": [], "produces": ["Win the game"], "description": "Cast both.", "mana_needed": "{U}{B}", "prerequisites": None, "bracket_tag": "R"}],
                        "almost_included": [{"id": "3", "url": "https://commanderspellbook.com/combo/3", "cards": ["X", "Y"], "missing": ["Y"], "produces": ["Infinite mana"],
                                             "description": "", "mana_needed": None, "prerequisites": "Y on the battlefield", "bracket_tag": None}],
                        "totals": {"included": 1, "almost_included": 1}, "notes": ["Combos are Commander Spellbook's."], "limits": "Only combos known to Commander Spellbook."},
              "provenance": [{"kind": "source", "source": "Commander Spellbook", "origin": "combos written by its community", "url": "https://commanderspellbook.com", "as_of": "2026-10-04", "notice": "Fan Content notice."}]}
    snaps = run("combos", tmp_path, {"deck_id": 1}, result, steps=[{"action": "click", "tag": "a", "text": "Open this combo"}])
    text = snaps[0]["text"]
    assert "Thassa's Oracle + Demonic Consultation" in text and "Produces: Win the game" in text and "Missing: Y" in text and "Source: Commander Spellbook" in text
    assert "1 combo(s) in this deck, 1 one card short" in text and "Fan Content notice." in text
    assert snaps[1]["requests"].count("ui/open-link") == 1  # links go through the host


def test_the_upgrades_view_pairs_each_add_with_a_cut_and_shows_the_price_difference(agent, bot, token, saved, tmp_path):
    args = {"deck_id": saved, "format": "commander", "budget_usd": 5, "roles": ["ramp"]}
    up = answer(bot, token, "find_upgrades", **args)
    cut = up["result"]["cut_candidates"][0]["name"]
    add = up["result"]["candidates"]["ramp"][0]
    validated = answer(bot, token, "validate_deck_changes", deck_id=saved, format="commander", adds=[add["name"]], cuts=[cut], budget_usd=5)
    snaps = run("upgrades", tmp_path, args, up, tools={"validate_deck_changes": validated},
                steps=[{"action": "check", "tag": "input", "type": "checkbox", "nth": 0}, {"action": "click", "tag": "button", "text": "Check this plan"}])
    picked, checked = snaps[1], snaps[2]
    assert f"Add {add['name']}" in picked["text"] and f"Cut: " in picked["text"]
    assert control(picked, "select", "No cut for this one")["value"] == cut  # the least played card is suggested as the cut
    cut_price = up["result"]["cut_candidates"][0]["price_usd"]
    delta = round(add["price_usd"] - cut_price, 2)
    assert f"Price change +${delta:.2f}" in picked["text"] and f"adds ${add['price_usd']:.2f}" in picked["text"]  # a delta against the cut card
    assert checked["calls"] == [{"name": "validate_deck_changes", "arguments": {"deck_id": saved, "format": "commander", "adds": [add["name"]], "cuts": [cut], "budget_usd": 5}}]
    assert "Valid." in checked["text"] and "Net price change: +$" in checked["text"] and "The deck: $" in checked["text"]  # and against the deck
    assert f"{validated['result']['deck_cost_before_usd']:.2f} before" in checked["text"] and f"{validated['result']['deck_cost_after_usd']:.2f} after" in checked["text"]


def test_the_budget_slider_asks_the_validator_again_and_works_for_a_saved_deck(agent, bot, token, saved, tmp_path):
    args = {"deck_id": saved, "format": "commander", "budget_usd": 5, "roles": ["ramp"]}
    up = answer(bot, token, "find_upgrades", **args)
    cheaper = answer(bot, token, "find_upgrades", **{**args, "budget_usd": 1})
    add, cut = up["result"]["candidates"]["ramp"][0], up["result"]["cut_candidates"][0]
    validated = answer(bot, token, "validate_deck_changes", deck_id=saved, format="commander", adds=[add["name"]], cuts=[cut["name"]], budget_usd=1)
    snaps = run("upgrades", tmp_path, args, up, tools={"validate_deck_changes": validated, "find_upgrades": cheaper},
                steps=[{"action": "check", "tag": "input", "type": "checkbox", "nth": 0}, {"action": "change", "tag": "input", "type": "range", "value": "1"}])
    moved = snaps[-1]
    names = [c["name"] for c in moved["calls"]]
    assert sorted(names) == ["find_upgrades", "validate_deck_changes"]  # both asked again: the plan is checked at the new budget
    by_name = {c["name"]: c["arguments"] for c in moved["calls"]}
    assert by_name["validate_deck_changes"] == {"deck_id": saved, "format": "commander", "adds": [add["name"]], "cuts": [cut["name"]], "budget_usd": 1}
    assert by_name["find_upgrades"] == {**args, "budget_usd": 1}  # by deck_id, not by text
    assert "budget: $1" in moved["text"] and f"Add {add['name']}" in moved["text"]  # the plan survives the new candidates
    assert "Valid." in moved["text"] or "Not valid." in moved["text"]
    assert any(c["tag"] == "input" and c["type"] == "range" and c["value"] == "1" for c in moved["controls"])


def test_removing_a_cut_or_choosing_another_changes_the_pair_and_a_cut_is_used_once(agent, bot, token, saved, tmp_path):
    args = {"deck_id": saved, "format": "commander", "budget_usd": 50, "roles": ["ramp"]}
    up = answer(bot, token, "find_upgrades", **args)
    cuts = [c["name"] for c in up["result"]["cut_candidates"]]
    assert len(up["result"]["candidates"]["ramp"]) >= 2 and len(cuts) >= 2
    snaps = run("upgrades", tmp_path, args, up, steps=[
        {"action": "check", "tag": "input", "type": "checkbox", "nth": 0}, {"action": "check", "tag": "input", "type": "checkbox", "nth": 1},
        {"action": "change", "tag": "select", "nth": 1, "value": cuts[0]}])
    two, swapped = snaps[2], snaps[3]
    selects = [c["value"] for c in two["controls"] if c["tag"] == "select"]
    assert selects == [cuts[0], cuts[1]]  # two adds, two different cuts
    assert [c["value"] for c in swapped["controls"] if c["tag"] == "select"] == ["", cuts[0]]  # the same cut is not used twice
    assert swapped["calls"] == []  # picking cards asks nothing until the plan is checked


def test_a_pasted_list_still_works_in_the_upgrades_view(agent, bot, token, tmp_path):
    args = {"text": VALID, "format": "commander", "budget_usd": 5, "roles": ["ramp"]}
    up = answer(bot, token, "find_upgrades", **args)
    cheaper = answer(bot, token, "find_upgrades", **{**args, "budget_usd": 2})
    snaps = run("upgrades", tmp_path, args, up, tools={"find_upgrades": cheaper},
                steps=[{"action": "change", "tag": "input", "type": "range", "value": "2"}])
    assert snaps[1]["calls"] == [{"name": "find_upgrades", "arguments": {**args, "budget_usd": 2}}]


def test_the_shopping_list_can_be_copied_and_saved_as_a_file_with_a_text_fallback(agent, bot, token, saved, tmp_path):
    result = answer(bot, token, "shopping_list", deck_id=saved)
    assert result["result"]["lines"]
    text = result["result"]["text"]
    snaps = run("shopping", tmp_path, {"deck_id": saved}, result, steps=[
        {"action": "click", "tag": "button", "text": "Copy list"}, {"action": "click", "tag": "button", "text": "Download as a text file"}])
    assert snaps[1]["copied"] == [text] and "Copied." in snaps[1]["text"]
    assert snaps[2]["downloads"] == [{"name": "shopping-list.txt", "text": text}] and "Download started" in snaps[2]["text"]
    blocked = run("shopping", tmp_path, {"deck_id": saved}, result, steps=[{"action": "click", "tag": "button", "text": "Download as a text file"}], blockDownloads=True)[-1]
    assert blocked["downloads"] == [] and "blocks downloads: use Copy list" in blocked["text"]
    assert any(c["tag"] == "textarea" and c["value"] == text for c in blocked["controls"])  # the text is still there to copy


def test_the_shopping_view_shows_copies_and_saves_whatever_formats_the_tool_returns(agent, bot, token, saved, tmp_path):
    result = answer(bot, token, "shopping_list", deck_id=saved)
    result["result"]["formats"] = {"Card Kingdom": "1 Cheap Ramp [CK]", "TCGplayer": "1 Cheap Ramp [TCG]"}
    snaps = run("shopping", tmp_path, {"deck_id": saved}, result, steps=[
        {"action": "change", "tag": "select", "value": "2"}, {"action": "click", "tag": "button", "text": "Copy list"},
        {"action": "click", "tag": "button", "text": "Download as a text file"}])
    assert control(snaps[0], "select", "Plain list")["text"] == "Plain list|Card Kingdom|TCGplayer"
    assert snaps[2]["copied"] == ["1 Cheap Ramp [TCG]"] and snaps[3]["downloads"] == [{"name": "shopping-list-tcgplayer.txt", "text": "1 Cheap Ramp [TCG]"}]
