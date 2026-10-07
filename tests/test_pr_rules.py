"""The pull request rule for security-sensitive code (issue #41): scripts/check_pr_rules.py, the pull request template that
asks for the two lines, and the workflow that runs the check (docs/mcp-oauth-threat-model.md, "What happened")."""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_pr_rules as rules  # noqa: E402

GOOD = ("Refs #41\n\nThreat model: updated in docs/mcp-oauth-threat-model.md in this PR\n"
        "Security review before merge: reviewed by the owner on this PR before merging\n")


def test_a_change_outside_the_auth_code_needs_nothing():
    assert rules.check(["vault/analytics.py", "docs/api.md"], "Refs #1") == []


@pytest.mark.parametrize("path", ["vault/oauth_routes.py", "vault/oauth_clients.py", "vault/client_auth.py", "vault/auth.py",
                                  "vault/passkeys.py", "vault/tokens.py", "vault/native.py", "vault/reviewer_routes.py",
                                  "vault/privacy.py", "vault/sharing.py"])
def test_every_auth_file_needs_the_threat_model_and_review_lines(path):
    problems = rules.check([path], "Refs #41")
    assert problems and path in problems[0]
    assert any("Threat model" in p for p in problems) and any("Security review before merge" in p for p in problems)


def test_the_two_lines_satisfy_the_check():
    assert rules.check(["vault/oauth_routes.py", "docs/mcp-oauth-threat-model.md"], GOOD) == []


def test_unchanged_with_a_reason_does_not_need_the_document_edited():
    body = ("Threat model: unchanged because it only renames a log line\n"
            "Security review before merge: second pass by the owner on the PR\n")
    assert rules.check(["vault/auth.py"], body) == []


def test_saying_updated_without_touching_the_document_is_caught():
    problems = rules.check(["vault/oauth_routes.py"], GOOD)
    assert any("not part of this pull request" in p for p in problems)


def test_the_templates_placeholder_text_does_not_count_as_an_answer():
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    problems = rules.check(["vault/oauth_routes.py"], template)
    assert any("Threat model" in p for p in problems) and any("Security review" in p for p in problems)


def test_the_template_asks_for_both_lines_and_lists_the_same_paths_as_the_check():
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert "Threat model:" in template and "Security review before merge:" in template
    assert template.startswith("Refs #") and "never \"Closes\"" in template
    for pattern in rules.AUTH_PATHS:
        assert pattern in template.replace("*", "*"), pattern


def test_the_workflow_runs_the_check_on_pull_requests_with_nothing_secret():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-rules.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == "pull_request"
    steps = workflow["jobs"]["auth-review-lines"]["steps"]
    run = next(step for step in steps if "run" in step)
    assert run["run"] == "python3 scripts/check_pr_rules.py" and "${{" not in run["run"]
    assert {"BASE_SHA", "HEAD_SHA", "PR_BODY"} <= set(run["env"])
    assert "secrets." not in yaml.safe_dump(workflow)
    assert next(step for step in steps if "checkout" in step.get("uses", ""))["with"]["fetch-depth"] == 0


def test_the_auth_paths_cover_every_oauth_module():
    for path in (ROOT / "vault").glob("oauth_*.py"):
        assert rules.sensitive([f"vault/{path.name}"]), path.name
