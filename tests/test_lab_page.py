"""The Lab page (#164, docs/lab-design.md task 4): its wording is public/lib/lab.js, its layout public/views/lab.jsx.

- Every counter of the decision strip equals the field of its section's response it reads ("a test compares the two"): the real
  answers of the Lab's routes go through the page's own code (tests/js/lab_page_driver.mjs).
- The old sections (spend by month, colour, type and curve, a type's priciest cards, stockpiles) are gone, and nothing links to them.
- A shared collection has no Lab: no navigation entry, and the routes answer 404 (also tests/test_lab_server.py).
- Every state of the design is in the page, and the page asks for each section's data from the routes the design names.
"""

import json
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.test_deck_independence import set_prices
from tests.test_lab_server import me, put, row, save  # noqa: F401 - fixtures and helpers
from vault.models import CollectionValue, User

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
LAB = (PUBLIC / "views" / "lab.jsx").read_text(encoding="utf-8")
LIB = (PUBLIC / "lib" / "lab.js").read_text(encoding="utf-8")
APP = (PUBLIC / "app.jsx").read_text(encoding="utf-8")
API = (PUBLIC / "lib" / "api.js").read_text(encoding="utf-8")
V1 = "/api/v1"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")


def money(v: float) -> str:
    return f"${abs(v):,.2f}"


def signed(v: float) -> str:
    return ("−" if v < 0 else "+") + money(v)


def answers(client) -> dict:
    out = {"overlap": client.get(f"{V1}/decks/overlap"), "spare": client.get(f"{V1}/collection/spare"),
           "pnl": client.get(f"{V1}/collection/pnl"), "history": client.get(f"{V1}/collection/history")}
    for name, res in out.items():
        assert res.status_code == 200, (name, res.text)
    return {k: v.json() for k, v in out.items()}


def page_says(data: dict, stale: bool = False) -> dict:
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "lab_page_driver.mjs")], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         input=json.dumps({**data, "stale": stale}))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture
def lab(me, app):
    """Four decks that all want The World Tree (two owned), two lack a card; spare copies, a winner, a loser."""
    put(me, row(4, "Sol Ring", "1", paid="1.00", market="3.00"), row(2, "The World Tree", "2", paid="2.00", market="5.00"),
        row(2, "Rhystic Study", "3", paid="30.00", market="10.00"), row(1, "Mana Crypt", "4", paid="50.00", market="100.00"),
        row(2, "Mystery Card", "5", market=""))
    set_prices(app, {"The World Tree": 5.0, "Missing Card": 2.5})
    for name in ("A", "B", "C", "D"):
        save(me, name, "1 The World Tree\n1 Sol Ring" + ("\n1 Missing Card" if name in "CD" else ""))
    with app.state.db.sessions() as db:
        user_id = db.query(User.id).scalar()
        for days, market in ((30, 120.0), (0, 140.0)):
            db.merge(CollectionValue(user_id=user_id, day=date.today() - timedelta(days=days), market_usd=market, cost_usd=0.0,
                                     copies=11, priced_copies=9))
        db.commit()
    return me


@needs_node
def test_every_counter_equals_the_field_of_its_sections_answer(lab):
    data = answers(lab)
    said = page_says(data)
    summary = data["overlap"]["summary"]
    assert summary["decks_needing_purchase"] == 2 and summary["cards_to_buy"] == 2  # the data is what the test thinks it is
    assert said["buy"]["headline"] == f"{summary['decks_needing_purchase']} decks need a purchase"
    assert said["buy"]["detail"] == f"finish all: {money(summary['finish_all_cost'])}"
    spare = data["spare"]["summary"]
    assert spare["names"] >= 2 and spare["unpriced_copies"] == 2
    assert said["sell"]["headline"] == f"{spare['names']} cards you could sell"
    assert said["sell"]["detail"] == f"spare value {money(spare['market_value'])}, 2 copies have no price"
    pnl = data["pnl"]["summary"]
    assert said["pnl"]["headline"] == f"Biggest known loss {signed(pnl['biggest_loss']['gain'])}"
    assert said["pnl"]["detail"] == f"biggest gain {signed(pnl['biggest_gain']['gain'])}"
    assert said["spareValue"].startswith(money(spare["market_value"]))
    # the coverage line says exactly the server's counts
    assert said["coverage"] == (f"Based on {pnl['covered_copies']} of {pnl['total_copies']} copies; "
                                f"{pnl['unknown_cost_copies']} have no price paid.")
    assert said["hidden"] is None
    assert said["history"].startswith("Market value ") and money(data["history"]["summary"]["market_end"]) in said["history"]
    assert said["action"] == {"kind": "losers" if pnl["net_gain"] < 0 else "spare", "label": "Show losers" if pnl["net_gain"] < 0 else "Show spare copies"}


@needs_node
def test_stale_prices_date_every_price_and_total(lab):
    said = page_says(answers(lab), stale=True)
    assert said["buy"]["detail"].startswith("finish all: $") and "(3 Oct)" in said["buy"]["detail"]
    assert "(3 Oct)" in said["sell"]["detail"] and "(3 Oct)" in said["pnl"]["detail"] and "(3 Oct)" in said["spareValue"]


@needs_node
def test_no_decks_asks_for_a_deck_and_says_there_is_nothing_to_decide_to_buy(me):
    put(me, row(2, "Sol Ring", "1", paid="1.00", market="3.00"))
    said = page_says(answers(me))
    assert said["buy"] == {"quiet": True, "headline": "Nothing to decide", "detail": "No decks saved yet"}
    assert said["sell"]["headline"] == "Needs decks first"
    assert said["headline"]["kind"] == "no_decks" and said["headline"]["text"].startswith("Save a deck to see whether your decks can all be built")
    assert said["pnl"]["headline"].startswith("Biggest known")  # profit and loss still shows without decks


@needs_node
def test_no_prices_paid_hides_profit_and_loss_with_the_servers_reason_and_offers_the_spare_action(me):
    put(me, row(2, "Sol Ring", "1", market="3.00"), row(1, "Mana Crypt", "2", market="100.00"))
    save(me, "A", "1 Sol Ring")
    data = answers(me)
    said = page_says(data)
    assert data["pnl"]["summary"]["reason"] == "no price paid is known"
    assert said["hidden"] == "No profit and loss to show: no price paid is known."
    assert said["pnl"] == {"quiet": True, "headline": "Nothing to decide", "detail": "No price paid is known"}
    assert said["action"] == {"kind": "spare", "label": "Show spare copies"}  # no known cost: only the market line, the action is the spare list


@needs_node
def test_all_decks_standing_alone_says_so_in_one_line(me):
    put(me, row(2, "Sol Ring", "1", market="3.00"))
    save(me, "A", "1 Sol Ring")
    save(me, "B", "1 Sol Ring")
    said = page_says(answers(me))
    assert said["headline"] == {"kind": "all_stand", "text": "All 2 decks can be built at the same time from what you own."}
    assert said["buy"]["headline"] == "Nothing to decide" and said["buy"]["detail"] == "2 decks stand on their own"


def test_the_old_sections_are_gone_and_nothing_links_to_them():
    for gone in ("SpendChart", "Acquisition spend by month", "Biggest stockpiles", "Color, type and curve", "Mana value distribution",
                 "most_copies", "api.breakdowns", "api.valuation", "api.stats", "typeFocus", "Insights"):
        assert gone not in LAB, gone
    assert 'data-screen-label="05 Lab"' in LAB and '<h1 className="h1">Lab</h1>' in LAB  # the page says Lab (decision 1)
    assert "Insights" not in APP


def test_a_shared_collection_has_no_lab_in_the_navigation_and_the_page_says_so():
    assert "{!viewing && <button data-nav=\"lab\"" in APP
    assert "readOnly={!!viewing}" in APP.split("route.view === 'lab'", 1)[1][:400]
    assert "A shared collection has no Lab." in LAB and "if (readOnly) return <LabShared />" in LAB
    css = (PUBLIC / "layout.css").read_text(encoding="utf-8")
    assert "nth-child" not in css.split("/* ==== Phone layout", 1)[1].split("Navigation", 1)[1].split("Account actions", 1)[0]  # icons follow data-nav


def test_the_page_has_every_state_of_the_design_and_reads_the_routes_it_names():
    for state in ("function LabEmpty", "Import your collection to see what to buy or sell.", "Import a file", "function LabOffline",
                  "Add a deck", "Save a deck to see whether your decks can all be built at once", "no_decks",
                  "isStale", "Export collection", "Copy shopping list", "role=\"tablist\"", "aria-selected", "role=\"tabpanel\"", "role=\"alert\""):
        assert state in LAB or state in LIB, state
    assert "(${age} days old)" in LIB and "Prices: Scryfall" in LIB
    for call in ("api.spare(", "api.pnl('winners'", "api.pnl('losers'", "api.historySince(", "window.VaultApi.overlap()", "window.VaultApi.overlapPurchases(",
                 "window.VaultApi.overlapText()", "api.card(", "api.follow(", "api.topPrinting("):
        assert call in LAB, call
    for route in ("'/spare'", "'/pnl'", "'/history'", "/decks/overlap/purchases?format=text"):
        assert route in API, route
    assert "const Text = window.VaultLab" in LAB


def test_the_browser_computes_nothing_but_formatting():
    """The page and its wording never sum or compare collection data: the figures are the server's fields."""
    for source in (LAB, LIB):
        assert not re.search(r"\.(reduce|sort)\(", source), "the lists keep the server's order and no figure is summed here"
    assert "net_gain" in LIB and "market_change" in LIB  # the action and the chart's line read the server's summaries
