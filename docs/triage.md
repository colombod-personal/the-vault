# Triage: labels, states and how work is picked up

One page for people and agents (issue #131). Every open issue carries one **status**, at least one **area**, one **type**
and a **priority**.

## Labels

| Family | Labels | Meaning |
|---|---|---|
| Area (one or more) | `area:frontend`, `area:backend`, `area:database`, `area:data`, `area:ai-integration`, `area:infra` | What part of the product it touches |
| Type (one) | `bug`, `type:feature`, `type:design`, `type:research`, `type:chore` | The kind of work |
| Status (one) | `status:needs-refinement`, `status:ready`, `status:blocked` | Whether it can be picked up (below) |
| Working | `in-progress`, `waiting-owner` | `in-progress`: a session or person is on it right now (a branch or agent exists; removed when the PR merges or work stops). `waiting-owner`: a decision, an action or a real-app check only the owner can give is the one thing left (the Progress block says which). `needs-verification` is retired: the Progress block says what is merged and what is not yet verified |
| Planning | milestone | Every open issue has one (M1 to M6 for the AI-integration roadmap, M7 for the hardening and verification work after the audit of 2026-10-07). No milestone, not planned |
| Priority (one) | `P1` do first, `P2` next, `P3` later | Order of work |
| Topic (optional) | `mcp`, `skills`, `harness`, `auth`, `security`, `apps-ui`, `onboarding`, `accessibility`, `epic` | Finer tags; `epic` marks a container |

## Label decisions (owner's #131, recorded 2026-10-07)

Issue #131 asked for three renames; the audit found them half done and the decisions unrecorded. Recorded now:

| Old label | New label | Decision | Reason |
|---|---|---|---|
| `enhancement` | `type:feature` | **rename** | One type per issue, in one family. `enhancement` is a GitHub default label that no open or closed issue carries any more; delete it after the relabel |
| `data` | `area:data` | **rename** | Same meaning ("catalog, rules, prices and other outside data"). 14 issues still carry `data`, #62 carries both, and #133 has `data` but no `area:data`: the rename was never applied to existing issues |
| `in-progress` | `status:in-progress` | **not renamed** (stays `in-progress`) | It is not a status. An issue is `status:ready` *and* claimed, and the claim rule (`AGENTS.md`, `CLAUDE.md`, every agent brief) is written as "add `in-progress`, assign yourself, comment" with 13 issues claimed that way on 2026-10-07; a rename would silently break the "skip claimed issues" check. The "Status" family stays ready / needs-refinement / blocked |

Applying it to the existing issues and pull requests: `scripts/relabel_issues.py`. It is a dry run unless given `--apply`
and prints the exact list first (do not change many issues at once without showing the owner that list: `AGENTS.md`). It
adds the new label, removes the old one (an item with both ends with the new), and with `--delete-old-labels` removes the
emptied old labels afterwards. It has **not** been run against GitHub; whoever runs it shows the owner the list first.

New issues start in the right place: the forms under `.github/ISSUE_TEMPLATE` (bug report, feature request) pre-apply `bug`
or `type:feature` and `status:needs-refinement`, ask for acceptance criteria in the person's own words, and the issue
chooser points people who only want to connect an assistant to https://mtgvault.cards/connect.html.

## How an issue moves

`status:needs-refinement` → `status:ready` → `in-progress` → closed. An issue waiting on another is `status:blocked`
and also lists the blocker (GitHub "blocked by").

- **needs-refinement:** the design or the question is open. Agents do **not** implement it. They may help refine it:
  research, a design draft in a PR for the owner's review, questions in a comment.
- **ready:** refined, with acceptance criteria. Anyone may pick it up.
- **blocked:** do not start; work the blocker.
- **in-progress:** claimed. Do not start it elsewhere.

## Rules for agents

1. **Look before you start.** List issues and PRs updated since your last look and check for overlap with your area.
2. **Claim before working.** Add `in-progress`, assign yourself, and comment what you are doing. Remove the label when you
   finish or hand off.
3. **Pick only `status:ready`** (highest priority first). Never implement `status:needs-refinement`; skip `status:blocked`.
4. **Close with evidence.** A comment saying what merged (PR numbers), how it was checked, and what is left.
5. **File what you find.** A defect found while working gets its own issue with area, type, status and priority, linked to
   the work that found it.
6. **Epics hold sub-issues** (GitHub sub-issues, with "blocked by" for order). The epic stays open until its last item closes.

## What "done" means for a feature

Merged with CI green, live on the production site, and checked in the way a user meets it: on a phone and desktop for
web changes; through a real assistant for AI tools (docs/ai-integration-testing.md).
