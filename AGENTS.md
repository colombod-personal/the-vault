# How work is done on The Vault (read before touching anything)

These rules come from the owner, in the owner's words, after work was shipped without evidence. They apply to every
agent and every session (Claude, Codex, Copilot, Cursor). `CLAUDE.md` has the code conventions.

## 1. Acceptance criteria before any work

- Never start work on an issue that has no acceptance criteria. Write them first, **from the owner's own words**
  (quote the ask), each one testable, and show them to the owner when the ask is open to more than one reading.
- A criterion states what a person can do or see, in the app they use (Claude, ChatGPT, the website), not what the code
  contains. "Tests pass" is never a criterion on its own.
- Track the work in the issue: claim it (`in-progress`, assignee, comment), keep a checklist, and link every PR.

## 2. Done means evidence, issue by issue

- A criterion is ticked only with **evidence posted in the issue**: the CI run for a test, the production response for an
  API, and for anything a person uses through Claude or ChatGPT **a real run in that app after deploy** (screenshot or
  the captured answer). Merged is not done. Deployed is not done. Tests green is not done.
- PRs say `Refs #n`, **never `Closes #n`**: GitHub closes on merge, before anything was checked. The issue is closed by
  hand, after the last criterion has its evidence.
- Report status in three separate words, never "done": **merged**, **deployed**, **verified** (verified = the evidence
  is in the issue). If something is partial, say so first.
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
- Do not change many issues, PRs or settings at once without showing the list first.
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

## 8. The issue is the board: progress is written there, in the same shape, every time

The owner reads GitHub, not the chat. An issue that does not say where its work stands is a mess, whatever the code does.
Every issue carries a **Progress block** at the top of its body (between `progress:start` and `progress:end`, written only by
`scripts/issue_progress.py`): one state line, the pull requests, and a row per acceptance criterion with its state and
evidence. A criterion is `open`, `in review` (a PR is open), `merged` (on main, not seen working), `verified` (evidence in
the issue), `owner` (waits for a decision, an action or a real-app check only the owner can give) or `waived` (the owner
dropped it, in a comment that says so). The state line is derived, never typed: it says VERIFIED only when every row is
verified or waived.

What happens, and who does it:

1. **Before work:** the issue has criteria in the owner's words (section 1). Claim it: `in-progress`, assignee, comment,
   and give it a **milestone** (an issue without a milestone is not planned work).
2. **Opening a PR:** the description starts with `Refs #n` (or `No issue: <why>`), never `Closes`/`Fixes`/`Resolves`:
   `scripts/check_pr_rules.py` fails the pull request otherwise. The `issue progress` workflow then updates the Progress block
   (`in review`) and comments on the issue by itself.
3. **Merging:** the same workflow marks it `merged` and comments. Merged is not verified: the rows stay open.
4. **Verifying:** after deploy, run the criterion the way the owner would (section 2), post the evidence in the issue, then
   `python scripts/issue_progress.py tick <issue> "<words of the criterion>" --evidence "<link or the captured answer>"`.
5. **Closing:** `python scripts/issue_progress.py close <issue>`: it refuses while any row is not verified or waived.
   A bug is closed the same way, with the production evidence of the fix.
6. **Waiting on the owner:** rows that need the owner are `owner`, the issue gets the `waiting-owner` label, and the comment
   says the exact step. When the owner answers, the row becomes `verified` or `waived` with their words as evidence.
7. **`in-progress` is only for work happening now** (a branch or agent is on it). When the PR merges or the work stops, remove
   it: a stale label is a lie. `needs-verification` is not used: the Progress block says what is left and why.

If the board and the code disagree, the board is wrong: fix it in the same turn, before anything else.

## 9. The GitHub repository is the backlog: read it, never remember it

The owner's words (2026-10-07): "github repo is the backlog, not your memory". Every statement about the backlog (how many issues
are open or closed, which ones, what an issue or its epic says, what a pull request holds, whether CI is green, who a thing waits
on) comes from a read made **in the same turn**: `gh issue list`, `gh issue view`, `gh pr view`, `gh pr checks`. Counts are computed
from that list, never recalled, and the answer says what the read returned. This is rule 1 of `docs/triage.md` ("Look before you start") made binding for every statement, not only before starting.

- Your own earlier summaries, the Progress blocks and labels you wrote, and the session's notes are **claims to re-check**, not facts.
  An issue marked "not started" is checked against the code and the tests before work is planned or reported (#83 said "not started" with
  16 passing tests behind it).
- A label, state or milestone applied to many issues (`waiting-owner`, `status:*`) is derived from each issue's own text **and its
  epic's text**, read first, and the list is shown before it is applied (section 5). A bulk label written from memory marked 3 of 15
  issues wrongly, and put the collections issues at `status:ready` against the epic's own "stays needs-refinement until the design is agreed".
- Before writing a doc, a design, an issue or a script, search the repository and the issue (and its epic) for one that already exists, and
  extend it. Never overwrite a file you have not read: `git status` showing a file as modified that you thought was new means stop
  (`docs/collections.md` was overwritten once this way).

### Picking, claiming and finishing work (these rules live here; `docs/triage.md` only explains the labels)

Rules that bind an agent are written in this file, because this is the file it reads. A rule that exists only in a `docs/` page does not bind anyone.

1. **Look before you start:** list the open issues and pull requests (`gh issue list`, `gh pr list`) and check for overlap and for work already claimed.
2. **Pick only `status:ready`**, highest priority first, not `waiting-owner`. Never implement `status:needs-refinement` (help refine it: research, a draft in a PR, questions in a comment); skip `status:blocked`.
3. **Claim before working** (section 8, step 1) and release the claim when the work stops.
4. **Close with evidence** (section 8, steps 4 and 5): what merged, how it was checked, what is left.
5. **File what you find:** a defect found while working gets its own issue with area, type, status, priority and milestone, linked to the work that found it.
6. **Epics hold sub-issues** (GitHub sub-issues, "blocked by" for order); an epic stays open until its last item closes.
7. **`waiting-owner` means the owner is the only one who can do the one thing left.** Read the issue and its epic before applying it, say the exact step, and put a decision that many issues wait on in one place (the sign-off issue), not in many threads.
