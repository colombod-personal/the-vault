"""Pull request rule for security-sensitive code (issue #41, AGENTS.md): a change to sign-in, tokens or the OAuth server
must say what happened to the threat model and who reviewed it before the merge.

History this exists because of: the OAuth authorization server reached ``main`` three minutes before its threat model, and its
security review (which found a redirect bypass and two races) happened after the merge (docs/mcp-oauth-threat-model.md,
"What happened"). CI cannot prove that a review took place; it makes sure the pull request says who did it and where, so that
a missing review is visible to the person who merges.

    BASE_SHA=... HEAD_SHA=... PR_BODY=... python scripts/check_pr_rules.py

Reads the changed files from git (``BASE_SHA...HEAD_SHA``) and the pull request description from ``PR_BODY``.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys

# Paths whose change needs the threat-model and review lines. Keep in step with .github/pull_request_template.md.
AUTH_PATHS = (
    "vault/oauth_*.py",
    "vault/client_auth.py",
    "vault/auth.py",
    "vault/passkeys.py",
    "vault/tokens.py",
    "vault/native.py",
    "vault/reviewer*.py",
    "vault/privacy.py",
    "vault/sharing.py",
)
THREAT_MODEL = "docs/mcp-oauth-threat-model.md"

THREAT_LINE = re.compile(r"^\s*Threat model:\s*(?P<text>.*\S)\s*$", re.IGNORECASE | re.MULTILINE)
REVIEW_LINE = re.compile(r"^\s*Security review before merge:\s*(?P<text>.*\S)\s*$", re.IGNORECASE | re.MULTILINE)


def sensitive(changed: list[str]) -> list[str]:
    return [path for path in changed if any(fnmatch.fnmatch(path, pattern) for pattern in AUTH_PATHS)]


def real_answer(text: str) -> bool:
    """An answer someone wrote, not the template's placeholder (which is wrapped in angle brackets)."""
    return not text.startswith("<") and len(text) >= 10


def check(changed: list[str], body: str) -> list[str]:
    """The problems with this pull request; empty when it is fine."""
    touched = sensitive(changed)
    if not touched:
        return []
    problems = []
    threat = THREAT_LINE.search(body or "")
    if not threat or not real_answer(threat.group("text")):
        problems.append("add the line `Threat model: updated in docs/mcp-oauth-threat-model.md in this PR` or "
                        "`Threat model: unchanged because <reason>`")
    elif threat.group("text").lower().startswith("updated") and THREAT_MODEL not in changed:
        problems.append(f"the Threat model line says updated, but {THREAT_MODEL} is not part of this pull request")
    review = REVIEW_LINE.search(body or "")
    if not review or not real_answer(review.group("text")):
        problems.append("add the line `Security review before merge: <who reviewed it, where, and that it was before the merge>`")
    if problems:
        problems.insert(0, f"this pull request changes security-sensitive code ({', '.join(touched)})")
    return problems


def changed_files(base: str, head: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], check=True, capture_output=True, text=True)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def main() -> int:
    base, head = os.environ.get("BASE_SHA", ""), os.environ.get("HEAD_SHA", "")
    if not base or not head:
        print("BASE_SHA and HEAD_SHA are required", file=sys.stderr)
        return 2
    problems = check(changed_files(base, head), os.environ.get("PR_BODY", ""))
    for problem in problems:
        print(f"::error::{problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
