"""docs/ai-parity.md (#100, #137): every action says where it lives in the web app ("UI place"), every label it quotes exists in
the front end, and every MCP tool is listed (simulate_draws included)."""

import re
from pathlib import Path

from vault.api import mcp

ROOT = Path(__file__).parent.parent
DOC = (ROOT / "docs" / "ai-parity.md").read_text(encoding="utf-8")
FRONT_END = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "public").glob("*.jsx")) + sorted((ROOT / "public" / "views").glob("*.jsx")))
TABLES = ("## Collection", "## Decks", "## Sharing", "## Cards and rules (catalog)", "## Account: kept out of AI apps on purpose")


def section(title: str) -> str:
    start = DOC.index(title)
    end = DOC.find("\n## ", start + 1)
    return DOC[start:end if end != -1 else len(DOC)]


def rows(title: str) -> list[list[str]]:
    lines = [l for l in section(title).splitlines() if l.startswith("|")]
    return [[c.strip() for c in l.strip("|").split("|")] for l in lines[2:]]


def test_every_table_has_a_ui_place_column_and_every_row_fills_it():
    for title in TABLES:
        header = [l for l in section(title).splitlines() if l.startswith("|")][0]
        columns = [c.strip() for c in header.strip("|").split("|")]
        assert "UI place" in columns, title
        index = columns.index("UI place")
        for row in rows(title):
            assert len(row) == len(columns), (title, row)
            assert row[index], (title, row)


def test_the_ui_place_column_follows_the_action_as_the_issue_asked():
    """#100: 'table in docs/ai-parity.md: action, UI place, REST route, MCP tool, scope (read/write/account), gap yes/no'."""
    header = [l for l in section("## Decks").splitlines() if l.startswith("|")][0]
    assert [c.strip() for c in header.strip("|").split("|")] == ["Action", "UI place", "REST route", "MCP tool", "Scope", "Gap"]


def test_every_label_the_table_quotes_exists_in_the_front_end():
    missing = []
    for title in TABLES:
        header = [l for l in section(title).splitlines() if l.startswith("|")][0]
        index = [c.strip() for c in header.strip("|").split("|")].index("UI place")
        for row in rows(title):
            for label in re.findall(r"“([^”]+)”", row[index]):
                if label not in FRONT_END:
                    missing.append((row[0], label))
    assert not missing, f"UI labels in docs/ai-parity.md that the front end does not contain: {missing}"


def test_the_tabs_it_names_are_the_tabs_the_app_has():
    for tab in ("Vault", "Browse", "Sets", "Decks", "Lab", "Ideas"):
        assert f">{tab}</button>" in FRONT_END or f">{tab}\n" in FRONT_END, tab


def test_every_mcp_tool_is_listed_and_simulate_draws_is_in_the_decks_table():
    named = [t.name for t in mcp.TOOLS if f"`{t.name}`" not in DOC]
    assert named == [], f"tools missing from docs/ai-parity.md: {named}"
    simulate = [r for r in rows("## Decks") if "`simulate_draws`" in r[3]]
    assert len(simulate) == 1 and "`POST /decks/simulate`" in simulate[0][2]
    assert "“Opening turns” tab" in simulate[0][1]  # the web app has a place for it (#138): the deck page's tab


def test_the_web_apps_buy_list_tab_exists_where_the_table_says():
    assert "['buy', 'Buy list']" in FRONT_END and "Copy list" in FRONT_END
