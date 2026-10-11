# How work is done on The Vault (read before touching anything)

These rules come from the owner, in the owner's words, after work was shipped without evidence. They apply to every
agent and every session (Claude, Codex, Copilot, Cursor). `CLAUDE.md` has the code conventions.

## 1. Acceptance criteria before any work

- Never start work on a bead that has no acceptance criteria. Write them first, **from the owner's own words**
  (quote the ask), each one testable, and show them to the owner when the ask is open to more than one reading.
- A criterion states what a person can do or see, in the app they use (Claude, ChatGPT, the website), not what the code
  contains. "Tests pass" is never a criterion on its own.
- Track the work in **beads**, and only in beads (section 8): claim the bead, record every step in it as it happens, and
  link every PR.

## 2. Done means evidence, bead by bead

- A criterion is ticked only with **evidence recorded in the bead**: the CI run for a test, the production response for an
  API, and for anything a person uses through Claude or ChatGPT **a real run in that app after deploy** (screenshot or
  the captured answer). Merged is not done. Deployed is not done. Tests green is not done.
- A PR names its bead: the description starts with `No issue: bead vault-<id>` (`scripts/check_pr_rules.py` accepts it), with
  the evidence in the PR's description. Only a PR that fixes a user's public GitHub report uses `Closes #n`. Checks that can
  only be run after deploy are run right after deploy, and the bead is reopened if one fails.
- Report status in three separate words, never "done": **merged**, **deployed**, **verified** (verified = the evidence
  is in the bead). If something is partial, say so first.
- Before saying a feature works, run it the way the owner would: the real app, their data, their words.

## 3. The digital twins must match reality, or every green test is meaningless

- `twins/` stand in for every outside service and client (`docs/twins.md`). A twin that is more lenient or simpler than the
  real thing makes CI pass for the wrong reasons. Whenever a real service or host behaves in a way the twin does not
  (seen in a production log, a real client document, a real response), **update the twin and add a conformance check in
  the same PR**, and add the regression test that would have caught it.
- Learned this way: ChatGPT authenticates with `private_key_jwt` and first tries without a signature; Archidekt's
  `deckFormat` 3 is Commander; claude.ai and ChatGPT read a connector's tool list once, when it is added; ChatGPT flags
  tool descriptions that steer approval. Each one belongs in a twin or a test.

## 4. Product rules learned from real use

- Deck answers and panels lead with the deck: name, format, commander(s), card count, colour identity. Never only card lines.
- Tool descriptions say what a tool does. The workflow (preview, the person's yes, ask before saving) lives in the server
  instructions and the skills, not in write-tool descriptions (a test enforces it).
- Anything a connector cannot carry (agents, skills) must still reach Claude and ChatGPT: plugin packages for both, and
  `council_brief` / `expert_brief` for connector-only use. Assistants must be grounded by tools and skills, not left to
  recall: a deck review states what the tools returned, and says when the sources do not settle a point.
- After any change to tools, say what each host needs to see it (claude.ai: reconnect; ChatGPT: delete and re-add the app).

## 5. How to behave with the owner

- Do not hand the owner decisions that are the agent's to make. Decide, do it, show the evidence.
- Do not change many beads, issues, PRs or settings at once without showing the list first.
- Never ask the owner for personal data, or to relax a security setting as a shortcut; keep Vercel and Neon clean.
- When the owner reports a problem, fix **every** place it occurs (all tools, all panels), not the one they pointed at.

## 6. Security-sensitive changes: threat model and review come before the merge

Sign-in, tokens, consent, the OAuth server, privacy and sharing (`vault/oauth_*.py`, `client_auth.py`, `auth.py`,
`passkeys.py`, `tokens.py`, `native.py`, `reviewer*.py`, `privacy.py`, `sharing.py`) are changed only with the threat model
(`docs/mcp-oauth-threat-model.md`) updated or explained **in the same pull request**, and a security review done **before the
merge**, never after: the first OAuth server reached `main` before its threat model and its review (the history is in the
threat model). The pull request says so in two lines, `Threat model: ...` and `Security review before merge: ...`
(`.github/pull_request_template.md`); `scripts/check_pr_rules.py` fails the pull request without them. CI cannot prove a
review happened: whoever merges checks that the named review is real.

## 7. Never push a pull request that is not already green on your machine

Every push to a pull request runs the whole suite twice on GitHub, about ten minutes each, and the owner pays for those
minutes. On 2026-10-07 seven parallel branches were pushed having run only "the tests of the files I touched": five of them
failed in CI on things a local run would have shown (a stale generated file, a doc table that no test owned, a source missing
from the compliance gate, a migration number two branches both took, a missing security line in the description). Do not
repeat that.

- Before the **first** push, and again before every push that follows a merge of `main`: `python scripts/prepush.py`
  (branch contains today's `origin/main`, generated files up to date, the cross-cutting tests green). It stops at the first
  failure; fix it, do not push around it.
- If your change touches shared behaviour (tool answers, MCP tools or views, plugins, skills, the database or a migration,
  auth, privacy, anything that more than one test file reads), also run the whole suite once locally first, in the background:
  `python scripts/prepush.py --full` (about 25 minutes, your own Postgres database, never one another worktree uses).
  Tests that need a Linux shell (`test_workflows.py::test_vercel_deploys_only_main`) fail on Windows only: say so, do not hide it.
- Parallel branches: before you open the pull request, `git fetch` and merge `origin/main` again; if two branches add a
  migration, the second to merge takes the next number (`alembic heads` must show one head).
- A new tool answer field, a new source, a new generated file, a new doc table checked by a test: update everything that
  checks it in the same commit (cost table, credits, compliance gate, ai-parity, README block, plugins) and read the test
  you may have broken before you push, not CI's log after.
- Pull requests that CI turns red are fixed by pushing **one** corrected commit after running `prepush.py`, never by trial
  and error against CI. If you are not sure a push will pass, it is not ready.

## 8. Beads is where all work is tracked, and only beads

The owner's decision (2026-10-10): the product description says where the Vault is going; **beads** (`bd`) holds **all the
work**: tasks, order, criteria, claims, progress and evidence. **GitHub issues are only the public place for users' bug
reports and feature requests.** Work is never tracked in two places, and product plans never go into public issues (the
repository is public).

1. **Start every session with `bd prime`**, then `bd ready` for the work that is free. If `bd` doesn't answer, stop and say so.
   Never fall back to markdown TODO lists, plan files, notes or memory for tracking work.
2. **Claim before working:** `bd update <id> --claim`. Work already claimed is not yours.
3. **Record every step in the bead as it happens:** `bd update <id> --append-notes "<date>: what was done, where, proof"`.
   Nothing is reported as done before the command that does it has run.
4. **Break work down in beads:** `bd create "<title>" --parent <id>`, order with `bd dep add <later> --blocked-by <earlier>`.
   Work found while working gets its own bead, linked to the one that found it.
5. **Close with evidence:** only when every criterion is verified (section 2): `bd close <id> --reason "<what shipped, how it
   was checked, link to the proof>"`.
6. **Users' GitHub issues:** a user's bug report or feature request gets a bead (`--external-ref gh-<n>`) for the work. The
   GitHub issue only gets short public replies, with no internal plans, and is closed when the fix or feature ships.
7. **The roadmap lives in beads** as one epic per phase; each phase is blocked by the one before, so `bd ready` shows the
   current phase only.

**Where beads runs.** On the owner's Windows PC, Smart App Control blocks the unsigned `bd.exe`, so `bd` is a wrapper
(`%USERPROFILE%\.local\bin\bd`, `bd.cmd`) that runs every command on the owner's Ubuntu server over Tailscale, in the
Vault's beads workspace on a shared Dolt server that starts on boot. Agents just type `bd ...`. An agent that can't reach it
(a cloud sandbox) says so in its pull request, and the next local session records the steps in beads.

**Running the checks.** Smart App Control also blocks the Vault's Python on the owner's PC. `vault-check` (same folder) runs
`scripts/prepush.py` for the current branch on that server, with the same Postgres as CI: `vault-check`, or
`vault-check --full`. Section 7 applies: never push a pull request that `vault-check` hasn't passed.

## 9. Read the record, never remember it

Every statement about the work (what is open or done, what a bead or a pull request holds, whether CI is green, who a thing
waits on) comes from a read made **in the same turn**: `bd list`, `bd show`, `bd ready`, `gh pr view`, `gh pr checks`. Counts
are computed from that read, never recalled.

- Your own earlier summaries and the session's notes are **claims to re-check**, not facts. A bead marked "not started" is
  checked against the code and the tests before work is planned or reported.
- Before asking the owner to do something (send an email, add a key, answer a question), check the beads and the repository
  records for whether it already happened.
- Before writing a doc, a design or a script, search the repository and the beads for one that already exists, and extend it.
  Never overwrite a file you have not read.
