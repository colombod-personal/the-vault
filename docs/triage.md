# Triage: labels, states and how work is picked up

One page for people and agents (issue #131). **Since 2026-10-10 GitHub issues are only the public place for users' bug
reports and feature requests. All work is tracked in beads** (`AGENTS.md` section 8): tasks, order, claims, progress and
evidence live there, and product plans never go into public issues. This page is about the labels a user's report carries.

## Labels

| Family | Labels | Meaning |
|---|---|---|
| Area (one or more) | `area:frontend`, `area:backend`, `area:database`, `area:data`, `area:ai-integration`, `area:infra` | What part of the product it touches |
| Type (one) | `bug`, `type:feature`, `type:design`, `type:research`, `type:chore` | The kind of work |
| Status (one) | `status:needs-refinement`, `status:ready`, `status:blocked` | Whether it can be picked up (below) |
| Working (retired) | `in-progress`, `waiting-owner` | Retired for internal work on 2026-10-10: claims, waiting and progress are kept in the bead, never as labels. A user's report does not carry them |
| Planning (retired) | milestone | Retired on 2026-10-10: order and phases are the roadmap epics in beads |
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

## How a user's report moves

`status:needs-refinement` → `status:ready` → closed.

1. **A person files a report** with one of the forms (bug report or feature request).
2. **It gets a bead** for the work, with `--external-ref gh-<n>`: `bd create "<title>" --external-ref gh-<n>`. The
   bead holds the criteria, the plan and the evidence; the issue never does.
3. **The issue gets short public replies only** (that it was seen, questions, and when it ships), with no internal plans.
4. **It is closed when the fix or feature ships**, with a short public note of what changed. The bead closes with its own
   evidence (`AGENTS.md` sections 2 and 8).

Other people (and their agents) can add a reaction or a comment to a report; the bead is where they are read and ranked.

## Rules for agents

The binding text is **AGENTS.md sections 8 and 9** (an agent reads that file, not this page). In short: start with
`bd prime` and `bd ready`, claim with `bd update <id> --claim`, record every step in the bead, close with evidence, and
never create or label a GitHub issue for internal work.

## What "done" means for a feature

Merged with CI green, live on the production site, and checked in the way a user meets it: on a phone and desktop for
web changes; through a real assistant for AI tools (docs/ai-integration-testing.md).
