"""The Ideas page (#163, docs/deck-ideas-lab-design.md, tasks 2 and 3): its wording is public/lib/ideas.js, its layout public/views/ideas.jsx.

- Every figure and word the page shows is the server's: the real answers of `GET /decks/{id}/ideas` and `/ideas/alternatives` go through the
  page's own code (tests/js/ideas_page_driver.mjs) and each number is compared with the field it reads, in the design's five states (the start,
  a deck with missing cards, a missing card with alternatives, one with none, a fully covered deck), a borrowed card with its Move, and the
  mixed shortage.
- The Graph is gone: its seven modes, graph.jsx, the cytoscape script, its navigation entry and the styles only it used; nothing links to a
  removed mode, `#/graph` goes to `#/ideas`, and the help, credits, privacy notice and analytics names say Ideas.
- Navigation, the contract with the deck page (`?swap=`), Clear, Esc and Back, the weight rule (thumbnails, lazy, windowed lanes) and the
  keyboard and labels are in the page.
The page used in a real browser (screenshots at 1400 and 390 px, the phone check, the budget): scripts/ideas_evidence.py, results in the pull request."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_deck_ideas import DECK, OWNED, alternatives, ideas, lab, lane_cards, own, save  # noqa: F401 - fixtures and helpers
from tests.test_deck_independence import alice  # noqa: F401 - fixture
from vault import deck_tools as dt
from vault.deck_ideas import LANE_PAGE

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
IDEAS = (PUBLIC / "views" / "ideas.jsx").read_text(encoding="utf-8")
LIB = (PUBLIC / "lib" / "ideas.js").read_text(encoding="utf-8")
APP = (PUBLIC / "app.jsx").read_text(encoding="utf-8")
API = (PUBLIC / "lib" / "api.js").read_text(encoding="utf-8")
CSS = (PUBLIC / "layout.css").read_text(encoding="utf-8")
INDEX = (PUBLIC / "index.html").read_text(encoding="utf-8")
BUILD = (ROOT / "web" / "build.mjs").read_text(encoding="utf-8")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")


def page_says(answers: dict) -> dict:
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "ideas_page_driver.mjs")], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         input=json.dumps(answers))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def says(client, deck_id, card=None, **params) -> dict:
    return page_says({"ideas": ideas(client, deck_id, **params), "alternatives": alternatives(client, deck_id, card) if card else None})


def rows(said: dict, lane_title: str) -> dict:
    return {r["card"]: r for lane in said["lanes"] if lane["title"].startswith(lane_title) for r in lane["rows"]}


# -- the five states: each number is the server's field -----------------------------------------------------------------------------

@needs_node
def test_a_deck_with_a_missing_card_says_what_the_server_counted(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    body = ideas(lab, deck)
    said = says(lab, deck)
    s = body["summary"]
    assert said["headline"] == {"covered": f"{s['covered']} of {s['copies']} covered", "missing": f"{s['missing']} missing", "borrowed": f"{s['borrowed']} borrowed"}
    assert s["missing"] == 1 and said["state"] == "deck" and said["covered"] is False
    assert said["facts"] == ["commander", "Test Commander", f"{body['deck']['overview']['cards']} cards"]
    assert [l["title"] for l in said["lanes"]] == [f"{l['label']} ({l['cards']})" for l in body["lanes"] if l["total"]]  # empty lanes are not drawn
    ramp = rows(said, "Ramp")
    assert ramp["Cultivate"] == {"card": "Cultivate", "tone": "missing", "word": "missing", "note": "1 to buy", "label": "Cultivate, missing, 1 to buy, Ramp lane"}
    assert ramp["Sol Ring"]["tone"] == "owned" and ramp["Sol Ring"]["word"] == "owned"
    assert said["decisions"] == ["Cultivate"] and said["borrowed"] == []
    # the lanes keep the server's order: cards that need a decision first
    assert [r["card"] for lane in said["lanes"] if lane["title"].startswith("Ramp") for r in lane["rows"]][0] == "Cultivate"


@needs_node
def test_every_lane_card_is_one_row_and_the_page_adds_nothing(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    body = ideas(lab, deck, limit=LANE_PAGE)
    said = page_says({"ideas": body, "alternatives": None})
    assert sum(len(lane["rows"]) for lane in said["lanes"]) == sum(l["count"] for l in body["lanes"]) == body["summary"]["cards"]


@needs_node
def test_a_fully_covered_deck_is_the_covered_state(lab, app):
    own(app, {**OWNED, "Cultivate": 1})
    deck = save(lab, "Ramp Deck", DECK)
    said = says(lab, deck)
    assert said["covered"] is True and said["state"] == "covered" and said["headline"]["missing"] == "0 missing" and said["decisions"] == []
    assert all(r["tone"] == "owned" for lane in said["lanes"] for r in lane["rows"])  # the lanes stay, so the deck can still be explored
    assert "Every card is in your collection and no copy is borrowed. Nothing to decide here." in LIB


@needs_node
def test_a_missing_card_with_alternatives_lists_them_with_the_servers_numbers(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK)
    save(lab, "Rock Deck", "1 Mind Stone")  # the only Mind Stone is that deck's: a borrowed alternative
    body = alternatives(lab, deck, "Cultivate")
    said = says(lab, deck, "Cultivate")
    assert said["target"]["state"] == "alternatives" and said["target"]["heading"] == "Owned alternatives"
    assert said["target"]["role"] == "Role: ramp (core)"
    t = body["card"]
    assert said["target"]["allocation"] == f"The deck lists {t['need']}; this deck holds {t['gets']}; {t['not_owned']} to buy."
    assert said["target"]["buy"] == "Buy $0.40 (Scryfall, 4 Oct)" and t["buy"]["unit_price"] == 0.4
    got = {a["card"]: a for a in said["alternatives"]}
    by_server = {a["card"]: a for a in body["items"]}
    assert set(got) == set(by_server) and "Arcane Signet" in got and "Mind Stone" in got
    signet, stone = got["Arcane Signet"], got["Mind Stone"]
    assert signet["owned"] == f"{by_server['Arcane Signet']['copies_owned']} owned" and signet["badges"] == [] and signet["move"] is None and signet["buy"] is None
    assert signet["swap"] == f"#/decks/{deck}?swap=" + __import__("urllib.parse", fromlist=["quote"]).quote('{"cut":["Cultivate"],"add":["Arcane Signet"]}', safe="")
    # borrowed: the other deck's name, Move from it, and what a copy would cost; the design offers no Swap for it
    assert stone["badges"] == [{"kind": "borrowed", "text": "borrowed from Rock Deck"}]
    assert stone["move"] == "Move from Rock Deck" and stone["buy"] == "Buy $1.10 (Scryfall, 4 Oct)"
    assert by_server["Mind Stone"]["move"]["quantity"] == 1 and by_server["Mind Stone"]["borrowed"] is True
    assert '!alt.borrowed && inDeck' in IDEAS  # Swap into the deck only for a copy no other deck holds
    for a in got.values():
        assert a["thumb"].replace("/small/", "/normal/") == by_server[a["card"]]["image"]["normal"] and "/small/" in a["thumb"]  # thumbnails only


@needs_node
def test_a_missing_card_with_no_alternative_shows_the_servers_message(lab, app):
    own(app, OWNED)
    deck = save(lab, "Ramp Deck", DECK + "1 Blasphemous Act\n")
    body = alternatives(lab, deck, "Blasphemous Act")
    said = says(lab, deck, "Blasphemous Act")
    assert body["items"] == [] and body["reason"] == "none_found"
    assert said["target"]["state"] == "no-alternative" and said["alternatives"] == []
    assert "own nothing else that does this job" in body["message"] and "a.message ||" in IDEAS  # the page shows the server's message
    assert "See upgrades on the deck page" in IDEAS and "Open on Scryfall" in IDEAS and "Buy for" in IDEAS.replace("Text.buyLabel(t.buy).replace(/^Buy /, 'Buy for ')", "Buy for")


@needs_node
def test_a_card_another_deck_holds_is_borrowed_and_move_is_offered_only_with_a_donor_copy(lab, app):
    own(app, {**OWNED, "Chaos Warp": 1})
    save(lab, "Tiny Deck", "1 Chaos Warp")
    big = save(lab, "Big Deck", DECK + "1 Chaos Warp\n")
    said = says(lab, big)
    warp = rows(said, "Removal")["Chaos Warp"]
    assert warp["tone"] == "borrowed" and warp["word"] == "borrowed" and warp["note"] == "held by Tiny Deck"
    assert said["borrowed"] == ["Chaos Warp"] and said["headline"]["borrowed"] == "1 borrowed"
    assert rows(said, "Ramp")["Cultivate"]["tone"] == "missing"  # owned nowhere: only bought, no Move
    body = ideas(lab, big)
    assert lane_cards(body, "removal")["Chaos Warp"]["move"]["from_deck"]["name"] == "Tiny Deck" and lane_cards(body, "ramp")["Cultivate"]["move"] is None
    assert "rowCard && rowCard.move" in IDEAS  # the target's Move shows only when the server gave one


@needs_node
def test_the_mixed_shortage_says_both_halves(lab, app):
    from datetime import datetime, timezone

    from tests.test_deck_independence import stamp

    own(app, {"Rare Thing": 1})
    first = save(lab, "First", "2 Rare Thing")
    second = save(lab, "Second", "2 Rare Thing")
    stamp(app, first, datetime(2026, 10, 2, tzinfo=timezone.utc))
    stamp(app, second, datetime(2026, 10, 1, tzinfo=timezone.utc))
    said = says(lab, second)
    thing = rows(said, "Other")["Rare Thing"]
    assert thing["tone"] == "missing" and thing["note"] == "1 to buy, 1 held by First"
    assert said["headline"]["missing"] == "1 missing" and said["headline"]["borrowed"] == "1 borrowed"  # counted under both
    mine = rows(says(lab, first), "Other")["Rare Thing"]
    assert mine["tone"] == "partial" and mine["word"] == "1 of 2" and mine["note"] == "1 to buy"


def test_the_format_choices_are_the_servers_formats():
    page = re.search(r"FORMATS = \[(.*?)\];", LIB, re.S).group(1)
    assert tuple(re.findall(r"'([a-z]+)'", page)) == dt.FORMATS


# -- the page's behaviour -----------------------------------------------------------------------------------------------------------------

def test_the_navigation_says_ideas_and_a_shared_collection_has_none():
    assert "data-nav=\"ideas\"" in APP and ">Ideas</button>" in APP and "{!viewing && <button data-nav=\"ideas\"" in APP
    assert "A shared collection has no Ideas." in IDEAS and "if (readOnly) return <IdeasShared />" in IDEAS
    assert "VAULT_VIEWS = ['dashboard', 'browse', 'sets', 'decks', 'lab', 'ideas', 'valuation', 'help']" in APP
    assert "<Ideas data={data} route={route}" in APP and "window.Ideas = Ideas" in IDEAS
    assert "'views/ideas.jsx'," in BUILD and BUILD.index("views/ideas.jsx") < BUILD.index("'app.jsx'")
    assert '<script src="lib/ideas.js"></script>' in INDEX and INDEX.index("lib/ideas.js") < INDEX.index("app.bundle.js")
    assert '.nav button[data-nav="ideas"]::before' in CSS


def test_ideas_says_that_it_reads_decks_not_the_scope_like_the_lab_does():
    assert "route.view === 'ideas' && window.scopeActive(scope)" in APP
    assert "reads your saved decks against your whole inventory, not the bucket or tag you chose" in APP
    assert "Lab, Ideas and Decks read the" in APP and "VAULT_SCOPED = new Set(['browse', 'dashboard', 'sets', 'setdetail', 'valuation'])" in APP


def test_a_graph_address_goes_to_ideas_and_the_route_carries_the_deck_card_and_swap():
    assert "if (view === 'graph' || view === 'ideas') return window.VaultIdeas.ideasRoute(arg, arg2)" in APP
    assert "window.VaultIdeas.ideasHash(route)" in APP and "route.swap ? '?' + window.VaultIdeas.swapQuery(route.swap)" in APP
    assert "window.VaultIdeas.parseSwap(new URLSearchParams(search || '').get('swap'))" in APP and "return { view: 'decks', deckId: arg" in APP
    # the deck page is not touched by this view: Swap into the deck is a link to the contract's address
    assert "Text.swapHash(deckId, [name], [alt.card])" in IDEAS and "swap: { cut: [cardName], add: [] }" in IDEAS


def test_clear_esc_and_back_each_step_out_and_leave_no_stale_selection():
    assert ">Clear</button>" in IDEAS and "const clear = () => onGo(Text.ideasRoute())" in IDEAS
    assert "e.key !== 'Escape'" in IDEAS and "stepRef.current()" in IDEAS and "(cardName ? onUp(Text.ideasRoute(deckId)) : clear())" in IDEAS
    assert "if (zoomRef.current) { e.preventDefault(); setZoom(null); return; }" in IDEAS  # Esc closes the enlarged image first
    assert "document.querySelector('.drawer, [role=\"dialog\"]')" in IDEAS  # the card drawer and the account panel close themselves
    assert "onUp={stepTo}" in APP and "history.state.from === vaultHashFor(raw)) history.back()" in APP  # the panel's Back is the browser's Back
    assert "from: vaultHashFor(route)" in APP
    assert ">Back</button>" in IDEAS and "onClick={onUp}" in IDEAS
    assert "key={route.deckId}" in IDEAS  # another deck starts with no selection and no open lanes


def test_the_page_is_light_thumbnails_only_lazy_and_long_lanes_are_windowed():
    assert 'loading="lazy"' in IDEAS and "Text.thumbUrl(image.normal)" in IDEAS and 'width="40" height="56"' in IDEAS
    assert "thumbUrl = (url) => (typeof url === 'string' ? url.replace('/normal/', '/small/') : url)" in LIB
    assert "limit: Text.ALT_PAGE" in IDEAS and "needsWindow(cards.length)" in IDEAS and "windowRange(" in IDEAS and "WINDOW_AT = 40" in LIB
    assert "MAX_IMAGES = 12" in LIB and "ALT_PAGE = 10" in LIB
    assert "<img" not in IDEAS.replace('<img src={Text.thumbUrl(image.normal)}', "").replace("<img src={card.normal}", "")  # no image in the lanes
    for heavy in ("cytoscape", "requestAnimationFrame(tick", "forceSimulation", "setInterval", "animation"):
        assert heavy not in IDEAS and heavy not in LIB, heavy  # no physics, no animation


def test_the_larger_image_opens_only_on_a_tap_and_carries_its_credit():
    assert "onZoom({ name, normal: image.normal, artist: image.artist })" in IDEAS and 'role="dialog" aria-modal="true"' in IDEAS
    assert "Illustrated by" in IDEAS and "image via" in IDEAS and "© Wizards of the Coast" in IDEAS and "Art: " in IDEAS
    assert "window.useDialogFocus(box)" in IDEAS  # focus goes in, Tab stays in, focus returns to the thumbnail


def test_lanes_and_cards_are_reachable_by_keyboard_and_named():
    assert "aria-expanded={open}" in IDEAS and "aria-controls={id}" in IDEAS and "<button type=\"button\" className={`ideas-row" in IDEAS
    assert "aria-label={Text.rowLabel(c, laneLabel || c.laneLabel)}" in IDEAS and "aria-current={on ? 'true' : undefined}" in IDEAS
    assert "e.key !== 'ArrowDown' && e.key !== 'ArrowUp'" in IDEAS and 'role="group"' in IDEAS
    assert 'aria-labelledby="ideas-panel-h"' in IDEAS and "document.getElementById('ideas-panel-h')" in IDEAS  # focus goes to the panel's heading
    assert 'role="status"' in IDEAS and 'role="alert"' in IDEAS and "aria-pressed={filterNow === 'missing'}" in IDEAS
    block = CSS.split(".ideas-lane-toggle {", 1)[1]
    assert ".ideas-fine summary { display: flex; align-items: center; min-height: 44px; }" in block and "min-height: 48px" in block


def test_the_page_reads_the_two_routes_and_the_combos_only_on_request():
    assert "deckIdeas: (id, params)" in API and "'/ideas' + query(params)" in API and "'/ideas/alternatives' + query({ card, ...params })" in API
    assert "window.VaultApi.deckIdeas(deckId)" in IDEAS and "window.VaultApi.deckAlternatives(deckId, cardName, { format, limit: Text.ALT_PAGE })" in IDEAS
    assert "window.VaultApi.follow(next)" in IDEAS and "include_combos: 'true'" in IDEAS and "Show combos you already own" in IDEAS
    assert "window.VaultApi.decks(true)" in IDEAS
    assert "coarse roles" in LIB and "COARSE_NOTE" in IDEAS and "the Vault contacts no shop" in LIB


def test_the_browser_computes_nothing_but_formatting():
    for source in (IDEAS, LIB):
        assert not re.search(r"\.(reduce|sort)\(", source), "the lanes keep the server's order and no figure is summed here"
    assert "Math.round((s.have / s.need) * 100)" in LIB  # the one ratio: the width of a bar, from the deck list's own counts


# -- the Graph is gone (task 3) ---------------------------------------------------------------------------------------------------------------

def test_the_graph_its_script_its_navigation_entry_and_its_styles_are_gone():
    assert not (PUBLIC / "views" / "graph.jsx").exists() and "graph.jsx" not in BUILD
    assert "cytoscape" not in INDEX.lower() and "unpkg.com/cytoscape" not in INDEX
    bundle = (PUBLIC / "app.bundle.js").read_text(encoding="utf-8")
    for gone in ("GraphView", "cytoscape", "Color galaxy", "Type roster", "Set clusters", "Affinity web", "Deck map", "Mana / price", "graphTopN", "Graph default top-N"):
        assert gone not in bundle and gone not in APP and gone not in IDEAS, gone
    assert 'data-nav="graph"' not in APP and ">Graph<" not in APP and "view === 'graph'" not in APP.replace("if (view === 'graph' || view === 'ideas')", "")
    for dead in (".graph-stage", ".graph-legend", ".m-matrix", ".m-stack", ".m-3col", "button.pip", 'data-nav="graph"'):
        assert dead not in CSS, dead
    assert "'graph'" not in APP.replace("if (view === 'graph' || view === 'ideas')", "")  # no view, no landing choice named graph


def test_nothing_links_to_a_removed_mode_and_the_names_that_listed_views_say_ideas():
    for path in list(PUBLIC.glob("*.html")) + list((PUBLIC / "views").glob("*.jsx")) + list((PUBLIC / "lib").glob("*.js")) + [PUBLIC / "app.jsx", ROOT / "web" / "analytics" / "url.mjs"]:
        text = path.read_text(encoding="utf-8")
        assert "#/graph" not in text, path.name
        assert not re.search(r"\bGraph\b", text) or path.name == "privacy.html", path.name
    credits = (PUBLIC / "credits.html").read_text(encoding="utf-8")
    assert "Cytoscape" not in credits and "cytoscape" not in (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    assert "lab, ideas, valuation or help" in (PUBLIC / "privacy.html").read_text(encoding="utf-8")
    assert "'lab', 'ideas', 'valuation'" in (ROOT / "web" / "analytics" / "url.mjs").read_text(encoding="utf-8")
