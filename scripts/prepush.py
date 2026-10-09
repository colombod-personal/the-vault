"""Run before every push and before opening a pull request (AGENTS.md section 7). A broken pull request wastes CI minutes.

    VAULT_TEST_DATABASE_URL=postgresql://vault:vault@localhost:5432/vault_test python scripts/prepush.py [--full]

It checks, in this order, and stops at the first failure: your branch contains today's origin/main; the generated files (plugins,
agent definitions, README credits block, connect page, llms.txt) are up to date; the front-end bundle matches its sources; the
cheap tests that cut across the whole product pass (tool answers, plugins, skills, credits, compliance gate, council cost, twins);
and, when your diff touches security-sensitive code, it tells you what the pull request description must say. With ``--full`` it
then runs the whole suite (about 25 minutes: do this in the background before the first push of anything that changes shared
behaviour). CI runs the same suite, so a pull request that passes this passes there.
"""

from __future__ import annotations

import os
import subprocess
import sys

CROSS_CUTTING = [
    "tests/test_frontend_build.py", "tests/test_plugin.py", "tests/test_skills.py", "tests/test_agent_definitions.py",
    "tests/test_mcp_catalog.py", "tests/test_chatgpt_twin.py", "tests/test_compliance_gate.py", "tests/test_council_cost.py",
    "tests/test_sources.py", "tests/test_legal_pages.py", "tests/test_ai_parity_doc.py", "tests/test_clock_is_frozen.py",
    "tests/test_help.py", "tests/test_onboarding_whoami.py", "tests/test_ai_smoke.py", "tests/test_privacy.py",
    "tests/test_capabilities.py", "tests/test_tool_names.py",
]
SECURITY_SENSITIVE = ("vault/oauth_", "vault/auth.py", "vault/client_auth.py", "vault/passkeys.py", "vault/tokens.py",
                      "vault/native.py", "vault/reviewer", "vault/privacy.py", "vault/sharing.py")


def run(*cmd: str) -> None:
    print("$", " ".join(cmd), flush=True)
    if subprocess.run(cmd).returncode:
        sys.exit(f"prepush: FAILED: {' '.join(cmd)}\nFix it before you push: a failing pull request only wastes CI minutes.")


def out(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def main() -> int:
    if not os.environ.get("VAULT_TEST_DATABASE_URL"):
        sys.exit("prepush: set VAULT_TEST_DATABASE_URL (README -> Run it locally): the checks need Postgres")
    subprocess.run(["git", "fetch", "-q", "origin", "main"])
    if subprocess.run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"]).returncode:
        sys.exit("prepush: your branch does not contain origin/main. Run `git merge origin/main`, resolve conflicts, rebuild "
                 "generated files (scripts/build_plugin.py, npm --prefix web run build), then run this again.")
    py = sys.executable
    run(py, "scripts/build_plugin.py", "--check")
    present = [t for t in CROSS_CUTTING if os.path.exists(t)]  # a branch cut before a test existed does not have it
    changed = [f for f in out("git", "diff", "--name-only", "origin/main...HEAD").splitlines() if f.startswith("tests/test_") and f.endswith(".py")]
    present += [t for t in dict.fromkeys(changed) if os.path.exists(t) and t not in present]  # and every test file this branch touched
    run(py, "-m", "pytest", "-q", "-x", *present)
    touched = [f for f in out("git", "diff", "--name-only", "origin/main...HEAD").splitlines() if f.startswith(SECURITY_SENSITIVE)]
    if touched:
        print("\nThis diff touches security-sensitive code:", ", ".join(touched))
        print("The pull request description needs these two lines, true and specific (scripts/check_pr_rules.py):\n"
              "  Threat model: <updated in docs/mcp-oauth-threat-model.md in this PR | unchanged because ...>\n"
              "  Security review before merge: <who reviewed, where, and that it was before the merge>")
    if "--full" in sys.argv:
        run(py, "-m", "pytest", "-q")
    else:
        print("\nCheap checks passed. If you changed shared behaviour (tool answers, MCP, plugins, the database, auth), run "
              "`python scripts/prepush.py --full` in the background too (about 25 minutes) before opening the pull request.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
