# Collections: inventory, buckets, tags and metadata (decision doc for #118)

Status: draft for owner decisions. Nothing here is built. It is the root of the Collections epic (#117): #119 (metadata), #120 (tags), #121 (schema), #122 to #130 wait on it.

## What exists today (checked in the code, 2026-10-06)

- `entries` holds one row per printing, finish, condition, language and **folder**, with `quantity` and `trade_quantity`. Every import **replaces all of a person's entries**; the change against the old collection is stored on `imports.summary`.
- `entries.folder` is Dragon Shield's folder, kept so the CSV round-trips. A stack of 4 in two folders is already **two entries** (3 in one folder, 1 in another). So a copy already lives in exactly one folder; buckets do not need a new idea, only a first-class home for the one we have.
- Sharing (`shares`) grants read access to the whole collection or one deck. Saved decks (`decks`) are decklists, not physical locations.
- An assistant can see a copy's `folder` only one printing at a time (`get_card`). `search_cards`, `list_card_names` and the breakdowns cannot list, filter or group by folder, so today an assistant cannot answer "what is in my trade binder?".
- Personal data is erased and exported through `vault.privacy` (`personal_data`, `export_archive`); every new per-person table must be added there.

## Glossary (one meaning each, used the same in the app, API, MCP and docs)

| Word | Meaning |
|---|---|
| **Inventory** | Everything the person owns. The total is the sum of the buckets, computed, never stored. |
| **Bucket** | A named place copies live in: a binder, a deck box, a trade box, storage. Every copy is in exactly one bucket. |
| **Entry** | One row of the inventory: a printing, finish, condition and language, in one bucket, with a quantity. |
| **Tag** | A label on a card (not on a copy). Tags never change quantities and a card can have many. |
| **Role** | What a card does (ramp, treasure, counterspell). Owned by the Vault and public (#174), not by the person. |

Tags versus roles: a tag is the person's own opinion or plan (`trade`, `commander-staple`, `for-the-sliver-deck`); a role is the Vault's reviewed data about the card. They never share a table: assistants may suggest tags to a person (labelled as computed), and suggestions about roles go through the review queue of #174.

## Decisions (recommendation first; each needs the owner)

### 1. Buckets are the folder, made first-class

- New `buckets` table (person, name, kind, position, optional `vault_metadata`) and `entries.bucket_id`. **Backfill:** one bucket per distinct `folder` value; entries with no folder go to a default bucket named **"Unsorted"** (the person can rename it).
- **Source folder is kept on the entry.** `entries.folder` stays exactly as written in the file (including blank, `Unsorted` and case-only variants), and `entries.bucket_id` is the Vault's grouping of it. Export writes `entries.folder`, never the bucket name, so the CSV round-trip (`tests/test_api.py::test_reimport_records_changes_and_export_round_trips`) stays byte-identical: a blank folder stays blank, a literal "Unsorted" stays "Unsorted", `Box` and `box` stay distinct on export even though bucket names compare case-insensitively (they map to one bucket). Renaming a bucket changes the Vault's label only; it does not rewrite exported folders. An explicit move of copies to another bucket writes the target bucket's name to `entries.folder` of the moved rows (a deliberate edit, recorded as a change), and that is the only way an export's folder changes.
- **History keeps the same identity.** Change-set rows (#193/#194) record the source folder string and the bucket id; the pre-#121 baseline therefore distinguishes edits per folder, not just per default bucket, and a folder with no remaining entries stays recoverable from history.
- A stack split across buckets is two entries, as now. No change to entry granularity.
- Limit: the backfill never merges or rejects existing data: an inventory with more distinct folders than the cap (the tests accept 501) keeps one bucket per folder. The cap of 100 applies only to buckets a person **creates** or to an **import that would add** buckets past it; such an import is rejected atomically with a preview naming the folders, and existing buckets above the cap stay editable and deletable. Names are unique per person, case-insensitive (case-only folder variants share a bucket, see above).
- **Ask:** is "Unsorted" the right default name, and which folders do you use in Dragon Shield today? (No list or breakdown shows folders, which is itself a gap: add `bucket` to the collection card items and a `bucket` filter to the search and breakdown tools, so an assistant can list what is in a bucket. Part of #122 and #130.)

### 2. Tags attach to the card (oracle id), not to the entry or the printing

- Entries are replaced on every import, so nothing can hang off `entries.id`. Tags are keyed `(person, oracle_id, tag)`: they survive imports, and a card that leaves the inventory keeps its tags (shown as "not owned") instead of losing the person's work. Removing a tag is explicit.
- Printing-level tags (a particular foil) are a later extension keyed by `scryfall_id` and finish; not in v1.
- Sources on a tag assignment: `person`, `assistant` (carries which app wrote it and when, per #127: never shown as the person's own), `system` (for example `imported:<file>`).
- **Unmatched copies are not tags.** An entry the importer could not match has neither a Scryfall id nor an oracle id (`vault/importer.py`), so it cannot take an `(person, oracle_id, tag)` key. "Unmatched" stays derived per entry from the missing printing, as today, and is shown as a status, never stored as a tag. If the card is matched later, the person's tags can be added then.
- Namespaces are plain text with a colon (`deck:sliver`, `trade:sell`); no curated list in v1. Slug rules: lower case, letters, digits, `-`, `:`; at most 40 characters.
- Limits: at most 50 tags per card and 500 distinct tags per person, so no response grows with the collection (cursor paging everywhere).

### 3. Decks and buckets: related by name, not linked, in v1

- A saved deck is a list of what the person wants; a bucket is where copies are. A deck box can be a bucket with the same name as the deck. No foreign key in v1.
- Later refinement that matters for #165 (deck independence): copies that physically sit in a deck's bucket count as already allocated to that deck. The #165 design counts by name today and says so; it can adopt this without changing its answers' shape.

### 4. Sharing stays whole-inventory in v1

- Today's shares (whole collection, or one deck) are unchanged. Sharing one bucket or one tag is a later extension; the rule stays 404, never 403, for anything the viewer may not see.

### 5. Analytics take a `bucket` and a `tag` filter

- `summary`, `breakdowns`, `valuation`, `names` and `stats` accept `bucket` and `tag` (default: the whole inventory). Additive metrics (copies, market value, paid) summed across buckets equal the inventory total (a test); distinct counts (cards, printings, sets) do **not** add up, because one printing split across two buckets counts once in each bucket but once inventory-wide, so they are computed over the combined inventory (`vault/collection_view.py`, `vault/analytics.py` already count distinct values). The test includes that split-stack case. Detailed in #130.

### 6. One import path (with #193 and #194)

- The owner decided on 2026-10-06 that a re-import becomes a **three-way update** (apply only what changed in the person's app since the last import, keep edits made in the Vault, ask about conflicts; "replace everything" stays), designed in `docs/owned-cards-updates.md` (PR #193, issue #194).
- Buckets must fit that, not fight it: **every change set records its bucket from day one** (the default bucket until #121 lands), so a baseline exists per bucket. **The baseline is the incoming file as imported, stored per scope (the folders it covered), never derived by subtracting change sets from today's inventory:** a card added only in the Vault and kept by a merge is not in the file, so subtracting would wrongly count it as part of the baseline and a second import of the same file would delete it. Each merged import stores its own file snapshot (or its row hashes) as the new baseline for that scope; change sets stay the history. Required test (done in #194: `tests/test_reimport_merge.py::test_a_card_added_only_in_the_vault_survives_a_changed_file_that_still_omits_it_and_the_same_file_again`): import A, add X in the Vault, import a changed file that still omits X (X kept), import the same file again: X is still kept; "import into one bucket" (#124) means a file maps to a single bucket and only that bucket's rows are compared; reset (#129) is the same operation with an empty target in **replace** mode for the selected scope (not ordinary merge mode: a card added only in the Vault is in neither the baseline nor the empty file, and a merge would keep it), with preview and confirmation kept. These three are one code path with different scopes and modes.

### 7. `vault_metadata` (detail in #119)

- `JSONB NOT NULL DEFAULT '{"version": 1}'` with a check that `version` is a positive integer, on buckets, tag assignments and the per-person card annotation; **not** on entries (replaced on import). The card annotation is keyed `(person, oracle_id)`, like card-level tags: it survives imports and stays, shown as "not owned", when the card leaves the inventory. A printing-level annotation is the same later extension as printing-level tags, keyed by `scryfall_id` and finish. Free-form for assistants and connectors, never read by the Vault's own logic, size-capped (8 KB) and labelled with who wrote it.

### 8. GDPR

- New per-person tables (`buckets`, `tag_assignments`, `card_annotations`) are added to `personal_data` (erased with the account), `export_archive` (`buckets.json`, `tags.json` and `card_annotations.json`), `docs/gdpr.md` and `public/privacy.html`; a test fails if a per-person table is missing from them.

## The surface (for #121 to #130)

- REST under `/api/v1/collection/buckets` and `/api/v1/collection/tags` (cursor-paged, HAL `_links`, idempotency keys on writes, 404 for another person's ids); the MCP tools follow the existing conventions (classified, `llms.txt`, `docs/ai-parity.md`, destructive tools preview then confirm).
- Writes need the write scope; read-only tokens are refused.

## Order of work

1. Settle this doc (owner), then #119 and #120 (small, they only refine 2 and 7).
2. #121: schema, backfill from `folder`, GDPR additions, tests (including the round-trip).
3. #123 and #127 (APIs and tools), then #122, #125, #128 (the app), then #124 and #129 together with #194.
4. #130 analytics by bucket and tag.

## Decisions for the owner

1. **Buckets are the Dragon Shield folder, made first-class, with "Unsorted" as the default?** (Recommended.)
2. **Tags on the card (oracle id), kept when the card leaves the inventory?** (Recommended.)
3. **Deck and bucket related by name only in v1?** (Recommended.)
4. **Sharing stays whole-inventory in v1?** (Recommended.)
