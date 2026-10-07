"""Every source the Vault uses is credited on public/credits.html (repo rule: new sources go on the credits page).

docs/data-sources.md lists each source and whether it is used. A source that is built, read live or used on demand must
be named on the credits page; one that is "Not used" needs no credit. A new row in the table fails the first test until
someone decides which it is, so a source cannot be added without a credit (#16, #18, #20)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDITS = " ".join(re.sub(r"<[^>]+>", " ", (ROOT / "public" / "credits.html").read_text(encoding="utf-8")).split())

# source row in docs/data-sources.md -> the text the credits page must contain for it
CREDITED_AS = {
    "Scryfall `oracle_cards`, `rulings`, `oracle_tags`, `default_cards`": ["Scryfall", "Scryfall Tagger"],
    "Wizards Comprehensive Rules": ["Wizards of the Coast", "Comprehensive Rules"],
    "Commander Spellbook": ["Commander Spellbook"],
    "Wizards Commander Brackets and Game Changers list": ["Commander Brackets", "Game Changers"],
    "Archidekt": ["Archidekt"],
    "Moxfield": ["Moxfield"],  # only as a CSV format: the Vault never fetches from it
    "EDHREC": ["EDHREC"],  # only the popularity rank Scryfall includes: the Vault never fetches from it
}
NOT_USED = ("Cardmarket price guide", "Card Kingdom price list", "Magic Madhouse product feed")


def table_rows():
    """(source, status) for the first table of docs/data-sources.md."""
    text = (ROOT / "docs" / "data-sources.md").read_text(encoding="utf-8")
    table = text.split("| Source | Use | Status | Reason |")[1].split("\n\n")[0]
    rows = []
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and not set(cells[0]) <= {"-", " "}:
            rows.append((cells[0], cells[2]))
    return rows


def test_every_row_of_the_data_sources_table_is_either_credited_or_marked_not_used():
    rows = table_rows()
    assert len(rows) >= 8
    for source, status in rows:
        assert source in CREDITED_AS or source in NOT_USED, f"{source!r}: decide whether it needs a credit and add it here"
        if source in NOT_USED:
            assert status.startswith("**Not used"), f"{source!r} is listed as not used but its status says: {status}"


def test_each_used_source_is_named_on_the_credits_page():
    for source, needles in CREDITED_AS.items():
        for needle in needles:
            assert needle in CREDITS, f"{source}: {needle!r} is missing from public/credits.html"


def test_the_credits_page_says_what_the_new_sources_do_and_what_is_sent():
    assert "Scryfall Tagger" in CREDITS and "volunteers" in CREDITS and "never as a rule" in CREDITS
    assert "keeps no copy of the rules" in CREDITS and "Commander Brackets" in CREDITS and "not approved or endorsed by Wizards" in CREDITS
    assert "sends the deck's card names" in CREDITS and "written by its community" in CREDITS
    assert "Commander Spellbook, Archidekt" in CREDITS  # named among the trademarks it does not claim
