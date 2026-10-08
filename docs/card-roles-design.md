# Card roles: what each card does, and what can stand in for it (design for #166, epic #174)

Status: agreed by the owner on 2026-10-08 (#287: decisions 23 to 25, all as recommended). The larger design below (a role vocabulary, a `card_roles` table, review pipelines) is not built. One part of it is: **Oracle-text rules as a fallback for the eight roles** (#18), described in the next section.

## Why

The deck ideas lab (#161) and deck independence (#165) need to answer "this deck is missing a card: does anything I own do the same job?" That needs to know what a card does. Owner direction (2026-10-05): this is the Vault's own intelligence data source. Anyone can read it; only the Vault's pipelines write and review it; user experience feeds it as suggestions.

## What exists today

- `oracle_tags` holds all 4,560 Scryfall Tagger tags with their tree (`parent_ids`, `child_ids`). `oracle_tag_links` holds links only for a curated set (`TAG_ROOTS`, 19 roots plus descendants, about 55k links), each with Scryfall's weight (`very_strong` ... `weak`).
- `vault/deck_tools.py` maps eight coarse roles to tags (`ROLE_TAGS`: ramp, draw, removal, sweeper, counterspell, tutor, recursion, sacrifice_outlet) and rolls descendants up. `HIDDEN_TAGS` switches a tag off, as Scryfall asks.
- Gap: the examples the owner gave (treasure generation, token doubling, repeatable draw versus one-shot draw) are finer than these eight roles, and the Vault has no roles of its own, only Scryfall's opinion passed through.

## Built: roles from Oracle text, as a fallback to Scryfall's tags (#18)

`docs/catalog-design.md` ("Tags") once said Scryfall's Tagger tags replace deriving roles from Oracle text. That was the author's
proposal, never an owner decision, and #18 asks for tags **derived from Oracle text with documented rules**. Both are now
true: the tags stay the primary source, and `vault/role_rules.py` adds a small set of written rules that fill the gaps.

How it behaves (`vault/deck_tools.py`, `roles_of` and `role_entries`):

- **Tagger wins.** For a card and a role, if Scryfall's Tagger has a tag for that role (through the tag tree), that is the answer,
  shown with its weight. A rule is consulted only for a role where the card has **no Tagger tag**.
- **Always labelled.** A role from a rule is shown as `basis: "computed"` with the rule's id (`rule: "ramp-fetch-land"`) and
  no weight; a Tagger role is `basis: "scryfall_tagger"`. The answer's provenance is the Vault's `computed` block listing the
  Oracle text and the tags it read; the text rules are never presented as Scryfall's and never as a fact about the card.
  `deck_stats` counts both and says how many of each (`from_tagger`, `computed`), so the old counts only grow where a rule
  found something Tagger did not.
- **Where it applies.** `deck_stats` roles and `get_card_oracle`'s `computed_roles`. Upgrade candidates still come from Tagger
  tags (a database query that rules over text cannot replace); the deck's role counts used for "what is the deck short of"
  include the computed roles.
- **Text is read per face** (`vault/card_faces.py`), reminder text in parentheses is removed, the card's own name is read as
  `~`. The rules are narrow on purpose: a miss shows no role, a false hit shows the rule that made it.
- **Not measured against Tagger at scale.** The tests (`tests/test_role_rules.py`) are hand-picked cards, one or more per rule,
  including cards each rule must not match. How often the rules agree with Tagger over the whole catalog was not measured
  (that needs the full Tagger file, whose use is gated by `docs/compliance.md`); compare the rules with the tags over a loaded catalog before
  widening them.

The rules (the same ids and wording as the code; `tests/test_role_rules.py` fails if one is missing here):

| Rule | Role | Matches | Known to get wrong |
|---|---|---|---|
| `ramp-add-mana` | ramp | a non-land artifact, creature or enchantment with an activated 'add' mana ability ({T}: Add ...) | a rock or creature that adds mana only under a condition is still matched; a mana ability that needs a cost per use is too |
| `ramp-fetch-land` | ramp | a non-land card with 'search your library for ... land ... put it onto the battlefield' (Cultivate-style) | does not check that the land comes in untapped or that the spell is cheap; a land that fetches a land (Evolving Wilds) is deliberately not ramp |
| `ramp-extra-land` | ramp | 'you may play an additional land' (or two, or any number) | a card that gives the extra land drop only to opponents is matched too |
| `ramp-treasure` | ramp | creates one or more Treasure tokens | treats every Treasure maker as ramp, including a one-shot reward on a spell that is mostly something else |
| `draw-cards` | draw | 'you draw', 'draw a card/two/X cards', 'target player draws', 'each player draws' | counts a card that draws only as a drawback or a symmetrical wheel the same as a plain draw spell |
| `removal-destroy-exile` | removal | 'destroy' or 'exile' (up to N) target creature, artifact, enchantment, planeswalker, battle or permanent | an optional 'may' or a conditional clause is not read; 'exile target card from a graveyard' is correctly not matched |
| `removal-damage` | removal | 'deals N damage to any target / target creature / planeswalker / battle' (burn and fight spells) | damage aimed only at players is not matched; damage shared out 'divided as you choose' is |
| `removal-shrink-fight` | removal | 'target creature gets -N/-N', '-X/-X until end of turn', or 'fights target creature' | a -N/-N that only shrinks a creature without killing it is matched; so is a one-sided fight of your own creature |
| `removal-pacify` | removal | an Aura that says the enchanted creature 'can't attack or block' | the creature stays on the battlefield and can still use abilities |
| `sweeper-destroy-all` | sweeper | 'destroy' or 'exile' all creatures, permanents, artifacts, enchantments or planeswalkers (not 'you control') | a sweeper that spares a type or has an exception in a later sentence is still matched |
| `sweeper-damage-each` | sweeper | 'deals N damage to each creature' (without 'you control') | damage that only hits some creatures ('each creature with flying') is matched |
| `sweeper-shrink-all` | sweeper | 'all creatures get -N/-N' or '-X/-X' | a symmetrical shrink that does not kill a big creature still counts |
| `sweeper-each-sacrifices` | sweeper | 'each player sacrifices all/every creature' | none known |
| `counter-spell` | counterspell | 'counter target spell' or 'counter target ... ability', 'counter that spell' | a counter with a heavy condition (only creature spells, only noncreature) is matched like an unconditional one |
| `tutor-library` | tutor | 'search your library for' something that is not only a land | a card that fetches a named, narrow card is matched like Demonic Tutor |
| `recursion-to-hand` | recursion | 'return ... card(s) from your/a/target player's graveyard to ... hand or the battlefield' | a card that returns only itself ('return ~ from your graveyard') is matched too |
| `recursion-reanimate` | recursion | 'put target ... card from a graveyard onto the battlefield' | a reanimation of only the card itself is matched too |
| `sacrifice-outlet` | sacrifice_outlet | a cost of 'Sacrifice a/another/an/two creature(s), permanent ...:' before a colon (a repeatable outlet) | an outlet limited to one type of permanent (a Treasure, a land) is matched like a creature outlet |
| `extra-turn` | extra_turn | 'take an extra turn', 'takes an extra turn after this one' | a card that only stops extra turns is not matched, but a rare typo could be |
| `mass-land-denial` | mass_land_denial | Wizards' own description (Commander Brackets, Feb 2025): cards that destroy, exile or bounce several lands, keep lands tapped, or change what mana several lands make (Armageddon, Ruination, Sunder, Winter Orb, Blood Moon). Matched: destroy/exile/sacrifice/return all lands, lands that don't untap, 'can't untap more than one land', 'nonbasic lands are <type>s' | Wizards published examples and a description, not a list: this finds the clear wordings and misses odd ones; it also matches symmetric effects and effects limited to nonbasic lands that Wizards might not count |

The last two rows are not roles. They are the two effects the Commander Brackets name that no Tagger role covers, found the same
way and used by the bracket hint (`vault/brackets.py`, `docs/catalog-design.md`).

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
