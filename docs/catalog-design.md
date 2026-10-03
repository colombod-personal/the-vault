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

The README states Neon's free tier as 0.5 GB, so the catalog is about a quarter of it.
**Open item:** confirm Neon's current free-tier limit before accepting this budget.

### What would blow the budget

- **Daily price history for every card.** 32.7k cards x 365 days is about 12 million rows a year,
  roughly 0.5 GB. Keep daily history only for owned printings (the existing `price_snapshots`) and
  store only today's price in `oracle_prices`. If history for all cards is ever wanted, keep weekly
  points.
- **All 235k tag links.** Store a curated subset (below).
- **Existing growth.** The README already estimates `price_snapshots` at about 0.5 GB a year for one
  10k-printing collection. That is the real pressure on the free tier, with or without the catalog.
  Separate decision: a retention rule (for example daily for 90 days, then weekly).

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
- Each table is replaced inside one transaction using `COPY` into a staging table and a swap or
  upsert, so readers never see a half-loaded catalog. The job is idempotent and safe to re-run.
- Writes `catalog_sources` last. `whoami` and every cited answer read the stamps from it.
- Schema changes come through Alembic migrations as usual (`vault/migrations`).

## Tests and twins

- Teach the Scryfall twin the three new bulk files and add conformance checks in
  `tests/conformance` ([#21](https://github.com/colombod-personal/the-vault/issues/21)).
- Tests use the twin universe only; none reaches real services.

## Open items

1. Confirm Neon's current free-tier storage and compute limits.
2. Scryfall terms for redistributing oracle tags in answers.
3. Wizards' terms for the Comprehensive Rules text.
4. Retention rule for `price_snapshots` (existing growth).
5. Final list of root tags to link (start from the ones named above).
6. Whether to keep tokens in `oracle_cards` (922 rows, 2 MB) or flag and filter them.

## Decisions requested in review

- Keys stay `varchar(36)` for consistency with `cards.oracle_id` (costs a few MB; mixing `uuid` and
  `varchar(36)` would force casts on joins).
- Curated tag links, not all 235k.
- Current-only prices for non-owned cards.
- Expression full-text indexes, not stored `tsvector` columns.
