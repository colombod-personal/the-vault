# Card roles: what each card does, and what can stand in for it (design for #166, epic #174)

Status: design for owner review. Nothing here is built yet.

## Why

The deck ideas lab (#161) and deck independence (#165) need to answer "this deck is missing a card: does anything I own do the same job?" That needs to know what a card does. Owner direction (2026-10-05): this is the Vault's own intelligence data source. Anyone can read it; only the Vault's pipelines write and review it; user experience feeds it as suggestions.

## What exists today

- `oracle_tags` holds all 4,560 Scryfall Tagger tags with their tree (`parent_ids`, `child_ids`). `oracle_tag_links` holds links only for a curated set (`TAG_ROOTS`, 19 roots plus descendants, about 55k links), each with Scryfall's weight (`very_strong` ... `weak`).
- `vault/deck_tools.py` maps eight coarse roles to tags (`ROLE_TAGS`: ramp, draw, removal, sweeper, counterspell, tutor, recursion, sacrifice_outlet) and rolls descendants up. `HIDDEN_TAGS` switches a tag off, as Scryfall asks.
- Gap: the examples the owner gave (treasure generation, token doubling, repeatable draw versus one-shot draw) are finer than these eight roles, and the Vault has no roles of its own, only Scryfall's opinion passed through.

## The limit to design around

Scryfall's terms forbid simply repackaging, republishing or proxying its data; the software must add value. So the shared table must not be a mirror of Scryfall's tags. It is the Vault's own layer: its vocabulary, its mappings, its review, and a second kind of evidence (rules text) that Scryfall's tags do not carry. Scryfall's tag is one labelled input, credited to Scryfall Tagger contributors. The terms check is recorded on #62 before any Scryfall-derived row is readable by signed-in accounts; until then the rows built only from the Vault's own rules can be readable and the Scryfall-derived ones stay internal.

## Design

### 1. A role vocabulary the Vault owns

A small table `roles` (about 40 to 60 entries to start), each with a slug, a name, a one-line description, a parent (a tree like `draw` > `repeatable-draw`), and a `kind`: an effect the card produces (`makes-treasure`, `doubles-tokens`, `draws-cards`, `counters-spells`, `ramps`) or a play pattern (`sacrifice-outlet`, `tutor`). Slugs are stable; descriptions are the Vault's own words.

Starting set, from the owner's examples and the roles the Vault already uses: ramp (mana rock, mana dork, land ramp), treasure generation, token creation, token doubling, card draw (one-shot, repeatable), counterspell, removal (spot, sweeper), tutor, recursion, sacrifice outlet.

### 2. A card-to-role table, readable by everyone

`card_roles(oracle_id, role_id, strength, source, evidence, reviewed_at)`.

- `strength`: `core` (the card exists to do this), `incidental` (does it on the side), matching how Scryfall weights are used today.
- `source`: `vault_rule` (a deterministic rule over oracle text), `scryfall_tag` (mapped from a Tagger tag), `suggestion` (a proposal that the reviewer pipeline accepted).
- `origin` and `author`, kept separately from `source` and from whether a row was reviewed: `origin` is `person`, `assistant` or `pipeline`, and for an assistant `author` names the app or model that wrote it and when. A row accepted from an assistant's suggestion keeps `origin = assistant` and its `author`, so it is always shown labelled as AI-written (per #126 and #174), even after it has been reviewed and accepted; review does not launder origin.
- `evidence`: for `vault_rule` the matched text; for `scryfall_tag` the tag `id` (never the slug); for `suggestion` the suggestion id.
- Read: like the card catalogue, it is readable by **every signed-in account**; there is no anonymous access (owner decision on #62, 2026-10-06: free accounts, no anonymous catalog, `PUBLIC_CATALOG` removed). Opening it to people without an account would be a separate product decision about a page that adds value, not part of this design. Write: only the pipeline's database role; no API route writes it. A test fails if any route does.

### 3. How rows are produced (pipelines, not people)

1. Rule pipeline: patterns over oracle text, for example "create a Treasure token" gives `makes-treasure`, "if one or more tokens would be created ... twice that many" gives `doubles-tokens`. Each rule has fixtures of cards it must and must not match.
2. Tag pipeline: a mapping table from Scryfall tag `id` to role (reviewed by hand once, then versioned). Tags marked hidden give no rows.
3. Reviewer pipeline: a scheduled job that compares sources and flags disagreements (a rule says treasure, no tag agrees) for review before they are published.
4. Rebuilds are idempotent and keyed by **every input to the roles**, not the catalogue version alone: the catalogue version, the rules version, the tag-mapping version, a hash of the hidden-tag set, and a watermark of reviewed suggestions. A corrected rule, a changed mapping, a newly hidden tag or an accepted suggestion therefore always triggers a rebuild even when Scryfall's data did not change, and a daily update with none of those changes touches nothing. Hiding a tag **withdraws** the rows it produced on the next rebuild (and the published answers stop showing them), so a hidden tag never lingers.

### 4. Experience from users: suggestions, reviewed

A person (or their assistant) can submit "card X does role Y, because Z" to a `role_suggestions` table that is writable only through a rate-limited endpoint and readable only by the pipeline and the submitter. The reviewer pipeline accepts or rejects it (accepted ones become `source = suggestion` rows that keep the suggester's `origin` and `author`, so an assistant-written row stays labelled as AI) and records why. Nobody edits `card_roles` directly. Design details and abuse limits are a separate task.

### 5. The questions it answers

- `roles_of(card)` and `roles_of(deck)`: what a deck does, which of the Vault's roles it has and lacks (replaces the eight fixed roles).
- `equivalents(card, deck_id | colour_identity + format)`: other cards sharing a `core` role. Colour identity and format legality are **filters**, applied before ranking: a card outside the deck's colour identity, or banned or not legal in its format, is never offered. The target deck (or an explicit colour identity and format) is a required input for that reason. The remaining candidates are ranked by overlap of roles and mana-value difference, with the ones you own first. Feeds "what can stand in for this missing card" in #161 and the swap suggestions in #165.
- Every answer carries provenance: the source of each role, the Scryfall credit where a tag was used, and "a Vault role, not a rule".

## Decisions for the owner

1. **Readable by signed-in accounts from day one, or after the terms check?** Recommendation: before the terms check on #62, only the Vault's own vocabulary and the **rule-derived** associations are visible to anyone. Tag-derived associations stay hidden until that check passes; showing them under the Vault's own role names is not an alternative to the check, because it would still expose tag-derived results.
2. **Vocabulary size to start.** Recommendation: about 40 roles from the owner's examples and the existing eight, grown by evidence from the suggestion queue.
3. **Strength.** Recommendation: two levels (`core`, `incidental`), mapped from Scryfall's weights where a tag is the source.

## Tasks that follow (epic #174)

1. Vocabulary and mapping table (data, reviewed): the first roles and the tag-to-role mapping.
2. Schema and migration: `roles`, `card_roles`, `role_suggestions`, with the write restriction and its test.
3. Rule pipeline with fixtures per rule.
4. Tag mapping pipeline with the hidden-tag switch.
5. Reviewer pipeline and the published-version stamp.
6. API and MCP tools: `roles_of`, `equivalents`.
7. Suggestion intake and review (own design: abuse limits, who can submit).
8. Switch `deck_tools` from `ROLE_TAGS` to the new roles, keeping today's answers working.
