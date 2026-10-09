# Limited data: per-card aggregates from 17Lands public datasets (design for #178)

Status: **design; slice 1 is built (section 14), the rest is not.** Written 2026-10-09 for [#178](https://github.com/colombod-personal/the-vault/issues/178)
(status `needs-refinement` until the owner has seen the decisions at the end). It blocks the Limited part of #106
(learning help). Nothing here is ingested, downloaded or served: the 17Lands pages were read and their files were only
asked about with HEAD requests (size, date, tag). This document is the "design written down before the build" and the
"compliance checked before ingesting" that #178 asks for; the compliance rules it applies are in `docs/compliance.md`
(show provenance always; never pass third-party data as ours; comply before ingesting) and the source row in
`docs/data-sources.md` ("Metagame and Limited data").

## 1. What was read on 2026-10-09 (and what was not)

The 17Lands pages and the licence were opened in a browser and read in full (the 17Lands pages are drawn by script, so a plain
fetch returns an empty shell); `robots.txt` and the types script were fetched as text. None of them contains text addressed to AI
agents or assistants.

| Page | Read | The gist, as it matters here |
|---|---|---|
| [17lands.com/public_datasets](https://www.17lands.com/public_datasets) | 2026-10-09 | "Unless otherwise noted, these data sets are licensed under a Creative Commons Attribution 4.0 International License" (links to the CC BY 4.0 deed). A table of expansion, format, last updated, and three links per row: draft data, game data, replay data. The data is "usually CSV or JSON data archived and compressed with gzip". Game-level data is one row per game; draft-level data one row per pick; replay data one row per turn. "P1P1 data may be missing in some sets due to it not being in the Arena logs." The page calls the files "aggregated data in an anonymized fashion"; it does not say whether they hold every game or a sample. Helper files: a replay data-types script, card lists (`cards.csv`, `abilities.csv`). 36 entries are listed (the newest HOB, MSH, SOS, TMT, ECL, TLA; one is a Powered Cube), each in PremierDraft, TradDraft, and mostly PickTwoDraft, Sealed and TradSealed. Sealed has no draft file |
| [17lands.com/terms_of_service](https://www.17lands.com/terms_of_service) | 2026-10-09 | "Effective as of September 12, 2024". Content on the site is 17Lands' "except where noted otherwise on specific pages", "17Lands content is not for resale", users may not "modify, publish, transmit ... or in any way exploit" the site content. The public datasets page is such a page ("noted otherwise": CC BY 4.0). Carries the Wizards Fan Content notice itself |
| [17lands.com/usage_guidelines](https://www.17lands.com/usage_guidelines) | 2026-10-09 | Scope: guidance for the **curated data on the site** (Card Data, Deck Color Data...). It says the data on the Public Datasets page "typically released under a CC BY 4.0 license" is **not in scope**. Even so it shows what 17Lands wants, and we follow it by choice: cite 17Lands (spelled "17Lands"), do not imply endorsement, keep the citation "clearly visible at the top level, not hidden in a footnote or mouse-over", link to the page the data comes from; an embargo until day 12 of a set on Arena for sites that re-show site data; "we discourage automated scraping of our API" and, instead, "we love when people make use of the public data dumps" |
| [17lands.com/faq](https://www.17lands.com/faq) | 2026-10-09 | Publication schedule of the dumps: draft data 2 weeks into a set, game data 3 weeks, replay data 5 weeks (the usage guidelines say 6: they disagree); "Previous data sets will be updated every time a new one is released". The API is unsupported for outside use (we do not use it) |
| [17lands.com/metrics_definitions](https://www.17lands.com/metrics_definitions) | 2026-10-09 | The site's own definitions of #GIH, GIH WR, #OH, OH WR, #GD, GD WR, #GP, GP WR, ALSA, ATA, with caveats. Counts are per copy, not per game. A game whose data is inconsistent (a card in hand that was not in the deck or sideboard, no deck linked) is left out of in-game statistics. Latest change in the changelog 2025-08-01. This is the vocabulary we reuse, and the definitions we mirror |
| [17lands.com/robots.txt](https://www.17lands.com/robots.txt) | 2026-10-09 | Disallows only `/card_data/details` and `/data/card_based_performance` for all robots. The files live on another host (`17lands-public.s3.amazonaws.com`), which has no robots file of its own that we read |
| [CC BY 4.0 legal code](https://creativecommons.org/licenses/by/4.0/legalcode.en) | 2026-10-09 | Section 3(a) (what attribution needs), 2(a)(6) (no implied endorsement), Section 4 (database rights) and Section 5 (no warranty), quoted in section 6 below |
| [helper_files/replay_dtypes.py](https://17lands-public.s3.amazonaws.com/analysis_data/helper_files/replay_dtypes.py) | 2026-10-09 | 17Lands' own list of column names and types for the public files: the only first-hand column list we have (section 2) |

**Not verified, and the build must verify before it relies on it:**

- The real header and first rows of a game file and a draft file. No raw file was downloaded; the columns below come from
  17Lands' own data-types script and the page text, not from opening a file.
- Whether `pick_number` and `pack_number` start at 0 or 1; whether rows of one draft are contiguous; how double-faced and
  split cards are named in the column names; how many rows and how many bytes uncompressed a file holds.
- Whether the S3 host rate-limits. None is published; the job is polite by design (section 4).
- Whether individual files carry a licence note that differs from the page ("unless otherwise noted"). The job's first run
  reads the first rows of each file and the build records what it found in the pull request.
- The page's "Last Updated" date and the file's own date disagree: HOB shows 2026-08-30 on the page, while the game file's
  `Last-Modified` is 2026-10-01; ECL shows 2026-02-08, the file 2026-05-18 (consistent with the FAQ: older sets are
  refreshed when a new one is released). **The file's `Last-Modified` and ETag are what the Vault records**, not the page's date.
- 17Lands' site numbers and ours will not match exactly (different weighting details, different exclusions): section 7.

## 2. The files

Address pattern (from the page's links): `https://17lands-public.s3.amazonaws.com/analysis_data/<kind>/<kind>_public.<SET>.<FORMAT>.csv.gz`
with `<kind>` one of `draft_data`, `game_data`, `replay_data`. A file that exists answers HEAD with 200, a `Content-Length`,
`Last-Modified`, an `ETag` and `Accept-Ranges: bytes`; one that does not exist answers 403 (checked 2026-10-09 with HEAD:
`game_data_public.ZZZ.PremierDraft.csv.gz` gives 403; `draft_data_public.SOS.Sealed.csv.gz` gives 403). The Vault can therefore
discover which sets are published by asking about each set code, without a listing.

| File | One row is | Columns we need (17Lands' types script) | Columns we ignore |
|---|---|---|---|
| **Game data** | one game | `expansion`, `event_type`, `game_time`, `won` (bool), `on_play`, `num_mulligans`, `main_colors`, `splash_colors`; per card: `deck_<Card>`, `sideboard_<Card>`, `opening_hand_<Card>`, `drawn_<Card>`, `tutored_<Card>` (small integers: copies) | `draft_id`, `rank`, `opp_*`, `user_n_games_bucket`, `user_game_win_rate_bucket`, `build_index`, `match_number`, `game_number`, `num_turns` |
| **Draft data** | one pick | `expansion`, `event_type`, `draft_id`, `draft_time`, `pack_number`, `pick_number`, `pick` (the card taken), `pick_maindeck_rate`, `pick_sideboard_in_rate`; per card: `pack_card_<Card>` (copies in the pack the drafter saw) | `pool_<Card>`, `rank`, `event_match_wins`, `event_match_losses` |
| **Replay data** | one turn | per-turn events (cards cast, damage...) | **everything: never downloaded** |

Sizes (HEAD, `Content-Length`, gzip, 2026-10-09; MB = 10^6 bytes):

| Set | Draft PremierDraft | Game PremierDraft | Draft TradDraft | Game TradDraft | Replay PremierDraft (not used) |
|---|---|---|---|---|---|
| HOB | 75.3 | 22.1 | 8.1 | 2.7 | 179.2 |
| MSH | 108.3 | 31.5 | 10.7 | 3.4 | 222.2 |
| SOS | 162.6 | 49.4 | 15.8 | 5.3 | 332.0 |
| TMT | 42.3 | 13.2 | 5.1 | 1.8 | 107.7 |
| ECL | 127.8 | 42.8 | 11.9 | 4.4 | not offered |
| TLA | 152.9 | 47.2 | 15.0 | 5.1 | not measured |
| EOE | 141.6 | 45.6 | 15.3 | 5.4 | not measured |
| FIN | 215.7 | 65.1 | 23.6 | 7.9 | not measured |
| **Eight sets** | 1,026.6 | 316.7 | 105.5 | 35.9 | |

So the draft and game files for eight sets in both formats are about **1.48 GB compressed** to read once (the replay files, the
largest, are never touched). The numbers in #178 and `data-sources.md` (draft 75 MB, game 22 MB) are HOB PremierDraft and
agree with the HEAD answers. Uncompressed size is unknown (not verified).

## 3. Decisions of this design

| Question | Design |
|---|---|
| **Formats** | `PremierDraft` (Bo1 draft) and `TradDraft` (Bo3 draft): the two with a draft file and a game file for every recent set. Not in the first build: `PickTwoDraft` (a different pick rhythm, so pick positions mean something else), `QuickDraft`, cubes (a cube is not a set; the pool changes), and the Sealed formats (game file only: no pick positions). Sealed is the second slice (section 11): the Limited expert covers sealed, and its game statistics are the same |
| **Sets** | A rolling window of the **8 most recent sets** that have a game file for a format, found by asking S3 about each recent Scryfall set code (upper case) with HEAD. A set leaves the window when a ninth arrives. Eight is about two years of releases: enough to answer a question about a set people still draft, small enough to refresh quickly. Storage is not the limit (section 5), download time is |
| **Slicing** | One slice only: all players, all colours. No per-colour-pair or per-skill-group numbers in the first build (`main_colors` and the win-rate bucket columns exist, so this can be added; it multiplies rows by about 11 and shrinks every sample by about 10) |
| **Kept** | Per-card counts (below), per set and format. **Never kept:** a row of either file, a `draft_id`, a rank, a timestamp per game, a deck list, a pool. The raw files are streamed and thrown away |
| **Not computed** | No grades (A+, B-), no composite score, no "pick order" of our own, no tier lists: those would be the Vault pretending to a rating. A sort by one named metric is all that is offered |
| **Scope of the claim** | The data is from Magic Arena players who run 17Lands' tracker. It is not paper Magic and not every player. Every answer says "MTG Arena" |

## 4. The job

`jobs/sync_limited.py`, run by `.github/workflows/sync-limited.yml`, built from the pattern of `sync-catalog`
(`docs/catalog-design.md`, "Ingestion"): runs only from `main`, in the `vercel-production` environment, pinned
runner, hash-checked dependencies, read-only `permissions`, the database address read the same way, concurrency group
`vercel-production`, and an `alert` job holding only `issues: write` for the budget guard's message. It loads nothing unless
`limited_17lands` is named in the repository variable `CATALOG_SOURCES` (the existing switch; `limited_17lands` is added to
`OTHER_JOBS` in `jobs/sync_catalog.py` so the catalog job skips it, as it does `oracle_prices`) and refuses names the gate does not know.

**Schedule.** Weekly (Monday, after the catalog job), plus `workflow_dispatch` with an optional `sets` input for the
first fill. 17Lands refreshes files every few weeks (their schedule: draft data about 2 weeks into a set, game data 3 weeks, and
older sets are refreshed when a new set arrives), so most runs find nothing new and finish in seconds.

**One run:**

1. `db_budget.check(refuse=True)` (stops at 85% full), as every job that adds data.
2. Work out the window: the codes of recent sets from Scryfall's sets list (one call through the job's rate-limited Scryfall client:
   expansion, core, masters and draft-innovation sets released in the last 30 months), upper case, asked to S3 with
   `HEAD game_data_public.<SET>.<FORMAT>.csv.gz` and the same for `draft_data`. 200 means published, 403 means not yet. A set with a
   game file is in the window; a set is not ingested until 14 days after its `released_at` (our own reading of 17Lands' day-12
   embargo wish, which does not formally apply to the public files; their own schedule makes it normally moot).
3. For each (set, format, kind) in the window: compare `ETag` and `Last-Modified` with the `limited_sources` row. Equal means skip.
4. For each file that changed, one at a time, in this order (smallest first): `GET` with the descriptive User-Agent the
   other jobs use (`the-vault/0.1 (+https://github.com/colombod-personal/the-vault)`), `Accept-Encoding: identity`, read the gzip stream
   once through `gzip` and `csv`, **never writing the file to disk**, and keep only the counters of section 5 in memory
   (a few hundred cards, plus the state of one draft at a time).
5. Check the result (below), then replace that file's rows in **one transaction** and write the `limited_sources` row last.
6. `db_budget.check(fail_at_warn=True)` last, as every job.

**Limits (they are ours: 17Lands publishes none for the dumps).**

| Limit | Value | Why |
|---|---|---|
| Concurrent downloads | 1 | A volunteer-run host and a public S3 bucket; the job is the only reader |
| Largest file accepted | 400 MB (`Content-Length`); today's largest is 215.7 MB | A file far larger than seen means a changed format; refuse and say so |
| Downloaded per run | at most 800 MB; the rest waits for the next run | The first fill (1.48 GB for eight sets) takes two or three runs; no run is long |
| Retries | 3 per file, waits of 2, 8 and 30 seconds, only for connection errors, 429 and 5xx; honour `Retry-After` | Never hammer; a 4xx other than 429 is not retried |
| Job timeout | 120 minutes | A cap, not an estimate: **the run time is unknown** (uncompressed size and Python's csv speed on wide rows were not measured). The first real run measures it and the pull request records it. If a run is too slow, the fallback is a faster reader with hash-pinned dependencies, decided then |
| Daily guard | the same 70% / 85% database budget rules | `jobs/db_budget.py` |

**Failure handling.** Nothing is written for a file until its whole stream was read and its checks passed; the previous rows
stay untouched when a file fails. A failed file does not stop the others; the run exits non-zero at the end if any planned
file failed, so GitHub shows a red run and the budget `alert` mechanism is unchanged. Specifically:

- A truncated or corrupt download: Python's gzip reader raises at the end of the stream (the CRC fails); no write.
- Missing required columns (`won`, `expansion`, any `opening_hand_`/`drawn_`/`deck_` card column in a game file; `pick`, `pick_number`,
  `pack_number`, `draft_id`, any `pack_card_` column in a draft file): fail the file, say which.
- Sanity checks before writing: at least 1,000 games (or picks) used, at least 100 cards, the set's overall win rate between 0.40 and
  0.70, `expansion` in the file equal to the set asked for. After a first load, a new file that would remove more than 20% of the
  cards or more than half of the games compared with the stored rows is refused unless `--force`.
- A draft file whose rows of one `draft_id` are not contiguous: fail the file (the last-seen computation relies on it). If the first run
  shows they are not, the design changes to a per-draft dictionary (memory is still small: one entry per draft in progress).
- S3 answering 403 for a file that was in the window yesterday: reported as "no longer published", the stored rows stay,
  and the run does not fail (a file can be withdrawn; the provenance keeps its date).

**Idempotence.** The same file (same ETag and `Last-Modified`) is never read twice; running the job again changes nothing
(`--force` re-reads). Rows are keyed `(set_code, format, card_name)`; replacing a file's rows is delete-and-insert inside one
transaction, which for a few hundred rows is cheaper than hashing and does not cause meaningful Neon change history.

## 5. The aggregate schema

No per-user data anywhere: the tables have no `user_id`, so nothing is added to `vault.privacy.personal_data`, the export,
`docs/gdpr.md` or `public/privacy.html` (the credits page changes: section 8). Migration `0119` or the next free number at
build time (check `alembic heads`; a parallel branch may take it). Three tables.

**`limited_game_stats`** (from the game file; one row per set, format, card)

| Column | Type | Meaning |
|---|---|---|
| `set_code` | varchar(10) | 17Lands' expansion code, upper case (`HOB`) |
| `format` | varchar(20) | `PremierDraft` or `TradDraft` |
| `card_name` | varchar(200) | the card's name as in 17Lands' columns |
| `oracle_id` | varchar(36), null, indexed | the card in the Vault's catalog, matched by name (front face or full name); null when the name does not match (for example Arena-only rebalanced cards); the unmatched count is logged and stored |
| `games_played`, `wins_played` | integer | #GP and wins: copies in the main deck, summed over games, and the same sum over games won |
| `opening`, `wins_opening` | integer | #OH: copies in the kept opening hand (`opening_hand_<Card>`), and the same over won games |
| `drawn`, `wins_drawn` | integer | #GD: copies drawn later (`drawn_<Card>`, which excludes the opening hand), and the same over won games |

Derived when read, never stored (so it cannot disagree): **games in hand** = `opening + drawn` (#GIH: "drawn into hand, either in the
opening hand or later"), **wins in hand** = `wins_opening + wins_drawn`, **win rate in hand** = wins in hand / games in hand
(GIH WR), the opening-hand and drawn rates likewise, and the 95% Wilson interval. A game with inconsistent data (more copies
in hand than deck plus sideboard) is left out of every in-game count, as 17Lands does, and counted in `skipped_records`. Tutored
copies are not counted as drawn (17Lands' definition since 2022-10-16).

**`limited_pick_stats`** (from the draft file; same key)

| Column | Type | Meaning |
|---|---|---|
| `set_code`, `format`, `card_name`, `oracle_id` | as above | |
| `seen` | integer | #Seen: packs (per drafter, per pack round) in which the card was seen; a wheel counts once |
| `last_seen_sum` | integer | the sum, over those, of the pick number (1 to 15, within the pack) at which the card was **last** seen; **last-seen pick position** (ALSA) = `last_seen_sum / seen` |
| `picked` | integer | the number of times it was taken (`pick`) |
| `picked_sum` | integer | the sum of the pick numbers at which it was taken; ATA = `picked_sum / picked` |

Computation: for each drafter (`draft_id`) and pack round (`pack_number`), the last `pick_number` at which `pack_card_<Card>` is at least
1 is the card's last-seen pick in that pack; a card seen at pick 1 and again at pick 9 counts once, with 9, as 17Lands defines
ALSA. Pick numbers are stored 1-based, within the pack. If the file's numbers are 0-based, the loader adds one (the build checks
and the test pins it). When pick 1 is missing in a set (the page warns of it), ALSA is slightly high for cards that rarely
wheel: the answer repeats 17Lands' own caveat when the file shows missing first picks (more picks with no `pack_card_` at pick 1 than expected).

**`limited_sources`** (what was read, one row per set, format and kind: feeds provenance, `whoami`, and the "as of" line)

| Column | Type | Meaning |
|---|---|---|
| `set_code`, `format`, `kind` | varchar | `kind` is `game` or `draft`; primary key together |
| `etag`, `last_modified`, `content_length` | text, timestamptz, bigint | what S3 said about the file read: the version of the data |
| `fetched_at` | timestamptz | when the Vault read it |
| `records`, `skipped_records` | bigint | games or picks used, and games left out for inconsistent data |
| `wins` | bigint, null | games won among the games used (game kind): the set's baseline win rate = `wins / records` |
| `first_time`, `last_time` | timestamptz, null | the earliest and latest `game_time` / `draft_time` in the file: the window the data covers |
| `cards`, `unmatched_cards` | integer | cards found, cards that did not match the catalog |

The set-level baseline matters: 17Lands users win more than half of their games (the baseline is not 50%), so a card's win rate
is read against its set's baseline, which the answer states (`vs_baseline_points`).

**Size.** Measured on 2026-10-09 in a throwaway local Postgres 16 table with the types above (400 cards of invented names about as long as real
ones, 8 sets, 2 formats = 6,400 rows per table, `varchar(36)` ids, primary key and `oracle_id` index, no vacuum):

| Table | Rows | Size with indexes |
|---|---|---|
| `limited_game_stats` | 6,400 | 1.96 MB (about 306 bytes a row) |
| `limited_pick_stats` | 6,400 | 1.91 MB |
| `limited_sources` | 32 | under 0.1 MB (estimate) |

About **4 MB for the whole window**, **0.4% of the 1,024 MB** of Neon's free plan, against a planned catalog of about 120 MB
(`docs/catalog-design.md`, "Neon budget"). The assumption of 400 cards a set is an upper guess (the file column counts were not read);
rows scale linearly: about 400 rows per set, format and table, so **about 1,600 rows per set** for two formats and two tables; the
"few thousand rows per set" of #178 is met with margin. The guard is unchanged: the job refuses at 85% and fails at 70% (`jobs/db_budget.py`);
no new table needs its own guard. The one-colour-pair slicing of a later slice would be about 11 times the game table (about 22 MB,
2% of the plan), still inside the budget but a decision of its own.

**Retention.** Current rows only, no history (a refreshed file replaces the old rows; there is no time series, so no growth).
Sets that leave the 8-set window are deleted in the same run, with their `limited_sources` rows. Neon's change history:
replacing about 13,000 small rows a few times a month is negligible beside the daily price job.

## 6. Compliance: the licence, and exactly what it asks

Read 2026-10-09 against CC BY 4.0 Section 3(a) ([legal code](https://creativecommons.org/licenses/by/4.0/legalcode.en)): if the Vault
Shares the Licensed Material "(including in modified form)" it must, for the items 17Lands supplies with the material,
retain: identification of the creator, a copyright notice, a notice that refers to the Public License, a notice that refers to
the disclaimer of warranties, and a URI or hyperlink to the Licensed Material "to the extent reasonably practicable"; **indicate
if it modified the Licensed Material**; and indicate the material is licensed under the Public License with the text or a URI of it.
"You may satisfy the conditions ... in any reasonable manner based on the medium, means, and context", for example "by providing a
URI or hyperlink to a resource that includes the required information". If the licensor asks, the information must be removed
"to the extent reasonably practicable". Section 2(a)(6): nothing permits asserting or implying endorsement. Section 4: if "all or a
substantial portion of the contents of the database" is Shared, 3(a) applies. The Vault shares aggregates, not the contents, but follows
3(a) anyway. The licence is not non-commercial and has no "share alike"; the Vault is free in any case.

What 17Lands supplies with the material: the page names the creator only as "17Lands" and gives **no copyright notice, no suggested credit line and no
list of others to credit**, so there is none to retain (the 17Lands Terms of Service name the owner, "17Lands LLC", and carry no
statement about the datasets other than the "noted otherwise on specific pages" exception). Compared with the existing compliance
documents: the `data-sources.md` row ("Usable with attribution", licence sentence word for word, our "17Lands data, CC BY 4.0, set, format, date" label) is
confirmed against the live page and extended below; the gate in `docs/compliance.md` needs its own row when the build adds the source (section 9).

**The attribution text.** Short form, in every answer that uses the data (as `provenance[source].notice` and as the `attribution` field),
at the top of the answer, never only in a footnote:

> Data from 17Lands (https://www.17lands.com/public_datasets), licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), which also states that it is provided without warranty (section 5). Magic Arena games and drafts, set {SET}, format {FORMAT}; 17Lands files last updated {DATE}, read by the Vault on {DATE}. Changed by the Vault: the per-game and per-pick rows were reduced to per-card counts, and the percentages, intervals and sample-size warnings were computed by the Vault, so they can differ from the figures on 17lands.com. Not produced or endorsed by 17Lands.

Every piece is there for a licence reason: creator and link (3(a)(1)(A)(i), (v)), the licence and its URI (iii, C), the warranty
notice (iv, through the licence section), the modification statement (B), and the no-endorsement line (2(a)(6) and the usage
guidelines). 17Lands is written with a capital L, as they ask.

**Where it shows.**

1. Every tool and API answer carrying these numbers: the `attribution` string and the `provenance` blocks (section 10).
2. The Limited expert's answer: the first sentence that quotes a number says "According to data from 17Lands (set, format, date)"
   (their suggested wording in the usage guidelines), and the answer repeats the sample size.
3. `public/credits.html`: a card for 17Lands with the text above, the link, and the thank-you (the usage guidelines ask for Patreon support: link
   `patreon.com/17lands` as they do).
4. The `vault-attribution` skill (the list of services behind the Vault), the MCP server `instructions`, `whoami` (the data version and date of each
   loaded set), the README "Attribution" section, `THIRD_PARTY_NOTICES.md` (data, not code), and the Limited expert definitions.
5. `docs/compliance.md`: the gate row and a "17Lands" requirements table like the Scryfall one; `docs/data-sources.md`: the row updated.

**What must not be claimed.**

- That 17Lands endorses, produced, reviewed or "powers" the Vault, or the Limited expert's advice.
- That a figure **is 17Lands' number**. The raw games and picks are theirs; the percentages are the Vault's, computed from them, labelled
  `computed` with the source as input, and may differ from 17lands.com (their site weights by copy and drops some games differently; the Vault mirrors
  the published definitions but the files may not carry everything the site's pipeline uses). Words: "computed by the Vault from 17Lands data", never
  "17Lands says this card wins 61%".
- That a win rate is **ours**: no "Vault rating", "Vault pick order", grades or tiers.
- That a card is a "best pick", "bomb", "must-pick" or "pick over X" on a sample below the floor (section 7); that a gap smaller than the
  intervals' overlap is a real difference; that a win rate in hand says the card *makes* decks win (it is correlation: strong cards go in strong decks, and
  17Lands cautions about biases such as cards that are mulliganed more often).
- That the data describes paper Magic, all players, or a set before its dump exists.
- That any older edition's figures are current: the as-of date is always shown.
- Any claim about a set the Vault has not loaded: the tool says it has no data and lists what it has.

**Guards in the build.** The gate test (`tests/test_compliance_gate.py`) needs the `limited_17lands` row (read, date, terms URL) before the code can
load it. A re-read of the licence page is due **every 90 days** and whenever 17Lands changes its terms: the page is drawn by script, so a plain
conformance fetch cannot read the sentence; instead a monthly reminder issue (the pattern of `neon-monthly-check.yml`) lists the pages and the licence
sentence to re-read, and the job refuses to run once the recorded "terms read on" date in `docs/compliance.md` is over 120 days old. If 17Lands asks to
remove its credit or its data, the data is switched off with the repository variable at once (the Fan Content policy's takedown rule for Wizards is
handled the same way, `docs/compliance.md`).

## 7. Sample size: when a number may be shown, ranked or recommended

Rule: every figure travels with its sample, and what may be said depends on the sample.

| Level | Games in hand (win rates) or times seen / picked (ALSA, ATA) | Win rate shown | May be ranked or recommended |
|---|---|---|---|
| `too_few` | under 200 | Yes, with the interval, flagged | **No**: left out of any sorted list; never called better or worse than another card |
| `low` | 200 to 999 | Yes, with the interval | Only as "a small sample": "may be" language, no "best", and only when the interval does not overlap the set's baseline or the compared card's |
| `ok` | 1,000 or more | Yes, with the interval | Yes, as "this data suggests", with the sample and the caveat that it is correlation |

Why these numbers: at a win rate near 55%, the 95% interval (Wilson) is about plus or minus 6.9 points at 200 games, 4.4 at 500, 3.1 at 1,000 and
1.0 at 10,000. Card win rates in a set spread over a few points on each side of the baseline, so under 200 games a card cannot be told from average,
and under 1,000 the interval is still as wide as the difference people want to read. Early in a set (the first dump arrives about 2 to 3
weeks in) samples are smaller; the as-of date and the window of games (`first_time`, `last_time`) are shown beside every figure.

The thresholds are constants in one place (`vault/limited_stats.py`, proposed), tested, and the same wording is used everywhere. The tool computes
`level` and `warning`; the assistant does not decide them.

**Wording of the warning** (exact, `{...}` filled by the tool):

- `too_few`: "Only {n} games in hand for {card} in {set} {format}: too few to say whether it wins more or less than other cards. The win rate ({rate}%, 95% range {low}% to {high}%) is shown for completeness. It must not be used to rank or recommend this card."
- `low`: "Small sample: {n} games in hand for {card} in {set} {format}. Its true win rate could be anywhere from {low}% to {high}% (95% range). Do not treat a difference of less than {width} points from another card as real."
- pick positions with `seen` or `picked` under the floor: "Too few packs ({n}) to say where {card} is usually last seen or taken; the average is not shown." (the figure is withheld below 200, since a position average of a few packs is noise)
- a list that left cards out: "{k} cards were left out of this ranking because they have fewer than 200 games in hand."
- always, in `caveats`: "17Lands data comes from Magic Arena players who use the 17Lands tracker, not from all players or from paper. A win rate in hand does not show that the card causes wins."

## 8. Credits and wording on the website

`public/credits.html` gets one card, under "Card data, images and prices" or a new heading "Limited statistics":

> **17Lands**: Per-card win rates and pick positions in the Limited expert's answers come from 17Lands' public data sets (CC BY 4.0). The Vault's server reads the game and draft files of recent sets once in a while, keeps only per-card counts and throws the files away; the percentages and warnings are computed by the Vault, so they can differ from 17lands.com. Thank you, 17Lands. Consider supporting them on Patreon. The Vault isn't produced or endorsed by 17Lands.

## 9. Where the build touches the repository (so nothing that checks it is missed)

- `vault/migrations`: the three tables; `vault/models.py`; `tests/test_schema_migrations.py`.
- `vault/limited_stats.py` (parsing counters, Wilson interval, levels, wording), `jobs/sync_limited.py`, `.github/workflows/sync-limited.yml`.
- `vault/provenance.py` `CATALOG_SOURCES`: a `limited_17lands` entry (source 17Lands, origin Magic Arena players' games and drafts,
  URL `https://www.17lands.com/public_datasets`, no Wizards material flag; the card names are not Wizards' text).
  `Provenance` gets two optional fields, `licence` (name and URL) and `changes` (what the Vault did), so the licence is data, not only prose; the
  answer schemas and `docs/api.md` follow.
- `docs/compliance.md`: a gate row for `limited_17lands` (Gate `read`, 2026-10-09, `https://www.17lands.com/public_datasets`) and the requirements table;
  `docs/data-sources.md` row; `tests/test_compliance_gate.py`'s lists of allowed jobs and workflows (`sync_limited`, `sync-limited.yml`).
- `jobs/sync_catalog.py` `OTHER_JOBS` (skip the name); `tests/test_workflows.py` (the new workflow follows the same pinned, read-only, `main`-only,
  environment rules; its `alert` job is added to the issue-writers list); `README.md` Security and Attribution, `THIRD_PARTY_NOTICES.md`.
- `public/credits.html`, `skills/vault-attribution`, the MCP `instructions`, `agents/vault-limited-expert.md` (its line "You have no draft
  statistics" changes) and the generated `agent-definitions/`, `plugins/`, `.claude-plugin/` and `public/connect.html` (`python scripts/build_plugin.py`),
  `docs/ai-parity.md`, `public/llms.txt`.
- Neon: the budget text in `docs/catalog-design.md` gets one line (the window's size).

## 10. The tool for the Limited expert

One tool, one API route (CLAUDE.md: an endpoint an agent could use also gets an MCP tool, a response model, `_links`, a paged list):
`GET /api/v1/catalog/limited/{set_code}` and the MCP tool **`get_limited_card_stats`**. Global data, no user id, so no tenancy case (a test
still pins that the route reads no `user_id`). It needs the free account like every tool.

Description (what it does; the workflow lives in the skill): "Win rates and pick positions for the cards of one Limited set, from 17Lands'
public data (Magic Arena, CC BY 4.0): games in hand, win rate in hand, games drawn, where the card is last seen and taken, each with its sample
size and a warning when the sample is small. Cards are given by name, or the best sorted by one metric."

Inputs:

| Input | Type | Notes |
|---|---|---|
| `set` | string, required | 17Lands set code (`HOB`); case-insensitive; Scryfall codes accepted. Unknown or not loaded: the answer lists the loaded sets |
| `format` | `PremierDraft` or `TradDraft`, default `PremierDraft` | |
| `cards` | array of up to 40 names, optional | exact names, either face of a double-faced card; names not found come back in `not_found` |
| `sort` | `win_rate_in_hand` (default), `games_in_hand`, `avg_last_seen_pick`, `avg_taken_at` | used when `cards` is empty; only cards at or above the floor are ranked |
| `limit`, `cursor` | integer up to 50, string | paged |

Output (shape, not final field names):

```json
{
  "set": "HOB", "format": "PremierDraft", "platform": "MTG Arena",
  "data_window": {"first_game": "...", "last_game": "...", "games": 0, "baseline_win_rate": 0.0,
                  "game_file": {"last_modified": "2026-10-01", "fetched_at": "..."},
                  "draft_file": {"last_modified": "2026-10-01", "fetched_at": "..."}},
  "cards": [{
    "name": "...", "oracle_id": "...",
    "games_in_hand": 0, "win_rate_in_hand": 0.0, "win_rate_ci95": [0.0, 0.0], "vs_baseline_points": 0.0,
    "games_in_opening_hand": 0, "win_rate_opening_hand": 0.0,
    "games_drawn": 0, "win_rate_drawn": 0.0,
    "games_played": 0, "win_rate_played": 0.0,
    "times_seen": 0, "avg_last_seen_pick": 0.0, "times_picked": 0, "avg_taken_at": 0.0,
    "sample": {"level": "ok|low|too_few", "n": 0, "warning": null}
  }],
  "not_found": [], "left_out": {"count": 0, "reason": "..."},
  "caveats": ["..."],
  "attribution": "<the text of section 6, filled>",
  "provenance": [
    {"kind": "source", "source": "17Lands",
     "origin": "17Lands users' Magic Arena games and drafts (public data sets)",
     "url": "https://www.17lands.com/public_datasets", "as_of": "2026-10-01",
     "version": "HOB PremierDraft: game file 2026-10-01, draft file 2026-10-01",
     "licence": {"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
     "changes": "Reduced to per-card counts; percentages, intervals and warnings computed by the Vault.",
     "notice": "<the attribution text>"},
    {"kind": "computed", "source": "The Vault",
     "origin": "per-card win rates, positions, intervals and sample warnings, from the 17Lands counts",
     "as_of": "2026-10-01", "inputs": ["<the source block above>"]}
  ]
}
```

The two provenance blocks are never mixed in one field (`docs/compliance.md`). Card names come from the files; `oracle_id` ties them to the catalog so
the expert can call `get_card_oracle` for the text. `win_rate_*` and `avg_*` are null when the sample is under the floor
(win rates are shown for `too_few`, flagged; positions are withheld below the floor, section 7).

The expert (`agents/vault-limited-expert.md`) and a new section of the council skill say: ask for the set and format first (a deck's format does not say which
Arena set); pass the `attribution` line on, in the first sentence that uses a number; give the sample beside every rate; compare cards only when both are `ok`
and the intervals do not overlap; otherwise say the data does not settle it; say it is Arena data; never turn a rate into a grade or a "best pick" claim.
Tool descriptions do not carry the workflow (a test enforces it).

## 11. Tests the build needs

- **Parsing, on small invented CSV fixtures** (not 17Lands' data; the repository holds no copy of it): opening-hand and drawn counts by copy; wins; a game with
  inconsistent data skipped and counted; tutored copies not counted as drawn; ALSA with a wheel (seen at pick 1 and 9 counts 9, once); ATA; 0-based and
  1-based pick numbers; a missing P1P1; a DFC name; an unmatched name keeps a null `oracle_id`.
- **Arithmetic**: the Wilson interval against known values; the levels at 199, 200, 999 and 1,000; the warning wording per level (exact strings); no
  ranking below the floor; positions withheld below the floor.
- **Job, against a twin** (a new `twins/` stand-in for the S3 host, `docs/twins.md`, with the real behaviours seen on 2026-10-09: 200 with `ETag`, `Last-Modified`,
  `Accept-Ranges` for a present file, **403 for an absent one**, `Content-Type: text/csv` for a gzip body; and a conformance check for the same facts in
  `tests/conformance`, run by hand or nightly with the other conformance checks, reading one HEAD only): skip on an unchanged ETag; replace on a changed one; idempotent re-run; a truncated gzip
  writes nothing; missing columns writes nothing; the sanity checks refuse; a file over 400 MB is refused from its `Content-Length`; the per-run byte cap defers the rest; retries only for 429
  and 5xx; a 403 for a previously published file keeps the rows; the window keeps 8 sets and deletes the ninth; the embargo of 14 days; `db_budget` refuses at 85%.
- **Compliance gate**: `limited_17lands` is loadable only with its row; the job refuses an unnamed source; a doc test that the attribution string contains the creator, the licence name and URL,
  the modification statement and the no-endorsement line, and that the credits page, the skill and the expert definition carry it; a test that no row, `draft_id` or per-game value is ever stored (the
  tables have only the columns above); a test that nothing in the repository holds a 17Lands file; the workflow tests of `test_workflows.py` for the new file.
- **Tool**: the answer has `provenance` (source and computed blocks, licence and changes), `attribution`, sample level per card, paging and `_links`; unknown set lists the loaded sets; the tool appears in the
  tool classification tests (`tests/test_agents.py`), the skills' `vault-tools` check, the plugin build, and the ai-parity doc.
- **After deploy, the owner's way (AGENTS.md section 2)**: a real run in Claude with a real set: ask for the cards of a set and a pick between two named cards, and capture the answer showing
  the attribution at the top, the sample beside each rate, and the refusal to rank a card under 200 games. That evidence goes in the issue.

## 12. The smallest first slice, and the order after it

**Slice 1 (the smallest thing that is real end to end, nothing scheduled):** one set (the newest with both files, HOB today), one format
(`PremierDraft`), both files; the three tables and migration; `vault/limited_stats.py`; `jobs/sync_limited.py` with `workflow_dispatch` only (no cron) and
the `limited_17lands` switch; the S3 twin; the gate row and credits card; `get_limited_card_stats` and its route; the Limited expert definition;
and the tests above for that scope. It downloads about 97 MB once (HOB PremierDraft draft plus game), measures the run time and uncompressed size, opens
the first real header to settle the "not verified" list of section 1, and ends with the real-run evidence. It answers the owner's question "does this
help someone draft?" before any scheduling is built.

**Slice 2:** `TradDraft`, the 8-set rolling window with discovery by HEAD, the weekly schedule, retention, the 90-day terms reminder and the download cap.
**Slice 3 (only if wanted):** Sealed and TradSealed (game file only), then colour-pair slices.

Slices become sub-issues of #178 when the owner has answered section 13.

## 13. What the owner decides (my recommendation for each; the build follows it unless told otherwise)

| # | Decision | Recommendation |
|---|---|---|
| 1 | Formats in the first build | **PremierDraft and TradDraft.** Sealed in slice 3; leave PickTwoDraft, QuickDraft and cubes out |
| 2 | How many sets to keep | **The 8 most recent per format** (about 4 MB, about 1.5 GB to read on a first fill, then little). Fewer is cheaper to refresh, more adds stale advice |
| 3 | The sample floors | **200 games to show a number flagged, 1,000 to rank or recommend** (section 7). Stricter is safer and loses newer sets for longer |
| 4 | Whether the weekly job is switched on in production | **Yes after slice 1 is verified**: the owner adds `limited_17lands` to the repository variable `CATALOG_SOURCES` (the existing switch) after the first manual run is checked. This is the only manual step of the whole plan |
| 5 | Telling 17Lands | **A short courtesy note after slice 1**, not as a condition: the licence does not need permission, and their usage guidelines do not apply to the public files. They publish a contact route on their usage guidelines page (a Discord name). A draft can be added to `docs/outreach-drafts.md`; only the owner sends it. If they ask us to stop or to change the credit, we do |
| 6 | Colour-pair or player-skill slices | **Not in the first build.** Revisit after the tool has been used: they shrink samples about tenfold |
| 7 | Arena data in a paper-first tool | **Say it every time and keep it**: the data describes Arena. No decision beyond the wording of section 7, unless the owner wants the Limited expert to be silent when a set is not on Arena |

Left to the build, not the owner: the migration number, the exact thresholds' constants, the run-time measurement and the choice of a faster reader
if the first run is too slow.

## 14. Slice 1 as built (the owner approved the recommended defaults of section 13 on 2026-10-09)

What exists: the three tables and migration `0120` (the design said 0119 or the next free number; this branch was assigned 0120 so that it does not clash with another branch),
`vault/limited_stats.py` (the counters, Wilson interval, levels, exact warnings, attribution, sanity checks), `vault/limited_data.py`
(writing a file's rows in one transaction, and the answer), `jobs/sync_limited.py` and `.github/workflows/sync-limited.yml`, the S3 twin
(`twins/seventeenlands.py`) and its conformance check, the gate row, `GET /api/v1/catalog/limited/{set}` and `get_limited_card_stats`, the
credits card, the `vault-attribution` and `expert-council` skills, the Limited expert, `whoami` (the loaded sets and dates), and their tests.
**Nothing has been downloaded or run against production.** The first real run is the Action `sync-limited`, started by hand with
`workflow_dispatch` once the repository variable `CATALOG_SOURCES` names `limited_17lands` (the only manual step; the workflow never sets it).

Decisions made while building (the design left them to the build or was silent):

| Point | As built |
|---|---|
| Which sets and formats the job reads | The ones it is given (`--sets`, `--formats`, or the workflow inputs); default `DEFAULT_SETS = ("HOB",)` and `DEFAULT_FORMATS = ("PremierDraft",)`, slice 1's scope. `TradDraft` works the same way. Finding the eight most recent sets with HEAD, the 14-day wait after a release, and deleting a set that leaves the window are slice 2 |
| Limits | All of section 4's limits are in: one download at a time, the User-Agent, 400 MB a file from `Content-Length`, 800 MB a run (the rest is `deferred`), three retries (2, 8, 30 s, `Retry-After` wins) for connection errors, 429 and 5xx only, the 85% and 70% budget checks, a 120-minute cap. Smallest file first |
| Extra column | `limited_sources.first_picks` and `empty_first_picks` (pick-1 rows, and those with an empty pack): the design says the answer repeats 17Lands' caveat about missing P1P1 "when the file shows missing first picks", and that needs a stored count. The caveat is added at 1% or more of the first picks |
| Sample floors | `SHOW_FLOOR = 200` and `RANK_FLOOR = 1000` in `vault/limited_stats.py`. A sorted list contains the cards from 200 games in hand, each with its `level`; the exact "left out" sentence counts those under 200. The position sorts use the same floor on packs seen or picks, with the sentence's noun changed ("packs in which they were seen", "picks") |
| `{width}` in the small-sample warning | The width of the 95% range in points (high minus low), one decimal: two cards whose ranges overlap are not told apart |
| Rates under the floor | `win_rate_in_hand` is always given when there are games (flagged, with its range); the opening-hand, drawn and played rates, and the two position averages, are null under 200 of their own sample (their counts are always given) |
| Sort direction | `win_rate_in_hand` and `games_in_hand` highest first; `avg_last_seen_pick` and `avg_taken_at` earliest first |
| Draft-file memory | Drafts already read are remembered as hashes (to refuse a file whose drafts are not contiguous); one pack round's last-seen picks are held at a time |
| What a run keeps of a game | The first and last game time of the games used, nothing else; a game with a missing or unreadable `won` is counted as skipped |
| Source key | `limited_17lands` is in `sync_catalog.OTHER_JOBS` (the catalog job skips it), in `provenance.CATALOG_SOURCES` (so `whoami` carries its notice) and in the gate; a `catalog_sources` row is written with each file |

Not in slice 1: the 8-set window and its discovery, the 14-day embargo, deleting sets that left the window, Sealed formats, colour-pair
slices, the 90-day terms reminder issue and the job's refusal after 120 days, and the Limited expert's real-app evidence after deploy (section 11,
last bullet).
