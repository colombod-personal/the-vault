"""The threat model says what actually happened and what is decided (issue #41, #45), and stays true.

These cannot make history different: they keep the document honest. If someone builds pre-registered clients, the test
fails until the decision paragraph is replaced by a real section; and AGENTS.md carries the rule for auth changes."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = (ROOT / "docs" / "mcp-oauth-threat-model.md").read_text(encoding="utf-8")
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def test_the_threat_model_records_the_real_order_of_events_with_commits_and_times():
    history = MODEL.split("## What happened", 1)[1].split("\n## ", 1)[0]
    for commit in ("66bb839", "fe51c01", "f8982d3", "8dc6d5d"):
        assert commit in history
    assert "written after the endpoints" in history and "after the code was\non `main`" in history
    assert "cannot be met retroactively" in history
    assert "It was written before the code" not in MODEL.replace("**It was not written before the code**", "")


def test_the_commits_named_in_the_history_exist_and_are_in_that_order():
    """Skipped where the repository has no full history (a shallow clone); the order is read from git, not from the prose."""
    try:
        order = {}
        for commit in ("66bb839", "fe51c01", "f8982d3", "8dc6d5d"):
            out = subprocess.run(["git", "show", "-s", "--format=%ct", commit], cwd=ROOT, capture_output=True, text=True, check=True)
            order[commit] = int(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        import pytest
        pytest.skip("no full git history here")
    assert order["66bb839"] < order["fe51c01"] < order["f8982d3"] < order["8dc6d5d"]
    assert 0 < order["fe51c01"] - order["66bb839"] <= 600  # minutes after the endpoints, not before them


def test_pre_registered_clients_are_a_recorded_decision_and_not_in_the_code():
    decision = " ".join(MODEL.split("Pre-registered clients (issue #45", 1)[1][:1800].split())
    assert "not built" in decision and "Owner decision, recommended: accept" in decision
    assert "metadata documents" in decision and "dynamic" in decision
    for path in (ROOT / "vault").rglob("*.py"):
        assert "preregistered" not in path.read_text(encoding="utf-8").lower(), (
            f"{path.name} builds pre-registered clients: replace the decision in docs/mcp-oauth-threat-model.md by a real "
            "section (configuration channel, consent display, secrets) in the same pull request")


def test_agents_md_carries_the_review_rule_for_auth_changes():
    assert "Threat model:" in AGENTS and "Security review before merge:" in AGENTS
    assert "scripts/check_pr_rules.py" in AGENTS and ".github/pull_request_template.md" in AGENTS
    assert re.search(r"before the (merge|code)", AGENTS)
