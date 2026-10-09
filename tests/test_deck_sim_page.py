"""The deck page's "Opening turns" tab (#138, docs/deck-simulation-design.md): its wording is public/lib/deck_sim.js, its layout
public/views/deck_opening.jsx.

- Every figure and sentence the person reads equals a field of the server's real answers (POST /decks/simulate and /decks/stats), run
  through the page's own code (tests/js/deck_sim_driver.mjs): the tiles, the four odds panels, the sample games, the plan note, the
  curve, the margin, the provenance line.
- The states use the server's own words: the 400 for too few cards, and the 429 with its Retry-After.
- The tab sits after Stats, the Stats tab links to it, the curve is one component, and the new files are wired into the build.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_deck_api import loaded  # noqa: F401 - fixture
from vault.api.deck_api import DECK_LIMIT

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
PAGE = (PUBLIC / "views" / "deck.jsx").read_text(encoding="utf-8")
TAB = (PUBLIC / "views" / "deck_opening.jsx").read_text(encoding="utf-8")
LIB = (PUBLIC / "lib" / "deck_sim.js").read_text(encoding="utf-8")
V1 = "/api/v1/decks"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")

# A Commander deck with a card that gives no maximum hand size (the plan note), a card the catalog does not know, and enough
# cards to play 6 turns.
DECK = "Commander\n1 Test Commander\n\nDeck\n37 Test Mountain\n30 Dull Bear\n31 Cheap Ramp\n1 Unknown Card\n"


def page_says(data: dict) -> dict:
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "deck_sim_driver.mjs")], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, input=json.dumps(data))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def real_answers(client, **extra):
    body = {"text": DECK, "format": "commander", "games": 1000, "samples": 5, **extra}
    sim = client.post(f"{V1}/simulate", json=body)
    stats = client.post(f"{V1}/stats", json={"text": DECK})
    assert sim.status_code == 200 and stats.status_code == 200, (sim.text, stats.text)
    return sim.json(), stats.json()["result"]


def pct(v):
    return f"{v:.1f}%"


@needs_node
def test_every_figure_and_sentence_equals_the_field_of_the_answer_it_reads(loaded):
    sim, stats = real_answers(loaded)
    r = sim["result"]
    said = page_says({"simulate": sim, "stats": stats, "errors": [], "turns": 6})
    # the deck leads (AGENTS.md section 4): name, format, cards, commander, colours, from the answer's overview
    overview = sim["deck"]["overview"]
    assert said["deckLine"].startswith("Pasted decklist") and f"{overview['cards']} cards" in said["deckLine"]
    assert f"commander: {overview['commanders'][0]}" in said["deckLine"]
    # the margin printed is the server's (1,000 games: about 3 points), not a number of the page's own
    assert r["margin_points"] == 3.1 and said["intro"]["margin"].endswith("at 1,000 games a percentage is good to about 3 points either way.")
    assert said["intro"]["body"] == "The Vault played this list 1,000 times with a simple player (no opponent) and counted what happened in the first 6 turns."
    assert f"seed {r['seed']}" in said["asPlayed"] and "multiplayer: everyone draws on turn 1" in said["asPlayed"] and said["asPlayed"].startswith("commander")
    # the four tiles are the headline, under the names the answer uses
    assert {t["label"]: t["value"] for t in said["tiles"]} == {
        "Five mana by turn 5": pct(r["headline"]["five_mana_by_turn_5"]), "Missed a land drop by turn 4": pct(r["headline"]["missed_a_land_drop_by_turn_4"]),
        "Discarded by turn 5": pct(r["headline"]["discarded_by_turn_5"]), "Mulligan rate": pct(r["headline"]["mulligan_rate"])}
    # the four odds panels: a row per turn, the value in words, equal to the turn's field
    land, mana, hand, discard = said["panels"]
    for g, field in zip(land["groups"], ("land_drop", "all_land_drops_so_far")):
        assert [x["value"] for x in g["rows"]] == [f"{pct(t[field])} of games" for t in r["per_turn"]]
    assert [x["value"] for x in mana["groups"][0]["rows"]] == [f"{t['mana_available']:.2f} available" for t in r["per_turn"]]
    assert [x["value"] for x in mana["groups"][1]["rows"]] == [f"{t['mana_spent']:.2f} spent" for t in r["per_turn"]]
    assert [x["value"] for x in mana["groups"][2]["rows"]] == [f"{pct(t['every_spell_in_hand_cost_too_much'])} of games" for t in r["per_turn"]]
    assert [x["value"] for x in hand["groups"][0]["rows"]] == [f"{t['cards_in_hand']:.2f} cards" for t in r["per_turn"]]
    assert [x["value"] for x in discard["groups"][0]["rows"]] == [f"{pct(t['discarded_by_now'])} of games" for t in r["per_turn"]]
    assert [x["label"] for x in land["groups"][0]["rows"]] == [f"Turn {t['turn']}" for t in r["per_turn"]]
    assert all(0 <= x["width"] <= 100 for p in said["panels"] for g in p["groups"] for x in g["rows"])
    # the sample games: the first of the run, every turn's cells from the turn's own fields
    assert len(said["games"]) == len(r["samples"]) == 5 and said["samplesIntro"].startswith("The first 5 of the 1,000 games")
    for shown, game in zip(said["games"], r["samples"]):
        assert shown["summary"]["hand"] == ", ".join(game["opening_hand"])
        assert len(shown["turns"]) == len(game["turns"]) == 6
        for cells, t in zip(shown["turns"], game["turns"]):
            assert cells["mana"] == f"{t['mana']} (spent {t['spent']})" and cells["hand"] == t["hand"]
            assert cells["cast"] == (", ".join(t["cast"]) or "nothing") and (t["drew"] is None or cells["drew"] == t["drew"])
            assert any("Nothing castable" in n["text"] for n in cells["notes"]) == t["nothing_affordable"]
            assert [n["text"] for n in cells["notes"] if n["tone"] == "bad"] == (["Discarded " + ", ".join(t["discarded"])] if t["discarded"] else [])
    # the curve is the stats call's, its caption the stats' own counts
    assert [b["n"] for b in said["curve"]["bars"]] == list(stats["curve"].values())
    assert said["curve"]["caption"].startswith(f"{stats['lands']} lands, {stats['nonland']} other cards, average mana value of the non-land cards {stats['average_mana_value_nonland']}.")
    assert "The commander is in the command zone." in said["curve"]["caption"]
    # cards the catalog does not know are said, in the Stats tab's words
    assert said["unmatched"] == "Not in the card catalog: Unknown Card" and r["unmatched"] == ["Unknown Card"]
    # provenance: computed by the Vault, from Scryfall's card data with its date, and the notice the sources require
    block = sim["provenance"][0]
    assert said["provenance"]["sources"][0]["name"] == "Scryfall" and said["provenance"]["sources"][0]["as_of"] == block["inputs"][0]["as_of"]
    assert said["provenance"]["notice"] == block["notice"] and "Fan Content" in said["provenance"]["notice"]


@needs_node
def test_deck_line_uses_the_format_the_server_simulated(loaded):
    sim, stats = real_answers(loaded, format="modern")
    assert sim["deck"]["overview"]["format"] == "commander"
    assert sim["result"]["format"] == "modern"
    said = page_says({"simulate": sim, "stats": stats, "errors": [], "turns": 6})
    assert said["deckLine"].startswith("Pasted decklist · modern · ")
    assert " · commander · " not in said["deckLine"]
    assert "modern · " in said["asPlayed"]


@needs_node
def test_the_deck_s_own_plan_is_shown_as_the_deck_s_not_a_fault(loaded, app):
    from tests.test_deck_api import CARDS, TODAY, card
    from vault import catalog_sync as cs

    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, CARDS + [card(40, "Test Tower", "Land", [], 0, text="You have no maximum hand size."),
                                          card(41, "Circle of Protection: Red", "Enchantment", ["W"], 2, text="Whenever you discard a card, draw one.")], today=TODAY)
        db.commit()
    text = DECK + "1 Test Tower\n1 Circle of Protection: Red\n"
    sim = loaded.post(f"{V1}/simulate", json={"text": text, "format": "commander", "samples": 0}).json()
    plan = sim["result"]["discard_may_be_the_plan"]
    assert "Test Tower: no maximum hand size" in plan and any(p.startswith("Circle of Protection: Red: ") for p in plan)
    said = page_says({"simulate": sim, "stats": loaded.post(f"{V1}/stats", json={"text": text}).json()["result"], "errors": [], "turns": 6})
    discard = said["panels"][3]
    assert discard["groups"][0]["tone"] == "neutral"  # a deck that wants to discard is not told it has a fault
    assert {"card": "Circle of Protection: Red", "why": plan_why(plan, "Circle of Protection: Red")} in discard["plan"]["items"]  # split at the last colon
    assert {"card": "Test Tower", "why": "no maximum hand size"} in discard["plan"]["items"]
    assert "lifts the hand limit" in discard["plan"]["note"]
    assert '<li key={`${it.card}-${it.why}`}' in TAB
    plain = loaded.post(f"{V1}/simulate", json={"text": DECK, "format": "commander", "samples": 0}).json()
    assert plain["result"]["discard_may_be_the_plan"] == []
    assert page_says({"simulate": plain, "stats": {"curve": {}, "lands": 0, "nonland": 0, "average_mana_value_nonland": 0}, "errors": [], "turns": 6})["panels"][3]["groups"][0]["tone"] == "danger"


def plan_why(plan, card_name):
    return next(p for p in plan if p.startswith(card_name + ": "))[len(card_name) + 2:]


@needs_node
def test_a_deck_of_three_colours_carries_the_servers_notice_the_page_shows_above_the_figures(loaded):
    """The page shows `colour_warning` as the server wrote it (views/deck_opening.jsx): no wording of its own to drift."""
    text = "Commander\n1 Test Commander\n\nDeck\n1 Blue Cantrip\n1 Banned Thing\n1 Fire // Ice\n" + "40 Test Mountain\n30 Dull Bear\n"
    sim = loaded.post(f"{V1}/simulate", json={"text": text, "format": "commander", "samples": 0}).json()
    assert "colour_warning" in sim["result"] and "does not check colours" in sim["result"]["colour_warning"]
    assert "{r.colour_warning}" in TAB and "colour_warning" not in LIB  # printed verbatim, not reworded


@needs_node
def test_the_states_use_the_servers_own_words(loaded):
    tiny = loaded.post(f"{V1}/simulate", json={"text": "3 Test Mountain", "format": "modern"})
    assert tiny.status_code == 400
    sim, stats = real_answers(loaded)
    # the 429 the person gets after 30 analyses a minute, with its Retry-After
    codes = [loaded.post(f"{V1}/legality", json={"text": DECK, "format": "commander"}) for _ in range(DECK_LIMIT + 2)]
    limited = next(c for c in codes if c.status_code == 429)
    retry = int(limited.headers["retry-after"])
    said = page_says({"simulate": sim, "stats": stats, "turns": 6, "errors": [
        {"status": 400, "message": tiny.json()["detail"]}, {"status": 429, "message": limited.json().get("detail", ""), "retryAfter": retry},
        {"status": 0, "message": "Network error: Failed to fetch"}]})
    few, limit, other = said["problems"]
    assert few["kind"] == "toofew" and few["quote"] == tiny.json()["detail"] and few["title"] == "Not enough cards to play 6 turns."
    assert limit["kind"] == "limit" and limit["seconds"] == retry >= 1 and f"allows {DECK_LIMIT} a minute" in limit["detail"]  # the number is the server's limit
    assert other["kind"] == "other" and other["detail"] == "Network error: Failed to fetch"


@needs_node
def test_a_seed_the_answer_reports_is_one_the_page_can_send_back(loaded):
    """'Play again' picks a new seed and the figures follow from it; the seed in the 'As played' line repeats a run exactly (#392)."""
    first, _ = real_answers(loaded)
    again, _ = real_answers(loaded, seed=first["result"]["seed"])
    assert again["result"] == first["result"]
    other, _ = real_answers(loaded, seed=first["result"]["seed"] + 1)
    assert other["result"]["seed"] != first["result"]["seed"] and other["result"]["per_turn"] != first["result"]["per_turn"]
    assert 0 <= first["result"]["seed"] <= 2**31 - 1


def test_the_tab_sits_after_stats_the_stats_tab_links_to_it_and_the_curve_is_one_component():
    assert "['stats', 'Stats'], ['opening', 'Opening turns'], ['legality', 'Legality']" in PAGE
    assert "{tab === 'opening' && <DeckOpening text={text} title={deck.title} format={format || 'commander'} setFormat={setFormat} />}" in PAGE
    assert "See how this curve plays: Opening turns" in PAGE and "onClick={onOpening}" in PAGE and "setTab('opening')" in PAGE
    assert PAGE.count("<DeckCurve ") == 1 and TAB.count("<DeckCurve ") == 1  # the Stats tab and the new tab draw the same component
    assert "role=\"img\" aria-label={c.label}" in PAGE and "Show the curve as a table" in PAGE


def test_the_tab_asks_the_server_for_everything_and_shows_the_states_the_design_names():
    assert "VaultApi.deckSimulate(text, { format, on_the_play: onPlay, turns, games: Sim.GAMES, samples: Sim.SAMPLES, seed })" in TAB
    assert "VaultApi.deckStats(text)" in TAB  # the curve comes from the stats call, in parallel
    assert "format: r.format" in TAB and "Sim.deckLine(title, deckOverview)" in TAB
    assert "Math.random" in TAB and TAB.count("Math.random") == 1  # only the new seed; no figure is computed in the browser
    for needle in ('role="status"', 'role="alert"', "aria-pressed={onPlay}", "aria-busy=", "<details className=\"ds-game\" open={open}>", "What this simulation does not do",
                   "r.assumptions.map", "Try again", "Sample games, turn by turn", "The headline figures", "The odds, turn by turn", "{r.colour_warning}"):
        assert needle in TAB, needle
    assert "open={i === 0}" in TAB  # the first game open, the others closed
    assert "VaultDeckSim.provenance" in TAB and "<SimProvenance p={answer.provenance && answer.provenance[0]} />" in TAB
    code = "\n".join(line for line in TAB.splitlines() if not line.lstrip().startswith("//"))
    assert "toFixed" not in code and "Math.round" not in code and "sqrt" not in code  # the page formats nothing itself


def test_the_new_files_are_wired_into_the_build_and_the_page():
    assert "'views/deck_opening.jsx'" in (ROOT / "web" / "build.mjs").read_text(encoding="utf-8")
    assert '<script src="lib/deck_sim.js"></script>' in (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert "deckSimulate:" in (PUBLIC / "lib" / "api.js").read_text(encoding="utf-8")


def test_the_phone_rules_are_inside_a_phone_media_query_and_the_new_css_is_before_the_phone_section():
    css = (PUBLIC / "layout.css").read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = css.index("/* ==== Phone layout")
    assert 0 < css.index(".ds-turns td::before") < marker  # before the phone section, but inside its own media query:
    block_start = css.rindex("@media (max-width: 760px)", 0, css.index(".ds-turns td::before"))
    assert css.index("/* ---- Deck page: Opening turns") < block_start
    assert re.search(r"\.ds-controls \.btn \{ min-height: 44px; \}", css) and ".ds-field .select { width: auto; min-height: 44px; }" in css
    assert "display: none; }" in css[css.index(".ds-turns td.ds-none"):css.index(".ds-turns td.ds-none") + 80]  # an empty Notes cell is dropped on a phone
