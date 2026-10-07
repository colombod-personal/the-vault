"""The docs about the Comprehensive Rules match the final design (#142, #143): nothing says the rules are loaded into the catalog, no
command in a doc is one the job refuses, and the pages that must explain how a new edition is noticed do."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md", ROOT / "CLAUDE.md"]
SKIP = {"verification-ledger.md"}  # the audit's own record quotes what the old docs said


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_doc_gives_a_command_that_loads_the_rules_into_the_catalog():
    """sync_catalog refuses a 'rules' source (tests/test_rules_parser.py): a doc must not tell anyone to pass one."""
    for path in DOCS:
        if path.name in SKIP:
            continue
        for line in text(path).splitlines():
            if "jobs.sync_catalog" in line or "CATALOG_SOURCES=" in line:
                assert not re.search(r"(--sources|CATALOG_SOURCES=)[^\s]*\brules\b", line), f"{path.name}: {line.strip()}"
        assert not re.search(r"--file rules=", text(path)), path.name


def test_the_design_docs_say_the_rules_are_read_live_and_not_stored():
    catalog = text(ROOT / "docs" / "catalog-design.md")
    assert "Not built; dropped by migration 0104" in catalog and "read live from Wizards" in catalog
    assert "keep each version (`rules_versions`)" not in catalog
    sources = text(ROOT / "docs" / "data-sources.md")
    row = next(line for line in sources.splitlines() if line.startswith("| Wizards Comprehensive Rules"))
    assert "read live from Wizards" in row and "off by default" not in row.lower()
    index = text(ROOT / "docs" / "rules-index.md")
    assert "supersedes the first idea of a daily job" in index and "(see \"Design\" below" in index


def test_the_rules_index_explains_how_a_new_edition_is_announced_and_noticed_with_measurements():
    index = text(ROOT / "docs" / "rules-index.md")
    for needed in ("How a new edition is announced", "Update Bulletin", "no date or version text", "published before it takes effect",
                   "same file name", "ETag", "seen after 360 minutes", "Measured on the real edition", "scripts/measure_rules_index.py"):
        assert needed in index, needed
    assert (ROOT / "scripts" / "measure_rules_index.py").exists()
