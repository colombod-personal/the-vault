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
    assert ip.refs_in("see #5 and Closes #6") == []  # a mention inside a sentence is not a link
    assert ip.refs_in("Closes #7\n\ntext #99") == [7]  # a closing line is followed by the progress workflow too


def test_every_pull_request_links_its_issue_with_a_keyword_github_understands():
    """A plain mention links nothing (the owner, 2026-10-08): Closes links the PR and closes the issue on merge, Refs says what is left."""
    assert rules.issue_link_problems("Closes #246\n\n## Evidence\n| a | b |") == []
    assert rules.issue_link_problems("Refs #246\n\nLeft open: the real run after deploy") == []
    assert rules.issue_link_problems("No issue: repairs a broken main after two PRs merged") == []
    assert any("names no issue" in p for p in rules.issue_link_problems("just a description"))
    assert any("names no issue" in p for p in rules.issue_link_problems("This is about #246 somehow"))  # a mention is not a link
    assert any("no evidence section" in p for p in rules.issue_link_problems("Closes #1\n\nit works"))
    assert any("Left open" in p for p in rules.issue_link_problems("Refs #1\n\nit helps"))
    assert rules.issue_link_problems("Closes #1\nRefs #2\n\nEvidence: tests/test_x.py") == []  # one closes, one only mentions


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
    # The owner's decision of 2026-10-10: all work is tracked in beads, GitHub issues are only users' reports and requests.
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "## 8. Beads is where all work is tracked, and only beads" in agents
    for needed in ("bd prime", "bd ready", "bd update <id> --claim", "--append-notes", "bd close <id> --reason",
                   "GitHub issues are only the public place", "vault-check", "merged", "deployed", "verified"):
        assert needed in agents, needed


def test_close_refuses_while_a_criterion_is_unverified(monkeypatch, capsys):
    issue = {"body": ip.put("x", ip.render(ROWS, {})), "state": "OPEN", "labels": [], "title": "t"}
    monkeypatch.setattr(ip, "read_issue", lambda n: issue)
    monkeypatch.setattr(ip, "gh", lambda *a: pytest.fail("must not call gh to close"))
    with pytest.raises(SystemExit) as exc:
        ip.main(["close", "246"])
    assert "cannot be closed" in str(exc.value) and "2 criteria" in str(exc.value)
