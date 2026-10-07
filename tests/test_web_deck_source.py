"""The web app's deck source (public/lib/deck.js) read against the real shape of the Vault's Archidekt answer (#256).

Nothing ran this code before: a change to the answer broke 'paste an Archidekt link' in the web app unnoticed."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")

SCRIPT = r"""
global.window = {};
const calls = [];
window.VaultApi = { archidektDeck: async (id, refresh) => { calls.push([id, refresh]); return ANSWER; }, parseDeck: async () => ({ cards: [], unparsed: [] }) };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
(async () => {
  const a = await window.DeckSrc.fetchUrl('https://archidekt.com/decks/123/some-slug');
  const b = await window.DeckSrc.fetchUrl('https://archidekt.com/decks/123', true);
  console.log(JSON.stringify({ a, calls }));
})();
"""


def test_a_deck_from_a_link_keeps_its_cards_printings_and_age_and_skips_the_sideboard():
    answer = {"deck": {"id": 123, "name": "Sliver Swarm", "author": "ann"}, "vault_cache": {"from_cache": True, "fetched_at": "x", "age_seconds": 180},
              "cards": [{"quantity": 1, "name": "Sol Ring", "set": "c21", "collector_number": "263", "categories": [], "section": "Deck"},
                        {"quantity": 1, "name": "Sliver Overlord", "set": "", "collector_number": "", "categories": ["Commander"], "section": "Commander"},
                        {"quantity": 1, "name": "Counterspell", "set": "mh2", "collector_number": "267", "categories": ["Sideboard"], "section": "Sideboard"},
                        {"quantity": 1, "name": "Maybe Card", "set": "", "collector_number": "", "categories": ["Maybeboard"], "section": "Maybeboard"}]}
    script = "const ANSWER = " + json.dumps(answer) + ";\n" + SCRIPT
    out = subprocess.run(["node", "-e", script, str(ROOT / "public" / "lib" / "deck.js")], capture_output=True, text=True, check=True, timeout=30)
    result = json.loads(out.stdout)
    deck = result["a"]
    assert deck["title"] == "Sliver Swarm" and deck["author"] == "ann" and deck["url"] == "https://archidekt.com/decks/123"
    assert [c["name"] for c in deck["cards"]] == ["Sol Ring", "Sliver Overlord"]  # no sideboard, no maybeboard
    assert deck["cards"][0]["set"] == "c21" and deck["cards"][0]["collector_number"] == "263" and deck["cards"][1]["categories"] == ["Commander"]
    assert deck["cache"]["age_seconds"] == 180
    assert result["calls"] == [["123", False], ["123", True]]  # Refresh asks Archidekt again


def test_the_api_client_asks_for_the_cards_and_refresh_only_when_told():
    source = (ROOT / "public" / "lib" / "api.js").read_text(encoding="utf-8")
    assert "'?detail=cards' + (refresh ? '&refresh=true' : '')" in source


def test_the_deck_library_tiles_show_each_deck_s_format_and_commanders():
    """#216/#280: the web app's tiles said the deck's name and link only; the format and commander(s) come from the answer's overview."""
    source = (ROOT / "public" / "views" / "deck.jsx").read_text(encoding="utf-8")
    tile = source.split('className="deck-tile-what"', 1)[1].split("</div>", 1)[0]
    assert "d.overview.format" in tile and "d.overview.commanders.join(' + ')" in tile and "no commander recorded" in tile
