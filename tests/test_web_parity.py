"""The numbers the web page shows for a deck equal what the MCP tools say for the same deck (#91).

The page does not send what an assistant sends: it reads the deck itself (the Vault's Archidekt answer), merges a card
listed twice into one line and writes its own list (`deckListText` in public/views/deck.jsx) before it asks for stats,
legality and the buy list. So equal numbers are not automatic. This test runs the page's own code (the Node driver
tests/js/deck_page_driver.mjs: DeckSrc, VaultApi and the page's list builder) on the Vault's answer for an Archidekt
deck, then sends exactly what the page sent to the same routes and compares the answers with the MCP tools:

- `deck_stats`, `deck_legality`, `shopping_list`, `check_decklist` given the page's list as `text`;
- the same tools given the `deck_id` of the deck the page saved (what its Save button sends);
- the same tools given the `deck_id` of the deck an assistant saves with `import_deck_from_link` (a different list: its
  own writer, the sections kept), because that is how a person's assistant sees the same deck.
"""

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_agents import auth, call_tool, make_token
from tests.test_deck_api import CARDS, PRICES, TAGS, TODAY, oid
from twins.universe import Universe
from vault import catalog_sync as cs
from vault.app import create_app
from vault.config import Settings

ROOT = Path(__file__).resolve().parent.parent
V1 = "/api/v1"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")

# The collection: one card of the deck owned, two Test Mountains, so coverage and the buy list are not trivial.
OWNED_CSV = (b"Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET\r\n"
             b"c,1,0,Test Rock,TST,Test,3,NearMint,Normal,English,,,1.00,1.00,1.00\r\n"
             b"c,2,0,Test Mountain,TST,Test,2,NearMint,Normal,English,,,0.10,0.10,0.10\r\n")

# An Archidekt deck as people build them: a commander, a card on two rows (two printings of a land), categories, a
# sideboard and a maybeboard that are not played.
ARCHIDEKT_DECK = [(1, "Test Commander", None, None, "Commander"), (1, "Test Rock", None, None, "Ramp"), (1, "Test Burn"),
                  (1, "Dull Bear"), (1, "Fire // Ice"), (3, "Test Mountain"), (2, "Test Mountain", "tst", "2"),
                  (1, "Cheap Ramp", None, None, "Sideboard"), (1, "Blue Cantrip", None, None, "Maybeboard")]


@pytest.fixture
def parity(database_url):
    universe = Universe()
    settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True)
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as client:
        client.post("/api/auth/dev-login")
        with app.state.db.sessions() as db:
            cs.sync_oracle_cards(db, CARDS, today=TODAY)
            tags = [{"id": "t-ramp", "slug": "ramp", "label": "ramp", "parent_ids": [], "child_ids": [],
                     "taggings": [{"oracle_id": oid(n), "weight": "strong"} for n in TAGS["ramp"]]}]
            cs.sync_oracle_tags(db, tags)
            cs.sync_oracle_prices(db, [{"oracle_id": oid(n), "scryfall_id": f"p{n}", "usd": usd, "usd_foil": None, "eur": None,
                                        "day": TODAY, "source": "scryfall"} for n, usd in PRICES.items()])
            for name in ("oracle_cards", "oracle_tags", "oracle_prices"):
                cs.record_source(db, name, version=name + "-1", rows=1)
            db.commit()
        assert client.post(f"{V1}/imports", files={"file": ("export.csv", OWNED_CSV, "text/csv")}).status_code == 201
        deck = universe.archidekt.add_deck("Parity Deck", "ann", ARCHIDEKT_DECK)
        yield client, app, deck
    assert not universe.escapes, universe.escapes


def page_sends(client, deck_id: int) -> dict:
    """What the deck page does for an Archidekt link: the driver runs its code on the Vault's own answer."""
    answer = client.get(f"{V1}/archidekt/decks/{deck_id}?detail=cards")
    assert answer.status_code == 200, answer.text
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "deck_page_driver.mjs")], capture_output=True, text=True, timeout=60,
                         input=json.dumps({"archidekt": answer.json(), "url": f"https://archidekt.com/decks/{deck_id}/parity-deck"}))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def replay(client, requests: list[dict], route: str):
    sent = [r for r in requests if r["url"] == f"{V1}/decks/{route}"]
    assert len(sent) == 1, (route, [r["url"] for r in requests])
    res = client.post(sent[0]["url"], json=sent[0]["body"])
    assert res.status_code == 200, (route, res.text)
    return res.json(), sent[0]["body"]


def tool(bot, token, name, /, **args):
    result = call_tool(bot, token, name, **args)
    assert result["isError"] is False, (name, result["content"][0]["text"])
    return result["structuredContent"]


def strip(answer: dict) -> dict:
    """The numbers: an answer without its links and provenance blocks and the saved deck's id, name and where it came from: link, author, fetched_at (they name the
    request or the copy, not the deck's contents). Everything else, the overview included, must be equal."""
    out = {k: v for k, v in answer.items() if k not in ("provenance", "_links")}
    if isinstance(out.get("deck"), dict):
        out["deck"] = {k: v for k, v in out["deck"].items() if k not in ("id", "name", "url", "author", "fetched_at", "source", "credit")}
    return out


def played(answer: dict) -> dict:
    """strip(), without the informational counts of the sections that are not played (sideboard, maybeboard): a list that
    names them reports how many it saw, and that is the only thing allowed to differ from the page's list without them."""
    out = strip(answer)
    out = {**out, "result": {k: v for k, v in out.get("result", {}).items() if k != "by_section"}} if "result" in out else out
    if isinstance(out.get("deck"), dict) and isinstance(out["deck"].get("overview"), dict):
        overview = {k: v for k, v in out["deck"]["overview"].items() if k not in ("sideboard", "maybeboard", "companion")}
        out = {**out, "deck": {**out["deck"], "overview": overview}}
    return out


def test_the_page_builds_the_list_the_server_reads_as_the_deck(parity):
    client, _, deck = parity
    sent = page_sends(client, deck["id"])
    lines = sent["text"].splitlines()
    assert lines[:3] == ["Commander", "1 Test Commander", ""] and "Deck" in lines
    assert "5 Test Mountain" in lines  # two rows of the same card, one line
    assert not any("Cheap Ramp" in line or "Blue Cantrip" in line for line in lines)  # sideboard and maybeboard are not played
    assert sent["format"] == "commander"


def test_what_the_page_shows_equals_the_mcp_tools_for_the_same_deck(parity):
    client, app, deck = parity
    sent = page_sends(client, deck["id"])
    text, fmt = sent["text"], sent["format"]

    page = {"stats": replay(client, sent["requests"], "stats")[0], "legality": replay(client, sent["requests"], "legality")[0],
            "shopping": replay(client, sent["requests"], "shopping-list")[0], "coverage": replay(client, sent["requests"], "coverage")[0]}
    assert replay(client, sent["requests"], "legality")[1] == {"text": text, "format": fmt}  # the page asks about this list and format

    token = make_token(client, scopes=["read", "write"])
    with TestClient(app) as bot:
        # 1. The MCP tools given the page's list.
        by_text = {"stats": tool(bot, token, "deck_stats", text=text), "legality": tool(bot, token, "deck_legality", text=text, format=fmt),
                   "shopping": tool(bot, token, "shopping_list", text=text), "coverage": tool(bot, token, "check_decklist", text=text)}
        for key in page:
            assert strip(by_text[key]) == strip(page[key]), f"{key}: the page and the tool given its list disagree"

        # 2. The deck the page saved (its Save button), analysed by id.
        saved_body = next(r["body"] for r in sent["requests"] if r["url"] == f"{V1}/decks" and r["method"] == "POST")
        page_deck = client.post(f"{V1}/decks", json=saved_body).json()
        assert page_deck["text"] == text  # what is stored is the list the page analysed
        by_id = {"stats": tool(bot, token, "deck_stats", deck_id=page_deck["id"]),
                 "legality": tool(bot, token, "deck_legality", deck_id=page_deck["id"], format=fmt),
                 "shopping": tool(bot, token, "shopping_list", deck_id=page_deck["id"])}
        for key, answer in by_id.items():
            assert strip(answer) == strip(page[key]), f"{key}: the saved deck by id and the page disagree"

        # 3. The deck an assistant saves from the same link: its own writer, sections kept (sideboard, maybeboard).
        link = f"https://archidekt.com/decks/{deck['id']}/parity-deck"
        imported = tool(bot, token, "import_deck_from_link", url=link, name="Assistant copy")
        mine = imported["deck"]["id"]
        assert imported["deck"]["text"].startswith("Commander\n1 Test Commander")
        assistant = {"stats": tool(bot, token, "deck_stats", deck_id=mine), "legality": tool(bot, token, "deck_legality", deck_id=mine, format=fmt),
                     "shopping": tool(bot, token, "shopping_list", deck_id=mine)}
        for key, answer in assistant.items():
            assert strip(answer) == strip(page[key]), (
                f"{key}: an assistant that saved the deck from its link sees different numbers than the page\n"
                f"page: {json.dumps(strip(page[key]), sort_keys=True)[:600]}\nassistant: {json.dumps(strip(answer), sort_keys=True)[:600]}")

        # 4. The same deck with its unplayed sections written out the way import_deck_from_link writes them (the twin's deck
        # has none, the real Archidekt often does): the sideboard and maybeboard must not change any number.
        sectioned = text + "\nSideboard\n1 Cheap Ramp\nMaybeboard\n1 Blue Cantrip"
        for key, got in {"stats": tool(bot, token, "deck_stats", text=sectioned), "legality": tool(bot, token, "deck_legality", text=sectioned, format=fmt),
                         "shopping": tool(bot, token, "shopping_list", text=sectioned), "coverage": tool(bot, token, "check_decklist", text=sectioned)}.items():
            assert played(got) == played(page[key]), f"{key}: a list with a sideboard and maybeboard gives other numbers than the page's"
        counted = tool(bot, token, "deck_stats", text=sectioned)
        assert counted["result"]["by_section"] == {"commander": 1, "main": 9, "sideboard": 1, "maybeboard": 1}  # listed, never counted in
        assert counted["result"]["cards"] == page["stats"]["result"]["cards"] == 10

        # The deck's own summary (get_deck: what list_decks and the library show) agrees with the page's Owned / Missing /
        # To finish line, which the page adds up from the coverage lines (DeckPage's `summary`, checked in the next test).
        summary = tool(bot, token, "get_deck", deck_id=page_deck["id"])["summary"]
        lines = page["coverage"]["cards"]
        shown = {"need": sum(c["need"] for c in lines), "have": sum(min(c["have"], c["need"]) for c in lines),
                 "missing": sum(c["missing"] for c in lines), "missing_cost": page["coverage"]["missing_cost"],
                 "missing_unpriced": page["coverage"]["missing_unpriced"]}
        assert {k: summary[k] for k in shown} == shown
        assert shown["have"] == 3 and shown["missing"] == 7  # not trivially equal: the deck is partly owned


def test_the_page_reads_its_numbers_from_these_calls_and_nothing_else():
    """The driver sends what the page's calls send; this keeps the page's components wired to those same calls and list."""
    jsx = (ROOT / "public" / "views" / "deck.jsx").read_text(encoding="utf-8")
    assert "const text = useMemoD(() => (deck ? deckListText(deck.cards) : ''), [deck]);" in jsx
    assert "window.VaultApi.deckStats(text)" in jsx and "window.VaultApi.deckLegality(text, format)" in jsx
    assert "window.VaultApi.deckShopping(text)" in jsx and "window.VaultApi.deckCoverage(deckListText(merged))" in jsx
    assert "<DeckStats text={text} />" in jsx and "<DeckLegality text={text} format={format} setFormat={setFormat} />" in jsx
    assert "<DeckBuyList text={text} />" in jsx
    # the page's Owned / Missing / To finish line, added up from the coverage lines as the test above adds them up
    assert "ownedQty += Math.min(r.owned, r.qty); missingQty += r.need;" in jsx
    assert "owned: l ? l.have : 0, need: l ? l.missing : c.qty" in jsx
    assert "missingCost: coverage.missing_cost || 0, unpricedQty: coverage.missing_unpriced || 0" in jsx
    assert "window.VaultApi.saveDeck(name, text, deck.url || null, author)" in jsx  # Save stores the list that was analysed
    api = (ROOT / "public" / "lib" / "api.js").read_text(encoding="utf-8")
    for route in ("/decks/stats", "/decks/legality", "/decks/shopping-list", "/decks/coverage"):
        assert f"V1 + '{route}'" in api
