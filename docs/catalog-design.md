# Catalog design (grounding data for agents)

Status: proposal for [#14](https://github.com/colombod-personal/the-vault/issues/14), milestone M1.
Sizes below were **measured**, not guessed: on 2026-10-04 the real Scryfall bulk files were loaded
into a throwaway Postgres 16 with the draft schema.

## Why

Agents (through the MCP server) must answer card, rules and deck questions from our own rows, with
sources, instead of from memory. Today the `cards` table holds only printings that someone owns,
and there are no rulings, rules, legalities, tags or prices for other cards. This design adds a
**global, read-only catalog** that the daily job fills and the MCP tools read.

The catalog holds no personal data: no `user_id`, nothing to erase or export, no tenancy rules, and
no change to `public/privacy.html` beyond crediting the new sources.

**Compliance gate.** Nothing in this catalog is ingested or served until the source terms have been
checked and the provenance rules in [`compliance.md`](compliance.md) are implemented. Every datum we
return carries its source, and nothing is ever presented as the Vault's own. Wizards' text (rules,
rulings, card text) needs particular care; see the compliance document.

## What Scryfall gives us (2026-10-04)

| Bulk file | Compressed | Content |
|---|---|---|
| `oracle_cards` | 23.5 MB | 38,705 cards, one per Oracle ID (3,214 multi-face; 2,243 art series, 922 tokens, 1,916 digital-only) |
| `rulings` | 5.2 MB | 79,706 rulings for 20,080 cards (79,637 from Wizards, 69 from Scryfall), about 16 MB of text |
| `oracle_tags` | 5.7 MB | 4,560 functional tags (Scryfall Tagger), 235,037 tag-to-card links, with a parent/child tree |
| `default_cards` (already synced) | 75 MB | every printing, used today for prices |

The bulk files are now JSON Lines (`jsonl_download_uri`, `compressed_size`); `mtg_toolkits`
already handles that format.

## Tables

All keys follow the existing schema: Scryfall ids as `varchar(36)`. Names are proposals.

| Table | Key | Content |
|---|---|---|
| `oracle_cards` | `oracle_id` | name, layout, mana cost, cmc, type line, Oracle text, P/T/loyalty/defense, colors, color identity, keywords, produced mana, legalities (jsonb), game_changer, edhrec_rank, released_at, faces (jsonb, per-face text), Scryfall URI, representative printing id (for images), digital flag |
| `rulings` | serial id; index on `oracle_id` | oracle_id, published_at, source (`wotc` or `scryfall`), comment |
| ~~`rules_versions`~~, ~~`rules`~~ | | **Not built; dropped by migration 0104.** The Comprehensive Rules are read live from Wizards and nothing of them is stored (owner decision 2026-10-05, #142; `docs/rules-index.md`) |
| `oracle_tags` | tag id | slug, label, description, parent_ids, child_ids |
| `oracle_tag_links` | `(tag_id, oracle_id)` | weight (strong/median/weak...). Curated subset only (see Tags) |
| `oracle_prices` | `oracle_id` | cheapest acceptable printing id, usd, usd_foil, eur, day, source. **Current value only** (see Prices) |
| `catalog_sources` | source name | what was loaded: version stamp, bulk `updated_at`, fetched_at, row counts, checksum. Feeds the `whoami` tool and every "as of" line |

Not stored: `art_series` cards (not playable), flavor text, images (hotlinked from Scryfall as now),
Arena/MTGO ids, purchase URIs.

Relationship to `cards`: `cards` stays as the owned-printings table. Join on `oracle_id`; do not
merge them.

## Measured size

Real data, Postgres 16, `VACUUM ANALYZE` done. Totals include indexes.

| Table | Size | Notes |
|---|---|---|
| `oracle_cards` | about 53 MB | 42 MB table, PK and lower(name) indexes, full-text GIN index as an expression index (a stored `tsvector` column measured 9 MB larger), trigram index on name for fuzzy matching |
| `rulings` | about 30 MB | 23 MB table plus a GIN full-text expression index (a stored `tsvector` column made it 47.5 MB) and an `oracle_id` index |
| `oracle_tag_links` | 9 to 16 MB | the full 235k links measured **60 MB** with `varchar(36)` keys (32 MB with native `uuid`). A curated subset of about 586 tags and 55k links is 8.7 MB with `uuid`, roughly 16 MB with `varchar(36)` |
| `oracle_tags` | 1.5 MB | all 4,560 tags |
| `oracle_prices` | about 7 MB | 32,765 cards currently have a USD price |
| ~~`rules` (CR)~~ | none | not stored any more: read live from Wizards (the estimate was 2 to 3 MB; the loaded table measured 3 MB on 2026-10-04) |
| **Total** | **about 110 to 125 MB** (about 108 to 122 MB without the stored rules) | |

Neon's free plan (checked 2026-10-04 against [Neon's FAQ](https://neon.com/faqs/free-plan-limits-and-quotas))
gives **1 GB of storage per project** (the README said 0.5 GB; corrected), so the catalog is about an eighth
of it. See [Neon budget](#neon-budget) for everything we must track.

### What would blow the budget

- **Daily price history for every card.** 32.7k cards x 365 days is about 12 million rows a year,
  roughly 0.5 GB. Keep history only for owned printings (the existing `price_snapshots`) and store
  only today's price in `oracle_prices`.
- **All 235k tag links.** Store a curated subset (below).
- **Existing price history.** This is the real pressure on the free tier; see
  [Price history](#price-history).

## Neon budget

Limits that matter on the free plan (source: Neon's FAQ above):

| Limit | Value | Why it matters to us | What we track |
|---|---|---|---|
| Storage | 1 GB per project, continuous (does not reset). When exceeded, inserts, updates and deletes that grow storage **fail**; data is not deleted | The daily jobs would start failing, and so would imports | Database size and the size of each large table, at the end of every job run |
| Compute | 100 CU-hours per project per month; scale to zero after 5 minutes idle (cannot be turned off); autoscale up to 2 CU. When used up, connections drop and new ones are refused until the next period | Daily jobs, first-request wake-ups, heavy tool queries (full-text search) and agents calling the MCP server all burn compute | CU-hours used this month: read from Neon's API every day **when an API key is configured**, otherwise by a person once a month (reminder issue); alert at 70% |
| Network transfer | 5 GB per project per month (public egress) | Large MCP responses and exports | Keep responses small and paged (already a rule); watch monthly total |
| Restore history | 6 hours, capped at 1 GB of change data | Daily jobs that rewrite whole tables create a lot of change data. **Unverified:** whether this counts toward storage; treat as a risk | Prefer upserts that touch only changed rows; check after the first real run |
| Branches / projects | 10 branches, 100 projects | Not a constraint today | Nothing |

Neon's history (point-in-time restore) is a separate billing meter, not part of the 1 GB storage line: on the free plan the window is 6 hours, capped at 1 GB-month of change history, and the plans page does not say that history counts toward the storage cap (read 2026-10-09, [Neon plans](https://neon.com/docs/introduction/plans)). The console read of 2026-10-09 (the owner's signed-in console, read only; organization usage since 1 October) shows the meters apart: compute 14.37 of 100 CU-hours, storage 0.31 GB, **History 0 GB**, network transfer 1.25 GB of 5; the-vault-db 241.34 MB of 1 GB, after the catalog loads and the price syncs of the month. So history is not counted in the storage figure and added nothing (#64).

Guardrails, built (#64) in `jobs/db_budget.py`, `jobs/neon_usage.py` and `jobs/budget_alert.py`, with the workflows that call them:

- Log database size and the 8 largest tables on every run (first and last check of every job).
- **At 70% (700 MB of 1,024): the job fails.** The last check of the price and catalog jobs fails the job with a clear message
  (`db_budget: Neon storage is at 72% of the 1024 MB free plan ... Failing the job on purpose, after its work was done`), **after**
  the job's own work: failing first would stop the retention that frees space. The message also goes to the step's output, and the
  workflow's second job, `alert`, opens a GitHub issue (or updates the open one: one issue per kind, found by a hidden marker; a
  comment when the figure moved five points) through the GitHub API with the run's own `GITHUB_TOKEN`.
  That job holds `issues: write` and nothing else: no environment, no secret, no database address (`tests/test_workflows.py` pins it).
  Nothing closes an alert; a person does.
- **At 85% (850 MB): ingestion is refused** before the catalog load starts, so imports and sign-ins keep working with headroom.
- **Compute hours and network transfer** (the database cannot see them): `jobs/neon_usage.py` reads the project from Neon's console
  API (`GET /api/v2/projects/{id}`: `compute_time_seconds` / 3600 as CU-hours, `data_transfer_bytes`, `synthetic_storage_size`, and the
  billing period) after the daily price job and fails at 70% of 100 CU-hours, 5 GB of transfer or 1 GB of storage, with the same
  issue mechanism. **This is automated only if the owner creates a Neon API key** and stores it as the `NEON_API_KEY` secret
  (and the project id as the `NEON_PROJECT_ID` variable) in the `vercel-production` environment. Without them the step prints a
  notice and passes. No key was available to this work, so the live call has not been run: it is tested against a twin of the API
  built from Neon's documentation and its real 401 answer (`twins/neon.py`; the field check against a real project is
  `tests/conformance`, by hand, needing the key). `compute_time_seconds` is Neon's count of CPU seconds, which at one vCPU per compute
  unit is CU-seconds: an estimate, the Neon console being the authority on billing.
- **Monthly reminder (`neon-monthly-check.yml`):** on the 1st of every month a workflow with `issues: write` opens (or nags on) an
  issue with the checklist: CU-hours, network transfer, storage, and the one open question below (restore history). It reads
  nothing and needs no key; it exists so the manual check cannot be forgotten.
- What is **not** automated: the restore-history question (whether Neon's 6-hour history counts toward storage) needs one look in the
  console after a real job run; the monthly issue carries it as a checkbox. The limits themselves were read on 2026-10-04.
- A planned budget: catalog about 120 MB, user data (entries, decks, tokens, passkeys) a small
  amount that grows with users, price history at most about 400 MB, and at least 25% kept free.

## Price history

### The problem

`price_snapshots` has one row for every owned printing, for every day, for everyone. Rows are
shared across users (key: printing and day), so growth is:

`distinct printings owned by anyone` x `days kept` x `bytes per row`

The current row is wide (a 36-character key, a date and six double-precision prices). **Measured**
(300,000 rows, Postgres 16, `VACUUM ANALYZE`): **237 bytes a row**, 126 in the table and 110 in the
primary-key index. That is more than the README's earlier estimate (about 0.5 GB a year for 10,000
printings; measured, it is about 0.86 GB). Two things make it dangerous on a free database:

1. **It never stops growing.** There is no retention rule today.
2. **It scales with users.** Every new user who owns cards nobody else owns adds printings, and each
   one adds a row every day, forever.

Computed from the measured 237 bytes a row, and from the **measured** compact row after migration 0113 (107 bytes: see
"Compaction, measured" below, which replaces the earlier estimate of about 96):

| Distinct printings | Daily, 365 days (237 B) | Tiered (122 points, 237 B) | Tiered, compact (measured 107 B) |
|---|---|---|---|
| 10,000 | about 860 MB | about 290 MB | about 130 MB |
| 30,000 | about 2.6 GB | about 870 MB | about 390 MB |
| 100,000 | about 8.6 GB | about 2.9 GB | about 1.3 GB |

**Consequence:** retention alone keeps 10,000 printings comfortable (290 MB of 1 GB), but past about
20,000 distinct printings across all users even the tiered table is too big for the free tier. The
compact row (now built) moves the line, for the 400 MB planned for price history, from about 14,000 to about 30,000 printings (122 points
x 237 B against x 107 B); storing a row only when a price changed would move it again and is not built. The budget guard (#64) is what tells us when.

"Tiered" is 90 daily points, then 26 weekly points (the next 6 months), then 2 points a month for
the last 3 months (6 points): 122 points a year.

### Proposal: keep at most one year, and thin it

Agreed: **nothing older than 365 days is kept.** That also suits a free, privacy-minded app (no
indefinite accumulation). But the table shows one year of *daily* points still does not fit, so:

1. **Cap:** delete price rows older than 365 days (daily job).
2. **Thin** (owner's decision): daily points for the last 90 days; then **weekly** points for the
   next 6 months (one per ISO week, the latest day with data); then **two points a month** (the
   1st and the 15th, or the nearest day with data) for the remaining months up to 365 days.
3. **Compact** the row (**built: migration 0113, #63**): integer cents instead of double-precision prices, a native 16-byte `uuid`
   key instead of `varchar(36)`, and `eur_etched` dropped (no reader, screen or export used it). See below.
4. **Per-user value history** (`collection_values`, one row per user per day, a few bytes) keeps the
   same one-year cap for consistency; the chart still works.
5. Cap the number of printings priced from the union of everyone's collections only if the budget
   guardrail trips; do not decide that now.

Retention and compaction are separate from the catalog (`vault/retention.py`, migration 0113) and did not block it.

### Compaction, measured (#63)

Migration 0113 run on 300,000 real-shaped rows (3,000 printings x 100 days; random v4 UUIDs, prices to the cent), Postgres 16, `VACUUM ANALYZE`,
the bytes a row being `pg_total_relation_size / rows` (table plus primary-key index). Reproduce with the test that checks it
(`tests/test_price_compaction.py` prints the numbers on a smaller table and fails if the row is not at least a quarter smaller).

| Data | Before: bytes a row (table + index) | After: bytes a row (table + index) | Saved | Upgrade, downgrade |
|---|---|---|---|---|
| all six prices present | 209 (126 + 83) = 59.8 MB | 111 (70 + 41) = 31.7 MB | 47% | 1.2 s, 0.7 s |
| typical gaps (about 60% foil, 5% etched) | 186 (103 + 83) = 53.3 MB | 105 (65 + 41) = 30.2 MB | 43% | 1.1 s, 0.7 s |

Loaded the way the daily job loads it (one day at a time, printings in no order) the typical shape is 186 bytes a row before and,
in a fresh table of the new shape, **116** after (table 65 + index 50: a freshly built index is denser than one grown by inserts, so
107 right after the migration drifts toward 116 as days are added).
The earlier **237 bytes** (126 + 110) was not reproduced: with all six prices present this run measures 209 (the same table, 126 bytes, but an
index of 83 against 110; the earlier index was probably built or filled differently, which was not recorded). The saving is what
matters and it is the same in every shape tried: **43% to 47%**. These are generated rows, **not production's**: production's table is
measured every day by the budget guard (`db_budget: tables_mb`, `price_snapshots`, in the job log), so the real before and after are the
logged sizes of the run before and the run after the deploy that carries 0113.

**Production's figure (read from the daily job's logs on 2026-10-09; the deploy of 0113 was on 2026-10-07 around 11:00).** The run of
6 October ("after the price sync") logged `price_snapshots` at **7.2 MB**; the run of 7 October ("before the price sync", the first run
after the migration) logged **4.1 MB**: **43% smaller**, the figure the generated rows predicted (43% to 47%). Since then a day's sync adds
about 0.4 MB to 1.9 MB (6.0 MB before and 6.4 MB after the run of 8 October), and the thinning of `docs/catalog-design.md` "Price history"
keeps the table from growing past a year of points. The whole database was 207.8 MB (20% of the free plan) on 8 October.

Why a `uuid` and not the 4-byte integer the first estimate assumed: an integer key needs a printing-number table and a join in every
reader; the native uuid gets most of the saving (the key is 16 bytes instead of 37, in the table and in the index) with no new table.
The cost is that a text id that is not a UUID can no longer be a key. Scryfall's ids are UUIDs, but `entries.scryfall_id` can hold
whatever a user's CSV had in its id column, so every reader goes through `vault.prices.valid_ids` / `uuid_sql` (a malformed id is "no
price", never a database error), and writers skip a card whose id is not a UUID.

### Migration plan for 0113 (and for the catalog tables, #14)

| Step | What happens | Size | Time measured | Rollback |
|---|---|---|---|---|
| 1. Deploy | The app applies migrations at startup (`Database.migrate`, behind an advisory lock). 0113 first deletes rows whose id is not a UUID (none can come from Scryfall) | none | instant | restore from a Neon branch / point-in-time restore taken before the deploy |
| 2. Backfill | One `ALTER TABLE` rewrites the table and its primary key once: key to `uuid`, each price to whole cents (`round(price x 100)`; NULL for NULL, NaN, infinity or above $10,000,000), `eur_etched` dropped. Transactional: a failure leaves the old table | needs room for a second copy while it runs: about 0.6 of the table plus its index (a 60 MB table needs about 35 MB spare), then the old one is freed | 1.2 s for 300,000 rows (about 4 s a million) | automatic with the transaction |
| 3. Rename | The five price columns become `*_cents` (metadata only) | none | instant | in `downgrade` |
| 4. Verify | The next daily job logs `price_snapshots` in `tables_mb`; compare with the previous run | | | |
| Rollback after deploy | `alembic downgrade 0111` (the migration's `downgrade`): the key back to text, cents back to dollars (exact: `cents / 100.0` is the nearest double to the price), `eur_etched` restored empty. The app code before this release cannot read the new table, so a downgrade needs the previous release too | same as step 2 | 0.7 s for 300,000 rows | |

The catalog tables follow the same pattern (`vault/migrations`, #14): **0008** creates them empty at startup (no data, no size), **0009** adds the
artist and image columns, **0104** drops the stored rules (the rules are read live, empty in production). Data arrives only when a source is
enabled in `CATALOG_SOURCES`, one source at a time, after its terms are checked (`docs/compliance.md`):

| Step | What | Size added (measured 2026-10-04, real files) | Rollback |
|---|---|---|---|
| 1 | migration 0008 (tables, indexes, `pg_trgm`) | about 0 | `alembic downgrade` drops the tables |
| 2 | `oracle_cards` loaded by `jobs.sync_catalog` | 55 MB | remove it from `CATALOG_SOURCES`: the tools say the catalog is not loaded (503); delete the rows or downgrade to reclaim the space |
| 3 | `rulings` | 34 MB | same |
| 4 | `oracle_tags` (all tags, curated links) | 3 MB tags + 16 MB links | same |
| 5 | `oracle_prices` (by the price job, from the file it already downloads) | about 7 MB | same |
| 6 | retention and compaction of price history (0113, `vault/retention.py`) | frees space | above |

The first full load took 39 seconds in all (per-source times were not recorded); a repeat with nothing changed took 6 seconds and wrote
no row. Every load is idempotent (content hashes), so a failed or repeated load is safe, and nothing here needs a backfill beyond that first load.

## Tags (role tags)

Scryfall publishes its functional **oracle tags** (Tagger) as a bulk file. They are the primary source of roles: maintained by a
community, versioned by Scryfall, and each card-tag link carries a weight. An earlier version of this page said they *replace* deriving roles
from Oracle text ([#18](https://github.com/colombod-personal/the-vault/issues/18)); that was the author's proposal and never an owner
decision, and #18 asks for tags derived from Oracle text with documented rules. So both exist: **Tagger wins, and a small set of
documented, tested rules over the Oracle text fills the roles Tagger has no tag for**, always shown as computed by the Vault with the
rule's id, never as Scryfall's (`vault/role_rules.py`, rules and their known failures in `docs/card-roles-design.md`).

Findings that shape the design:

- Tags form a tree. A parent such as `removal` has **no direct links** (0); its children do
  (`removal-destroy` 1,713, `removal-exile` 452, `removal-creature` 1,925). Queries must roll
  descendants up with a recursive query over `child_ids`.
- Several useful roots exist: `ramp` (548) with `mana-rock`, `mana-dork` and `land-ramp`; `sweeper` (741);
  `counterspell` (98); `spot-removal` (5,508); `draw-engine` (1,576); `card-advantage`; `sacrifice-outlet`;
  `evasion`; `reanimate`. A few names I expected (`draw`, `tutor`, `recursion`) have no direct links
  and need their child tags mapped by hand.
- Store all 4,560 tags (1.5 MB) but links only for a curated set of root tags and their descendants.
  Adding a root later is a re-run of the job, not a migration.
- **Provenance rule for tools:** tag answers say "tagged `ramp` by Scryfall's community Tagger
  (weight: median)", never "this is a ramp card" as if it were a rule. Tags are opinions.
- Scryfall Tagger contributors are credited on `public/credits.html` (done, #18; `tests/test_credits.py` fails when a used source is
  not credited). **Open item:** confirm Scryfall's terms for redistributing tag data in our tool answers.

## Rulings and rules

- Rulings: loaded as-is, source preserved (`wotc` vs `scryfall`). Tools return the verbatim comment,
  date and source so quotes can be verified.
- Comprehensive Rules ([#16](https://github.com/colombod-personal/the-vault/issues/16)): **not stored.** The first design parsed the official
  text into rows and kept each version; the owner decided instead (2026-10-05, #142) to read the current edition live from Wizards
  (`vault/rules_live.py`, `docs/rules-index.md`). An answer records which edition it used (its effective date) and links Wizards' document,
  and Wizards, the Comprehensive Rules and the Fan Content notice are credited on `public/credits.html`.
- Search: Postgres full-text (`to_tsvector('english', ...)`) as expression indexes, plus a
  trigram index on card names for typos. No extra services.

## Legalities

Taken from `oracle_cards.legalities` (jsonb with 23 formats). A small `legality_changes` log
(oracle_id, format, old, new, observed_on) is written when the daily job sees a change, so
`deck_legality` can say "as of" and a later answer can be compared. Detailed design is in
[#17](https://github.com/colombod-personal/the-vault/issues/17).

**Read path (#17).** The log is read by `vault.catalog_queries.legality_changes`: `GET /api/v1/catalog/cards` (tool
`get_card_oracle`) returns the card's changes as `legality_changes`, newest first (at most 25), and `POST /api/v1/decks/legality`
(tool `deck_legality`) returns the changes of the deck's cards in the asked format as `changes`. Both carry a note that says
what the record is: the day the Vault *saw* a change (not the day Wizards announced it), and nothing before the Vault first
loaded the card data. The card answer adds a `computed` provenance block (the Vault's comparison of Scryfall's legalities).
The Vault does not import older ban history, so an assistant must not say a card "was never banned" from an empty list.
Tests: `tests/test_legality_history.py`.

## Prices for any card

`oracle_printings` (#29) holds one row per priced paper printing (about 85,000): its set, language and the nonfoil, foil and etched USD price, so a shopping list can pick the cheapest printing under a person's finish, language and set rules. It is loaded by the same daily price job from the same file, as a diff (rows whose figures did not change are not rewritten), only when `oracle_printings` is in `CATALOG_SOURCES`, and its day is the `catalog_sources` row (no per-row day). Excluded: digital cards, tokens, emblems, art cards, oversized cards and memorabilia. The size is an estimate (about 25 MB with its indexes): measure `pg_total_relation_size('oracle_printings')` after the first load and record it in the next row of the budget table before leaving it enabled.

`oracle_prices` holds one row per card: the cheapest printing that has a price, with its date and
source. The daily price job already loads `default_cards`, so this is one extra pass over data we
already download. Cardmarket's public price guide and Card Kingdom's price list are separate
sources to evaluate in [#19](https://github.com/colombod-personal/the-vault/issues/19); each price
row keeps its source so tools never mix them silently. Combos are not ingested: whether to cache
Commander Spellbook results or query on demand is decided in
[#20](https://github.com/colombod-personal/the-vault/issues/20) after checking their data licence.

## Ingestion

- New job `jobs/sync_catalog.py`, run from its own GitHub Actions workflow on `main` only (same
  security rules as `sync-prices`: hash-checked dependencies, secrets only from the
  `vercel-production` environment). It does not run on Vercel, so function limits do not apply.
- Downloads about 34 MB a day (oracle cards, rulings, tags). Skips a source when its bulk
  `updated_at` equals the stored stamp in `catalog_sources`.
- Each table is updated inside one transaction by loading into a staging table and **upserting only
  rows whose content hash changed**, then deleting rows that disappeared. No whole-table rewrites:
  that keeps Neon's change history small and readers never see a half-loaded catalog. The job is
  idempotent and safe to re-run.
- Writes `catalog_sources` last. `whoami` and every cited answer read the stamps from it.
- Schema changes come through Alembic migrations as usual (`vault/migrations`). A migration must keep the previous release working on the new schema (add, do not rename or drop in the same release): an older instance that cold-starts after a newer deploy migrated the database now boots on the newer schema instead of failing (`database_ahead_of_code` is logged, #169), so a destructive change needs two releases.

## Tests and twins

- Teach the Scryfall twin the three new bulk files and add conformance checks in
  `tests/conformance` ([#21](https://github.com/colombod-personal/the-vault/issues/21)).
- Tests use the twin universe only; none reaches real services.

## Decisions

Settled:

- Keys stay `varchar(36)` for consistency with `cards.oracle_id` (decided by the owner).
- Price history is capped at one year (decided by the owner); thinning and compaction as above.
- Compliance and provenance come first: see [`compliance.md`](compliance.md).

Proposed, still to confirm:

- Curated tag links, not all 235k.
- Current-only prices for non-owned cards.
- Expression full-text indexes, not stored `tsvector` columns.

## Open items

1. Compliance checks listed in [`compliance.md`](compliance.md) (Scryfall terms, Wizards' terms for the
   Comprehensive Rules and rulings, Moxfield). These gate ingestion of the affected data.
2. Whether Neon's restore history counts toward storage (check after the first real job run).
3. Final list of root tags to link (start from the ones named above).
4. Whether to keep tokens in `oracle_cards` (922 rows, 2 MB) or flag and filter them.

## Measured with the real loaders

On 2026-10-04 the loaders were run on the real bulk files into Postgres 16: first load 39 seconds (36,462 oracle cards, 79,663 rulings, 4,560 tags with 55,557 links, and 4,062 Comprehensive Rules rows, which are no longer loaded: see "Rulings and rules"); a repeat load with no changes 6 seconds and **zero rows written**. Sizes: oracle_cards 55 MB, rulings 34 MB, oracle_tag_links 16 MB, rules 3 MB, oracle_tags 3 MB; about 111 MB of the catalog in a 120 MB database. This matches the estimate above. Real data also caught one mistake in the first draft: glossary terms need a rule-number column of 120 characters, not 20.

## Multi-faced cards: who reads what (audit for #197, 2026-10-07)

Scryfall puts a double-faced card's mana cost, Oracle text and stats on its faces, and (transform and modal cards) its colours
too. The catalog keeps the top-level values as Scryfall gives them (null for those cards), derives the card's `colors` (front
face first), and keeps each face's own values in `faces`. Every reader of colours or text was checked; the helpers that read a
faced card's text are in `vault/card_faces.py`. Evidence: `tests/test_card_faces.py`.

| Reader | What it reads | Verdict |
|---|---|---|
| `catalog_queries.card_body` (`get_card_oracle`, `/catalog/cards`) | top-level fields and `faces` | Correct: top-level text is null for faced cards by design and `faces` carries it; the tool description says so. Colours are derived by the loader. `verify_citation` already quotes per face (#249) |
| `deck_tools.stats`, `legality`, `find_upgrades`, `validate_changes` | `color_identity` (always complete), the type line (complete: `A // B`), `cmc` | Correct. **Fixed:** the "any number of cards named" copy-limit check read the top-level text only (now every face); an upgrade candidate's `mana_cost` was null for a transform or modal card (now the front face's) |
| `simulate.from_oracle` | Oracle text for "enters tapped", mana rocks and creatures, land ramp, "no maximum hand size" | **Fixed:** it read the top-level text, so a modal land with "enters tapped" on its face was simulated as entering untapped, and a faced card's ramp text was never seen. It now reads the front face's text (what the card does as played) |
| `deck_overview` | `color_identity` | Correct |
| `analytics` (colour mix, breakdowns, filters) | `cards.color_identity`, the front type line, `cmc` | Correct: it never reads `colors`; the mix is by colour identity, as its labels say |
| `collection_view`, `catalog.py` (card lookups for the web app) | the owned-printings table `cards` | **Checked, no gap:** `cards` is filled by `mtg_toolkits.Card.from_json`, which takes `colors` from the top level or, if empty, the union of the faces' colours (sorted alphabetically, not front face first), and joins the faces' mana cost, text, power and toughness with `//`. A real-shaped transform card (Delver of Secrets) looked up through the Vault has colours `["U"]`, the front face's cost and both faces' text. This depends on the pinned `mtg-toolkits` commit: if the library changes `from_json`, the test fails |
| the new role derivation (below) | the text of every face | Reads `card_faces.all_text`, so a faced card's removal or ramp text is seen |

Left as is, on purpose: the full-text index `ix_oracle_cards_fts` is on the top-level `oracle_text` only. No query uses it today
(rules text search is the live rules index), so a text search over cards would miss faced cards until the index is rebuilt over the
faces' text; the index must be changed before any card-text search is added.
