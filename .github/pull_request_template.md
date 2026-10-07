Refs #<issue>   <!-- never "Closes": the issue is closed by hand once every criterion has evidence (AGENTS.md) -->

| Criterion | What now makes it true | Evidence (test name, command output, file) |
|---|---|---|
| | | |

## Checklist (docs/triage.md, AGENTS.md)

- [ ] I claimed the issue (`in-progress`, assignee, comment) before starting, and it was `status:ready`.
- [ ] Every criterion above is from the issue, with evidence; anything not met is listed as not met.
- [ ] Twins and conformance checks updated if the Vault now talks to an outside service differently (`docs/twins.md`).
- [ ] **Auth and security changes only** (a change to `vault/oauth_*.py`, `vault/client_auth.py`, `vault/auth.py`, `vault/passkeys.py`, `vault/tokens.py`, `vault/native.py`, `vault/reviewer*.py`, `vault/privacy.py` or `vault/sharing.py`): fill in both lines below, or this pull request fails its check (`scripts/check_pr_rules.py`).

Threat model: <updated in docs/mcp-oauth-threat-model.md in this PR | unchanged because ...>
Security review before merge: <who reviewed, where (a review on this PR, or a link), and that it happened before the merge>
