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
| `rules_versions` | `version` (CR effective date) | effective date, source URL, fetched_at |
| `rules` | `(version, number)` | rule number (e.g. `613.1`), text, parent number, kind (rule, subrule, glossary) |
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
| `rules` (CR) | about 2 to 3 MB per version | estimate, not measured; about 3k rules plus glossary |
| **Total, one CR version** | **about 110 to 125 MB** | |

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
| Compute | 100 CU-hours per project per month; scale to zero after 5 minutes idle (cannot be turned off); autoscale up to 2 CU. When used up, connections drop and new ones are refused until the next period | Daily jobs, first-request wake-ups, heavy tool queries (full-text search) and agents calling the MCP server all burn compute | CU-hours used this month (Neon console or API), alert at 70% |
| Network transfer | 5 GB per project per month (public egress) | Large MCP responses and exports | Keep responses small and paged (already a rule); watch monthly total |
| Restore history | 6 hours, capped at 1 GB of change data | Daily jobs that rewrite whole tables create a lot of change data. **Unverified:** whether this counts toward storage; treat as a risk | Prefer upserts that touch only changed rows; check after the first real run |
| Branches / projects | 10 branches, 100 projects | Not a constraint today | Nothing |

Proposed guardrails, enforced by a small `jobs/db_budget.py` that every job calls first and last:

- Log database size and the 8 largest tables on every run.
- **Warn at 70% (700 MB):** the job fails with a clear message and opens or updates a GitHub issue.
- **Refuse ingestion at 85% (850 MB)** so imports and sign-ins keep working with headroom.
- A planned budget: catalog about 120 MB, user data (entries, decks, tokens, passkeys) a small
  amount that grows with users, price history at most about 400 MB, and at least 25% kept free.

## Price history

### The problem

`price_snapshots` has one row for every owned printing, for every day, for everyone. Rows are
shared across users (key: printing and day), so growth is:

`distinct printings owned by anyone` x `days kept` x `bytes per row`

The current row is wide (a 36-character key, a date and six double-precision prices), about 137
bytes with its index; this matches the README's own estimate of 0.5 GB a year for one 10,000-printing
collection. Two things make it dangerous on a free database:

1. **It never stops growing.** There is no retention rule today.
2. **It scales with users.** Every new user who owns cards nobody else owns adds printings, and each
   one adds a row every day, forever.

Estimates (not measured; row sizes as above, "compact" assumes integer cents and a 16-byte key at
about 60 bytes per row):

| Distinct printings | Daily, 365 days (137 B) | Tiered (129 points, 137 B) | Tiered, compact (60 B) |
|---|---|---|---|
| 10,000 | about 500 MB | about 177 MB | about 77 MB |
| 30,000 | about 1.5 GB | about 530 MB | about 230 MB |
| 100,000 | about 5 GB | about 1.8 GB | about 770 MB |

### Proposal: keep at most one year, and thin it

Agreed: **nothing older than 365 days is kept.** That also suits a free, privacy-minded app (no
indefinite accumulation). But the table shows one year of *daily* points still does not fit, so:

1. **Cap:** delete price rows older than 365 days (daily job).
2. **Thin:** keep daily points for the last 90 days, then one point per week up to 365 days.
3. **Compact** the row in a later migration (integer cents, shorter key, drop columns nobody reads).
4. **Per-user value history** (`collection_values`, one row per user per day, a few bytes) keeps the
   same one-year cap for consistency; the chart still works.
5. Cap the number of printings priced from the union of everyone's collections only if the budget
   guardrail trips; do not decide that now.

This is a separate migration and job (tracked as its own issue) and does not block the catalog.

## Tags (role tags)

Scryfall publishes its functional **oracle tags** (Tagger) as a bulk file. This replaces the idea of
deriving roles from Oracle text with our own heuristics ([#18](https://github.com/colombod-personal/the-vault/issues/18)):
the tags are maintained by a community, versioned by Scryfall, and each card-tag link carries a
weight.

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
- Credit Scryfall Tagger contributors on `public/credits.html`. **Open item:** confirm Scryfall's
  terms for redistributing tag data in our tool answers.

## Rulings and rules

- Rulings: loaded as-is, source preserved (`wotc` vs `scryfall`). Tools return the verbatim comment,
  date and source so quotes can be verified.
- Comprehensive Rules ([#16](https://github.com/colombod-personal/the-vault/issues/16)): parse the
  official text into numbered rows and keep each version (`rules_versions`). An answer records which
  CR version it used, so a cited rule stays reproducible after the next update. **Open item:** check
  Wizards' terms for reusing the CR text and how to credit it.
- Search: Postgres full-text (`to_tsvector('english', ...)`) as expression indexes, plus a
  trigram index on card names for typos. No extra services.

## Legalities

Taken from `oracle_cards.legalities` (jsonb with 23 formats). A small `legality_changes` log
(oracle_id, format, old, new, observed_on) is written when the daily job sees a change, so
`deck_legality` can say "as of" and a later answer can be compared. Detailed design is in
[#17](https://github.com/colombod-personal/the-vault/issues/17).

## Prices for any card

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
- Schema changes come through Alembic migrations as usual (`vault/migrations`).

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
