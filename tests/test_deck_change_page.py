"""The deck page's change flow (#163 task 0, docs/deck-ideas-lab-design.md): its wording is public/lib/deck_change.js, its layout
public/views/deck_change.jsx.

- The sentences the person reads equal the fields of the server's real answers (the validator with `include_text`, the coverage of the
  list it returns), run through the page's own code (tests/js/deck_change_driver.mjs).
- What the check says is the server's: every problem's detail is shown as the server wrote it.
- Nothing is applied without the confirmation click: the page writes in one place, the confirmation's handler, which is offered only for
  a check of exactly the cards chosen.
- A swap in the address (the Ideas view's `#/decks/7?swap={"cut":[...],"add":[...]}`) is read into the route and written back unchanged.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

from tests.test_deck_api import VALID, loaded  # noqa: F401 - fixture and the 100-card deck
from tests.test_deck_independence import own

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
FLOW = (PUBLIC / "views" / "deck_change.jsx").read_text(encoding="utf-8")
DECK_PAGE = (PUBLIC / "views" / "deck.jsx").read_text(encoding="utf-8")
V1 = "/api/v1"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")


def money(v: float) -> str:
    return f"${v:.2f}"


def page_says(data: dict) -> dict:
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "deck_change_driver.mjs")], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, input=json.dumps(data))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def answers(client, deck_id, cuts, adds):
    checked = client.post(f"{V1}/decks/validate-changes", json={"deck_id": deck_id, "format": "commander", "cuts": cuts, "adds": adds, "include_text": True})
    assert checked.status_code == 200, checked.text
    result = checked.json()["result"]
    cover = client.post(f"{V1}/decks/coverage", json={"text": result["deck_text"]})
    assert cover.status_code == 200, cover.text
    return result, cover.json()


@needs_node
def test_every_sentence_equals_the_field_of_the_answer_it_reads(loaded, app):
    own(app, {"Cheap Ramp": 1, "Dull Bear": 1}, email="dev@localhost")
    deck_id = loaded.post(f"{V1}/decks", json={"name": "Sliver Swarm", "text": VALID}).json()["id"]
    cuts, adds = ["Dull Bear"], ["Cheap Ramp", "Pricey Ramp", "Blue Cantrip"]
    result, coverage = answers(loaded, deck_id, cuts, adds)
    assert {i["kind"] for i in result["issues"]} >= {"color_identity", "result_deck_size"}  # the data is what the test thinks it is
    swap = json.dumps({"cut": cuts, "add": adds})
    said = page_says({"deck": "Sliver Swarm", "cuts": cuts, "adds": adds, "validation": result, "coverage": coverage,
                      "hashes": ["#/decks/7", "#/decks/7?swap=" + quote(swap, safe=""), "#/decks/7?swap=" + quote(swap, safe="").replace("%20", "+")]})
    n = len(result["issues"])
    assert said["verdict"] == {"good": False, "text": f"The check found {n} problem{'s' if n != 1 else ''}."}
    for shown, issue in zip(said["issues"], result["issues"]):  # the server's own words, in the server's order
        assert shown["detail"] == issue["detail"] and shown["card"] == issue["card"] and shown["label"]
    assert said["counts"] == f"The list would have {result['cards_after']} cards (cut {result['cuts']}, add {result['adds']})."
    assert said["cost"][0] == f"The cards to add cost {money(result['added_cost_usd'])}."
    assert any(f"{money(result['deck_cost_before_usd'])} to {money(result['deck_cost_after_usd'])}" in line for line in said["cost"])
    # what the person owns: the coverage of the new list, card by card
    by_name = {c["name"]: c for c in coverage["cards"]}
    assert said["owned"][0] == "Cheap Ramp: you own it" and by_name["Cheap Ramp"]["have"] >= by_name["Cheap Ramp"]["need"]
    assert said["owned"][1] == f"Pricey Ramp: you do not own it ({money(by_name['Pricey Ramp']['unit_price'])} to buy)"
    assert money(coverage["missing_cost"]) in said["toBuy"]
    assert said["confirm"] == "Save anyway: Cut 1 card, add 3 cards to Sliver Swarm" and said["blocked"] is None
    assert said["saved"] == "Saved. Cut 1 card, add 3 cards to Sliver Swarm. The earlier list is in the History tab."
    # the address: the swap the Ideas view writes is read as is, and written back as is
    bare, plain, plus = said["routes"]
    assert bare == {"view": "decks", "deckId": "7"} and said["rebuilt"][0] == "#/decks/7"
    assert plain == plus == {"view": "decks", "deckId": "7", "swap": swap} and said["rebuilt"][1] == "#/decks/7?swap=" + quote(swap, safe="")


@needs_node
def test_a_clean_check_says_so_and_offers_the_plain_button(loaded, app):
    own(app, {"Cheap Ramp": 1}, email="dev@localhost")
    deck_id = loaded.post(f"{V1}/decks", json={"name": "Sliver Swarm", "text": VALID}).json()["id"]
    cuts, adds = ["Dull Bear"], ["Cheap Ramp"]
    result, coverage = answers(loaded, deck_id, cuts, adds)
    assert result["valid"] is True
    said = page_says({"deck": "Sliver Swarm", "cuts": cuts, "adds": adds, "validation": result, "coverage": coverage, "hashes": []})
    assert said["verdict"] == {"good": True, "text": "The check found no problems."} and said["issues"] == []
    assert said["confirm"] == "Cut 1 card, add 1 card to Sliver Swarm" and said["blocked"] is None


@needs_node
def test_a_cut_that_is_not_in_the_deck_cannot_be_saved(loaded):
    deck_id = loaded.post(f"{V1}/decks", json={"name": "Sliver Swarm", "text": VALID}).json()["id"]
    result, coverage = answers(loaded, deck_id, ["Blue Cantrip"], [])
    assert [i["kind"] for i in result["issues"] if i["kind"] == "cut_not_in_deck"] == ["cut_not_in_deck"]
    said = page_says({"deck": "Sliver Swarm", "cuts": ["Blue Cantrip"], "adds": [], "validation": result, "coverage": coverage, "hashes": []})
    assert said["blocked"].startswith("Not in the deck: Blue Cantrip.")


def test_the_page_writes_in_one_place_and_only_for_a_check_of_the_cards_as_chosen():
    """Nothing is applied without the confirmation click."""
    assert FLOW.count("updateDeck(") == 1 and "VaultApi.updateDeck(deckId, deckName, current.result.deck_text)" in FLOW
    save = FLOW[FLOW.index("async function save()"):]
    save = save[:save.index("\n  }\n")]
    assert "if (!current || !current.result || saving) return;" in save  # a saved list only after a check of exactly these cards
    assert FLOW.count("save}") == 1 and re.search(r"<button[^>]*onClick=\{save\}", FLOW)  # the button that names the change, nowhere else
    assert FLOW.index('<div className="dc-step dc-confirm">') < FLOW.index("onClick={save}")  # offered in the step that follows the check's result
    assert FLOW.index("const view = current && current.result ? current.result : null;") < FLOW.index("{view && (", FLOW.index("Check this change"))  # and only once there is one
    assert "const current = check && check.key === key ? check : null;" in FLOW  # a different proposal has to be checked again
    code = "\n".join(line for line in FLOW.splitlines() if not line.lstrip().startswith("//"))
    assert "validate-changes" not in code and "PUT" not in code and "fetch(" not in code  # the requests are the client's (public/lib/api.js)
    assert FLOW.count("VaultApi.deckValidateChanges(") == 1 and FLOW.count("VaultApi.catalogCard(") == 2  # a card is only ever one the catalog found


def test_only_cards_the_catalog_found_can_be_added_and_only_a_saved_deck_of_the_person_has_the_flow():
    assert "setAdds((list) => (list.length >= V.MAX_ITEMS ? list : [...list, a.card.name]))" in FLOW  # the catalog's name for the card, never the typed text
    assert "if (a.card) found.push(a.card.name)" in FLOW  # a swap's adds too
    assert "{changing && source.saved && (" in DECK_PAGE and "{saved && source.saved && rows && (" in DECK_PAGE  # a pasted list or a shared deck has neither
    assert "source.saved && savedId" in DECK_PAGE  # nor does a swap in the address open it there


def test_the_flow_is_wired_into_the_build_and_the_page():
    assert "'views/deck_change.jsx'" in (ROOT / "web" / "build.mjs").read_text(encoding="utf-8")
    assert '<script src="lib/deck_change.js"></script>' in (PUBLIC / "index.html").read_text(encoding="utf-8")
    api = (PUBLIC / "lib" / "api.js").read_text(encoding="utf-8")
    assert "deckValidateChanges:" in api and "include_text: true" in api and "catalogCard:" in api
    assert "e.key === 'Escape'" in FLOW  # Esc closes it
