# Reconciler: what changed in the rules, and which skill text it touches (#107)

Written 2026-10-09, with the owner's standing instruction to deliver and the recommended (conservative) choice at every open
point. The issue asked for a job that diffs `rules_versions`, `rulings` and `legality_changes`. The stored `rules` and
`rules_versions` tables are gone (#142/#145: the rules are read live from Wizards and nothing of them is stored,
`tests/test_rules_live.py::test_nothing_of_the_rules_is_stored`), so "diff rules versions" means: **read two editions from Wizards
when someone asks, compare them in memory, return a small brief, keep neither.**

## What is built

| Piece | Where |
|---|---|
| The change brief: MCP tool `rules_changes`, REST `GET /api/v1/catalog/rules/changes` | `vault/api/catalog_api.py`, `vault/api/mcp_catalog.py` |
| Reading the previous edition and comparing | `LiveRules.compare` in `vault/rules_live.py` |
| The diff, the capped brief, the citation check | `vault/rules_changes.py` |
| Rulings and legality changes for the same period | `rulings_since`, `legality_changes_since` in `vault/catalog_queries.py` |
| The check of skill and agent citations, run weekly | `scripts/check_rule_citations.py`, `.github/workflows/rules-reconciler.yml` |
| Experts cite the edition and look for changes | the council brief (`vault/experts.py`), `agents/*.md`, `skills/rules-judge`, `skills/expert-council` |
| Two editions in the tests | `twins/wizards.py` (`publish(text, edition, listed=False)`), `tests/test_rules_changes.py` |

## Decisions taken, and why

1. **No table, no job that writes.** The brief is computed when asked and held in memory for six hours (ten minutes when no earlier
   edition was found). Nothing about an edition is stored: not its text, not its URL, not its date. (The issue allowed migration 0123 for a
   small summary; storing nothing is the conservative choice and `rules_changes` costs one extra file read per six hours per instance.)
2. **Where the previous edition comes from.** Wizards' rules page links only the current edition (re-read 2026-10-09: three links, no
   archive, no date). But the CDN keeps every earlier file under its dated name: a `HEAD` for
   `https://media.wizards.com/2026/downloads/MagicCompRules%2020260819.txt` answers 200 (977,822 bytes, `text/plain`, an `ETag`)
   next to the current `20260925`, and a name that was never published answers 404. Probing every date from 2025-08-22 to
   2026-09-24 on 2026-10-09 found seven earlier files: 2026-08-19, 2026-06-19, 2026-04-17, 2026-02-27, 2026-01-16, 2025-11-14,
   2025-09-19 (gaps of 41 to 63 days; most are Fridays, not all). **The date in a file name is the day it was published, not the day it
   takes effect:** the 20260819 file says "effective as of August 7, 2026". So:
   - the previous edition is the nearest earlier dated file, found by asking `HEAD` for each day back from the current file's date,
     15 days at a time (so about four rounds for a gap of eight weeks), up to 150 days back;
   - an earlier file with the same "effective as of" date as the current one is a correction of it, not the previous edition: it is
     noted (`same_effective_date`) and the search goes on past it;
   - `previous` (a date) skips the search; a date with no file is a 404;
   - if nothing is found within 150 days the brief says so (`rules` is null, with the reason) and the rest still answers;
   - anything other than 200 or 404 from the CDN is "unavailable" (503): a guess could name the wrong edition.
3. **What the brief holds.** Counts, then lists in rule-number order, each capped at `limit` (default 25, at most 50): rules `added`,
   `removed` (the number is gone and its words are nowhere else), `renumbered` (the same words, found once in each edition, under another
   number: Wizards inserts rules and the following ones move), `shifted` (a number both editions have that now holds another rule's
   words) and `changed` (the same number with other words, with `was` and `now`: the first sentence of the new rule the old one did not
   have, and the first sentence of the old one that is gone, each cut to 240 characters). Added and removed rules show their first
   sentence cut to 160. A tally by subsection says where the changes are. Deterministic: the same two editions give the same brief.
4. **Rulings and legality.** There is no history of catalog syncs (`catalog_sources` keeps only the latest load), so "between the two
   latest syncs" cannot be asked. The data does carry dates: a ruling has `published_at`, a legality change has `observed_on`. The brief
   lists those on or after `since`, by default the day the previous edition took effect (the same period as the rules diff); with no previous
   edition, the last 30 days. Newest first, capped, rulings cut to 240 characters (a pointer; `get_rulings` has the whole text). The legality
   log only covers what the Vault saw since it first loaded the card data, and says so.
5. **Skill and agent text that cites a rule.** `scripts/check_rule_citations.py` reads every rule number in `skills/*/SKILL.md` and
   `agents/*.md` (`rule 603.3b`, `rules 506 to 511`, `CR 702.19`, any dotted number such as `613.1a`; a bare three-digit number counts only
   after the word *rule*, so `100-card` is not one), then: **fails** when the number is not in the current edition (removed, renumbered, or
   never a rule); **warns** when it is still there with other words, or now holds another rule's words. Placeholders in skill text are written
   `rule <number>` so they are not citations. It runs weekly on GitHub (Monday, plus on demand) against Wizards' real editions: it is not
   part of the pull-request checks, because no test may contact Wizards (a pull request is checked against the twin's two editions instead).
   Exit 2 means Wizards could not be read.
6. **The experts.** Re-checking a persona's guidance against new rules cannot be automated further than naming the text that cites a
   changed rule. What changed is what the experts are told: every agent that can open a rule, the council brief a connector receives, the
   `rules-judge` and `expert-council` skills and the server instructions say to cite each rule number with the edition (`version`) a rules
   tool returned, and the judge and the chair to call `rules_changes` when a rule may have changed lately.

## Not built (and why)

- **A stored summary or a notification when a new edition appears.** Nothing is stored; the weekly job reports in its run summary and
  annotations only. A failing run emails the people watching the repository.
- **Reading an earlier edition for citations.** `version` on `get_rule`, `verify_citation` and the others still accepts only the current
  edition (#25). The CDN keeps earlier files, so this is now possible; whether to cite from them is a product decision (the owner's
  "store nothing" rule is not in the way, since they would be read live), filed as a follow-up.
- **Matching a rule that was reworded and renumbered at once.** It appears as one removed and one added rule.
- **Year folders.** A file is looked for in the folder of its own date's year; an edition published in December but filed under the next
  year would be missed (none seen).

## Limits to know

- The first call after a new edition reads two files of about a megabyte and asks up to 150 days of `HEAD`s; later calls are served from
  memory for six hours.
- A brief compares the current edition with the nearest earlier file, which can be a correction of an older edition than the one the reader
  has in mind: `previous` names it exactly.
