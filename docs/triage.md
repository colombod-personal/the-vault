# Triage: labels, states and how work is picked up

One page for people and agents (issue #131). Every open issue carries one **status**, at least one **area**, one **type**
and a **priority**.

## Labels

| Family | Labels | Meaning |
|---|---|---|
| Area (one or more) | `area:frontend`, `area:backend`, `area:database`, `area:data`, `area:ai-integration`, `area:infra` | What part of the product it touches |
| Type (one) | `bug`, `type:feature`, `type:design`, `type:research`, `type:chore` | The kind of work |
| Status (one) | `status:needs-refinement`, `status:ready`, `status:blocked` | Whether it can be picked up (below) |
| Working | `in-progress` | A session or person has claimed it right now |
| Priority (one) | `P1` do first, `P2` next, `P3` later | Order of work |
| Topic (optional) | `mcp`, `skills`, `harness`, `auth`, `security`, `apps-ui`, `onboarding`, `accessibility`, `epic` | Finer tags; `epic` marks a container |

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
