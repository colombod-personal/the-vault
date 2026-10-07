"""scripts/ai_smoke.py drives a live Vault with real data (docs/ai-integration-testing.md). It cannot run in CI, so this
keeps it from rotting: it must call only tools that exist, with arguments those tools accept."""

import re
import sys
from pathlib import Path

from vault.api import mcp, mcp_ui

SCRIPT = Path(__file__).parent.parent / "scripts" / "ai_smoke.py"


def test_the_smoke_test_calls_only_real_tools_with_real_arguments():
    source = SCRIPT.read_text(encoding="utf-8")
    called = set(re.findall(r'c\.call\("([a-z_]+)"', source))
    assert called and called <= set(mcp.BY_NAME), f"unknown tools: {called - set(mcp.BY_NAME)}"
    for match in re.finditer(r'c\.call\("([a-z_]+)"((?:, \w+=)[^\n]*)', source):
        tool, rest = match.group(1), match.group(2)
        props = set(mcp.BY_NAME[tool].properties)
        for arg in re.findall(r"[ (,]\s*([a-z_]+)=", rest):
            assert arg in props, f"{tool} has no argument {arg}"


def test_the_script_imports_and_has_its_groups():
    sys.path.insert(0, str(SCRIPT.parent))
    import ai_smoke

    assert set(ai_smoke.GROUPS) == {"connection", "cards", "rules", "decks", "prompts"}


def test_the_smoke_test_checks_the_views_the_server_really_has():
    """It said 'five views' for a while after a sixth was added, so the live check would have failed. Now it compares with the
    server's own list of views, and a count written into the script is refused."""
    sys.path.insert(0, str(SCRIPT.parent))
    import ai_smoke

    assert ai_smoke.VIEWS == set(mcp_ui.VIEWS)
    source = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"len\(res\)\s*==\s*\d", source) and "five" not in source.lower().replace("five-colour", "")
    assert "ui://vault/{v}" in source and 'out.get("mcp-session-id")' in source  # the view list and the capability check are both asserted live
