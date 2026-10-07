"""The compliance gate is a test, not a promise (issue #62, docs/compliance.md "Source gate"): nothing is ingested or
served from a source whose terms have not been read first-hand and recorded, with a date and a URL.

Also pins the shape of the research documents the audit found incomplete (#79, #80): the Archidekt message and the
shop table. These check that the facts are written down with a source and a date; they cannot check that the facts are
true (that is why each row names the page it was read from)."""

import re
from pathlib import Path

from jobs import sync_catalog
from vault import provenance

ROOT = Path(__file__).resolve().parent.parent
COMPLIANCE = (ROOT / "docs" / "compliance.md").read_text(encoding="utf-8")
DATA_SOURCES = (ROOT / "docs" / "data-sources.md").read_text(encoding="utf-8")
OUTREACH = (ROOT / "docs" / "outreach-drafts.md").read_text(encoding="utf-8")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def gate_rows() -> dict[str, list[str]]:
    """Rows of the two tables under "## Source gate" in docs/compliance.md, by the key in the first column."""
    section = COMPLIANCE.split("## Source gate", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 6 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = cells
    return rows


def loadable_sources() -> set[str]:
    """Every catalog source the code can load or serve."""
    return set(sync_catalog.SOURCES) | set(sync_catalog.OTHER_JOBS) | set(provenance.CATALOG_SOURCES)


def test_every_source_the_code_can_load_has_a_row_marked_read_with_a_date_and_a_terms_url():
    rows = gate_rows()
    for name in sorted(loadable_sources()):
        assert name in rows, (
            f"catalog source {name!r} can be loaded but has no row in the Source gate of docs/compliance.md: "
            "read its terms first-hand, then add the row (Gate `read`, the date, the terms URL)")
        _, source, gate, read_on, terms, _meaning = rows[name][:6]
        assert gate == "read", f"{name}: the gate says {gate!r}; a source whose terms are not read stays off"
        assert DATE.match(read_on), f"{name}: the date the terms were read must be YYYY-MM-DD, not {read_on!r}"
        assert terms.startswith("https://"), f"{name}: name the page the terms were read from"


def test_a_source_marked_not_read_is_not_loadable():
    for key, cells in gate_rows().items():
        if not cells[2].startswith("read"):
            assert key not in loadable_sources(), f"{key} is loadable but its terms are not read"


def test_the_gate_table_has_no_half_filled_rows():
    for key, cells in gate_rows().items():
        gate, read_on, terms = cells[2], cells[3], cells[4]
        if gate.startswith("read"):
            assert DATE.match(read_on) and terms.startswith("https://"), f"{key}: a read source needs a date and a URL"
        else:
            assert gate.startswith("not read"), f"{key}: gate must start with 'read' or 'not read', not {gate!r}"


def test_a_new_sync_job_or_workflow_forces_a_look_at_the_gate():
    """A new job that loads data is a new source. Adding one fails here until this list (and the gate table) are updated."""
    jobs = {p.stem for p in (ROOT / "jobs").glob("sync_*.py")}
    assert jobs == {"sync_catalog", "sync_prices"}, (
        f"new sync job(s) {jobs - {'sync_catalog', 'sync_prices'}}: add the source to the Source gate in docs/compliance.md "
        "(terms read first-hand, date, URL), make the job refuse unnamed sources, then update this list")
    workflows = {p.name for p in (ROOT / ".github" / "workflows").glob("sync-*.yml")}
    assert workflows == {"sync-catalog.yml", "sync-prices.yml"}, workflows


def test_the_catalog_job_refuses_a_source_the_gate_does_not_know():
    import pytest
    with pytest.raises(SystemExit):
        sync_catalog.main(["--sources", "some_new_source"])


def test_the_workflows_only_name_gated_sources():
    """Any CATALOG_SOURCES list written into the repository (docs, workflows, jobs) names only gated sources."""
    rows = gate_rows()
    pattern = re.compile(r"CATALOG_SOURCES=([a-z_,]+)")
    for path in [*(ROOT / ".github" / "workflows").glob("*.yml"), *(ROOT / "docs").glob("*.md"), *(ROOT / "jobs").glob("*.py")]:
        for listing in pattern.findall(path.read_text(encoding="utf-8")):
            for name in listing.split(","):
                assert name in rows and rows[name][2] == "read", f"{path.name}: {name} is not a gated source"


# -- the research documents (#79, #80, #62)

def test_the_outreach_drafts_include_archidekt_and_the_order_lists_it():
    assert "## To Archidekt" in OUTREACH
    order = OUTREACH.split("## Order", 1)[1]
    assert "Archidekt" in order
    assert "Nothing has been sent" in OUTREACH
    archidekt = OUTREACH.split("## To Archidekt", 1)[1].split("\n## ", 1)[0]
    for needle in ("**one**", "never write", "never search", "User-Agent", "public deck"):
        assert needle in archidekt, needle


def test_the_drafts_do_not_say_sign_in_is_only_for_the_private_collection():
    """The owner decided on 2026-10-06 that every feature needs a free account (#62); the drafts must say the same."""
    assert "only to protect each person's private collection" not in OUTREACH
    assert "without an account" not in OUTREACH.replace("no anonymous", "")


def test_the_shop_table_gives_every_shop_a_source_url_a_date_and_a_feed_answer():
    table = DATA_SOURCES.split("## Shops: links, terms and price feeds", 1)[1].split("What this means:", 1)[0]
    header = next(line for line in table.splitlines() if line.startswith("| Shop"))
    for column in ("Link format", "Terms on automation", "Feed available?", "alert"):
        assert column.lower() in header.lower(), column
    rows = [line for line in table.splitlines() if line.startswith("| **")]
    assert {re.match(r"\| \*\*([^*]+)\*\*", r).group(1) for r in rows} == {"Card Kingdom", "Magic Madhouse", "Cardmarket"}
    for row in rows:
        shop = row.split("|")[1].strip(" *")
        assert re.search(r"https://\S+", row), f"{shop}: a source URL"
        assert re.search(r"2026-\d\d-\d\d", row), f"{shop}: a date"
        feed = row.split("|")[4].strip()
        assert re.match(r"\*\*(Yes|No)\b", feed), f"{shop}: the feed column starts with a bold Yes or No, not {feed[:30]!r}"
        assert "Not checked" not in row, shop
        assert len(row.split("|")) == 7, f"{shop}: five columns"


def test_search_decks_is_documented_as_one_page_on_request_only():
    assert "search_decks" in COMPLIANCE
    text = " ".join(COMPLIANCE.split("search_decks", 1)[1][:2500].split())  # one line, whatever the wrapping
    assert "follows `next`" in text and "one page" in text
    assert "never called from a job" in text
