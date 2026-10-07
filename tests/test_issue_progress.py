"""Progress is tracked in the issue itself (AGENTS.md section 8): the block's shape, what it derives, and the rules that keep it steady
(every pull request names its issue and never closes one; the workflow updates the issue; the script refuses to close an unverified one)."""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_pr_rules as rules  # noqa: E402
import issue_progress as ip  # noqa: E402

ROWS = [{"criterion": "Disconnect revokes all | of an app", "state": "verified", "evidence": "tests/test_connected_apps.py"},
        {"criterion": "Two devices both work", "state": "merged", "evidence": ""},
        {"criterion": "Real click on production", "state": "owner", "evidence": ""}]


def test_the_block_round_trips_and_escapes_what_would_break_the_table():
    block = ip.render(ROWS, {273: "merged"}, today="2026-10-07")
    rows, prs = ip.parse("intro\n\n" + block + "\n\nbody")
    assert prs == {273: "merged"}
    assert [r["state"] for r in rows] == ["verified", "merged", "owner"]
    assert rows[0]["criterion"] == "Disconnect revokes all / of an app" and rows[0]["evidence"] == "tests/test_connected_apps.py"
    assert "| [x] |" in block and block.count("| [ ] |") == 2  # only the verified row is ticked


def test_the_state_line_never_says_more_than_is_true():
    assert ip.derive([], {}) .startswith("NOT STARTED")
    line = ip.derive(ROWS, {273: "merged"})
    assert line.startswith("MERGED (#273) but not verified") and "2 of 3 criteria still open" in line and "1 wait for the owner" in line
    assert "IN REVIEW (#9 open)" in ip.derive([{"criterion": "x", "state": "in review", "evidence": ""}], {9: "open"})
    done = [{"criterion": "x", "state": "verified", "evidence": "e"}, {"criterion": "y", "state": "waived", "evidence": "owner said so"}]
    assert ip.derive(done, {1: "merged"}).startswith("VERIFIED")
    assert not ip.derive([*done, ROWS[1]], {1: "merged"}).startswith("VERIFIED")  # one unverified row stops it


def test_the_block_replaces_itself_and_keeps_the_rest_of_the_issue():
    body = "The ask.\n\n## Acceptance criteria\n- [ ] first thing\n- [x] second thing\n"
    first = ip.put(body, ip.render(ip.checklist_rows(body), {}))
    assert first.startswith(ip.START) and "The ask." in first and "- [ ] first thing" in first
    second = ip.put(first, ip.render(ROWS, {1: "open"}))
    assert second.count(ip.START) == 1 and second.count(ip.END) == 1 and "The ask." in second and "Two devices both work" in second
    assert [r["state"] for r in ip.checklist_rows(body)] == ["open", "verified"]


def test_issues_are_found_from_refs_lines_only():
    assert ip.refs_in("Refs #246\n\ntext #99") == [246]
    assert ip.refs_in("Refs #23 #25, #137 and #9") == [9, 23, 25, 137]
    assert ip.refs_in("see #5 and Closes #6") == []


def test_every_pull_request_names_its_issue_and_never_uses_a_closing_keyword():
    assert rules.issue_link_problems("Refs #246\n\nbody") == []
    assert rules.issue_link_problems("No issue: repairs a broken main after two PRs merged")  == []
    assert any("names no issue" in p for p in rules.issue_link_problems("just a description"))
    closing = rules.issue_link_problems("Refs #1\nFixes #2")
    assert closing and "closing keyword" in closing[0]
    assert rules.issue_link_problems("Refs #1\n\nThis fixes the parity test in #274")[0:0] == []  # prose without 'fixes #n' is fine


def test_the_workflow_updates_issues_from_the_default_branch_with_only_issue_write():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "issue-progress.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["pull_request"]["types"] == ["opened", "reopened", "closed"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["progress"]["permissions"] == {"contents": "read", "issues": "write"}
    steps = workflow["jobs"]["progress"]["steps"]
    checkout = next(s for s in steps if "checkout" in s.get("uses", ""))
    assert "default_branch" in checkout["with"]["ref"]  # never the pull request's own copy of the script
    run = next(s for s in steps if "run" in s)
    assert "${{" not in run["run"] and "issue_progress.py" in run["run"] and "secrets." not in yaml.safe_dump(workflow)
    assert "merged == true" in workflow["jobs"]["progress"]["if"]  # a PR closed without merging changes nothing


def test_agents_md_states_the_lifecycle_and_the_commands():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## 8. The issue is the board" in agents
    for needed in ("scripts/issue_progress.py", "Progress block", "waiting-owner", "in review", "merged", "verified", "milestone"):
        assert needed in agents, needed


def test_close_refuses_while_a_criterion_is_unverified(monkeypatch, capsys):
    issue = {"body": ip.put("x", ip.render(ROWS, {})), "state": "OPEN", "labels": [], "title": "t"}
    monkeypatch.setattr(ip, "read_issue", lambda n: issue)
    monkeypatch.setattr(ip, "gh", lambda *a: pytest.fail("must not call gh to close"))
    with pytest.raises(SystemExit) as exc:
        ip.main(["close", "246"])
    assert "cannot be closed" in str(exc.value) and "2 criteria" in str(exc.value)
