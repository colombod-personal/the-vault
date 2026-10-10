# Functional equivalents: owned cards that do the same job as a missing card (research and design for #166, epic #174)

Status: **research and design agreed by the owner on 2026-10-09 ("take the recommendations", every open decision as recommended); built in the pull request for #166: see section 12.** Sections 1 to 11 are the research and design as written before the build (where the build differs, section 12 says so). Written 2026-10-09. The research itself **ingested no data**: it read public pages and ran a
few dozen read-only Scryfall searches (about 80 requests with an accurate User-Agent; one early burst got a 429 and was backed off for 70 s, which is why
the numbers below are search totals and the real measurement belongs in a job that reads the bulk file). Nothing was read from the owner's collection.

This note answers the four criteria of #166: the sources and their terms (sections 2 and 3), the role vocabulary (section 5), matching and honesty rules
(sections 6 and 7), the mockups (section 8), and the promise that nothing is ingested before its terms are recorded (section 9). The data layer itself is
already designed and agreed in `docs/card-roles-design.md` (2026-10-08); this note adds what that design left to #166 and does not repeat it.

How to read the claims. Every statement carries one of these marks, so a reader can tell what was seen from what is advice:

- **Read** (date): the page text was read on that date; the URL is given.
- **Measured** (2026-10-09): a request or a script was run today and the output is quoted.
- **Repo**: taken from this repository at `origin/main` 62d4c63 (a doc or the code), not re-checked outside it.
- **Recommendation**: my advice. Not a fact, not a decision.
- **Not verified**: said so, because it could not be read or tried.

## 1. The question, and what already exists

Owner request (2026-10-05, #158): when a deck is missing a card, can anything already owned stand in, or deliver "the same dynamic" (treasure generation,
token doubling, card drawing, a counterspell).

What the repository already has (**Repo**):

- `docs/card-roles-design.md`: a Vault-owned role vocabulary, a read-only `card_roles` table written only by pipelines, and `equivalents(card, deck)`.
  Owner decisions 23 to 25 (all as recommended, 2026-10-08).
- `vault/role_rules.py`: 18 written rules over Oracle text for eight coarse roles (ramp, draw, removal, sweeper, counterspell, tutor, recursion,
  sacrifice outlet) plus two bracket signals (`extra-turn`, `mass-land-denial`). `vault/deck_tools.py` maps Scryfall Tagger tags onto the same eight roles (`ROLE_TAGS`); the Tagger wins
  where it has a tag, a rule fills the gap and is labelled `computed`.
- `vault/deck_ideas.py`, `equivalence()`: today two cards are "equivalent" when they share a core coarse role. #166's `equivalents` replaces it.
- `docs/deck-ideas-lab-design.md`: the Ideas view that will show the candidates (built as `public/views/ideas.jsx`).

The gap #166 closes: the owner's examples are finer than eight roles. **Measured** with today's rules (the 18 role rules run on the Oracle text of seven cards
named in the issue, text fetched from Scryfall on 2026-10-09):

| Card | Roles the existing 18 rules give | Comment |
|---|---|---|
| Doubling Season | none | token doubling and counter doubling are not roles yet |
| Anointed Procession | none | same |
| Rhystic Study | `draw` (`draw-cards`) | does not say "repeatable, triggered by opponents" |
| Smothering Tithe | `ramp` (`ramp-treasure`) | right, but a tax enchantment that sometimes makes Treasure is read as plain ramp |
| Fierce Guardianship | `counterspell` (`counter-spell`) | does not say "free" or "noncreature only" |
| Cyclonic Rift | none | bounce is not a role: no rule covers "return target ... to its owner's hand" |
| Cloudstone Curio | none | correct: it is not token doubling (see the note below) |

Correction found on the way: the mockup in `docs/deck-ideas-lab-design.md` (state 3) uses Cloudstone Curio as a card with the role "token doubling". Its Oracle
text returns a permanent to hand when a nonartifact permanent enters (**Measured**, Scryfall, 2026-10-09). It is an illustration, not data, but it
should not stay in the design as a token doubler; Anointed Procession, Parallel Lives and Doubling Season are. This note's mockups use those.

## 2. The candidate sources, one by one

### 2.1 Scryfall oracle tags (the Tagger project)

What it is. Community-written functional tags ("removal", "ramp", "draw") on cards, kept by Scryfall's Tagger project, imported by Scryfall daily, searchable as
`otag:` and shipped as a bulk file. The Vault already loads them daily (`oracle_tags`, 4,560 tags; links only for 19 roots, about 55k links, **Repo**).

Terms (**Read 2026-10-09**):

- [scryfall.com/docs/api/tags](https://scryfall.com/docs/api/tags): tags come from "the Tagger project, a community-maintained database"; data "is subject
  to change"; Scryfall moderates but "cannot guarantee" it is free of intentional errors or abuse. Do not treat slug or label as permanent: track the `id`
  UUID. Applications are "strongly recomended" (sic) to be able to switch individual tags off temporarily. Tags come as bulk files "updated daily". Each tagging
  carries a `weight` (`very_strong`, `strong`, `median`, `weak`) and an optional `annotation`. The bulk file holds only direct taggings: a parent tag has none
  of its own, you walk `child_ids`.
- [scryfall.com/docs/api](https://scryfall.com/docs/api) (the guidelines the data comes under): free "for the primary purpose of creating additional Magic
  software"; no Scryfall logos or implied endorsement; no paywall (free accounts are fine); and "You may not simply repackage, republish, or proxy Scryfall data.
  Your software must create additional value for end-users." Misuse "may result in" restricted API access.
- [scryfall.com/docs/api/bulk-data](https://scryfall.com/docs/api/bulk-data): the Oracle Tags file was 5.74 MB compressed, last updated 2026-10-09 09:00 UTC
  (**Measured**, the page's own table).
- [scryfall.com/docs/terms](https://scryfall.com/docs/terms) (Scryfall's site terms): "Artificially generated content is not allowed" on Scryfall, and what a
  contributor posts is licensed to Scryfall. Consequence (**Recommendation**): the Vault never sends AI-proposed roles back to Tagger, and never describes
  its own roles as Tagger's.
- [scryfall.com/docs/tagger-tags](https://scryfall.com/docs/tagger-tags): confirms "oracle" tags describe a card's function and are searchable by
  `function:`, `otag:`, `oracletag:`.

What the terms do not settle (**Not verified**, honest gap): the pages above do not say, in so many words, whether a derived table built from tags counts
as "additional value" or as "repackaging". That is the question the owner has not yet sent to Scryfall (`docs/compliance.md`, "Open with the owner", draft in
`docs/outreach-drafts.md`, **not sent**). The agreed position stands: tag-derived rows stay internal until the terms check is recorded on #62.

Coverage (**Measured** 2026-10-09, Scryfall search totals, `unique=cards`; the Vault's own counts from the bulk file will differ a little):

| What was counted | Result |
|---|---|
| Commander-legal cards (`f:commander`) | 32,116 |
| of the 30,904 that are not lands, how many carry at least one of the tags behind the Vault's eight roles | 15,182 (49 %). The other half is mostly vanilla bodies and cards with no role the eight cover, not necessarily misses |
| `otag:token-doubler` | 17 cards (16 Commander-legal) |
| `otag:repeatable-treasures` | 203 (190 Commander-legal) |
| `otag:counterspell` | 550 (522 Commander-legal); `counterspell-free` 13 |
| `otag:removal-bounce` | 438 (421 Commander-legal) |
| `otag:repeatable-pure-draw` / `draw-engine` | 1,378 / 1,569 |
| `otag:mana-doubler` | 53 Commander-legal |

The issue's example cards against the tags (**Measured**; Y = the tag is on the card):

| Card | Tags found |
|---|---|
| Doubling Season | `token-doubler` Y, `counter-doubler` Y |
| Anointed Procession, Parallel Lives, Mondrak, Glory Dominus | `token-doubler` Y (none of the three is a counter doubler) |
| Rhystic Study | `repeatable-pure-draw`, `draw-engine`, `card-advantage` Y (also Mystic Remora, Phyrexian Arena) |
| Smothering Tithe | `repeatable-treasures`, `repeatable-token-generator`, `ramp` Y (no `token-doubler`, no `counterspell`) |
| Fierce Guardianship | `counterspell`, `counterspell-free` Y |
| Cyclonic Rift | `removal`, `removal-bounce`, `sweeper` Y (not `counterspell`) |

Gaps that matter for equivalents (**Measured**): there is no tag for one-shot Treasure creation (`otag:treasure` matches no card; `repeatable-treasures` is
the repeatable one); the one-shot draw tags (`pure-draw`, `card-advantage`) and the repeatable ones (`repeatable-pure-draw`) are different tags, but the
Vault's `draw` role merges them; `Hardened Scales` is not `counter-doubler` (it adds counters, it does not double them); `Divine Visitation` and `Avenger of
Zendikar` are not in `repeatable-token-generator`. A community tag set is deep where people care and uneven elsewhere.

Verdict (**Recommendation**): useful second input, never the only one. It is the best source for rare roles that text rules miss, and it carries a weight.
Map by tag `id`, keep a hide switch (already `HIDDEN_TAGS`), credit Tagger contributors, and keep it behind the #62 record.

### 2.2 The Vault's own classification from Oracle text and keywords

What it is. Deterministic patterns over the Oracle text and type line the Vault already holds, written down with the rule id, the cards each must and must not
match, and what it is known to get wrong (`vault/role_rules.py`, `docs/card-roles-design.md`).

Terms (**Read 2026-10-09** for the inputs): the Oracle text is Wizards' and reaches the Vault through Scryfall under the guidelines in 2.1; the Fan Content
Policy ([company.wizards.com/en/legal/fancontentpolicy](https://company.wizards.com/en/legal/fancontentpolicy), "Last Updated: November 15, 2017") asks that
Fan Content be free to access and carry the unofficial notice (the notice is shown; the free-account question is the registration risk already recorded in `docs/compliance.md`). A role the Vault computes is the Vault's own work: no third party's terms
restrict it, only the rule "never make anything look like ours" in reverse (a computed role is labelled computed, with its rule id). This is why the rows built
only from rules can be readable before the #62 record (agreed in `docs/card-roles-design.md`, decision 1).

Coverage to expect (**Recommendation**, with the **Measured** result above as the baseline): high precision on roles whose wording is formulaic, lower recall
elsewhere, and zero on roles that live in context. Rough sorting of the proposed vocabulary (section 5), by how formulaic the text is:

- Formulaic, good for rules: token doubling ("if an effect would create one or more tokens ... twice that many"), Treasure creation, counter spells, bounce
  ("return target ... to its owner's hand"), spot removal, sweepers, tutors, sacrifice outlets, mana rocks and creatures that tap for mana.
- Mixed, rule plus a check: repeatable versus one-shot (needs "whenever", "at the beginning of", or an activated ability versus a spell), "free" counterspells
  (alternative cost wording), card draw that is only a drawback.
- Not rule-able, tags or review only: "protects my commander", politics and group hug, "good in my theme".

Not measured: how often the rules agree with Tagger over the whole catalog. `docs/card-roles-design.md` already says so; the comparison is the first thing to do
once the bulk file is loaded, before any rule is widened. Coverage on the owner's four decks and collection was **not measured** (no access, no data touched):
the measurement is a report of, per owned card, which roles it has and from which source, and a list of the owned cards with no role in the four decks.

Verdict (**Recommendation**): the primary source for #166's first release, because it is the only one that is entirely the Vault's and can be shown to
everyone from day one.

### 2.3 Commander Spellbook features

What it is. Commander Spellbook is a combo database. Its API (`backend.commanderspellbook.com`) has endpoints for `variants`, `features`, `cards`, `templates`,
`properties`, `find-my-combos`, `estimate-bracket` and more (**Measured**, the API root, 2026-10-09). A "feature" is an effect a combo produces or a card
provides ("Doubler - Tokens", "Infinite colorless mana").

What a card carries (**Measured**, the `cards` endpoint): per card a `features` array, a `variantCount`, and booleans `tutor`, `massLandDenial`,
`extraTurn`, `gameChanger`. Doubling Season has two features (`Doubler - Tokens`, `Increaser - Counters`) and 1,162 variants; Smothering Tithe has none and
129 variants; `+2 Mace` has none.

How many features, and of what kind (**Measured**, paged `features`): 1,335 public features. By status: 580 Helper, 324 Standalone, 279 Contextual, 152 Public
utility (hidden-utility features, such as `Increaser - Counters` on Doubling Season, appear not to be in the public list: none of the 1,335 has that status). The meaning of the codes is from the
backend source (`backend/spellbook/models/feature.py`, **Read** 2026-10-09: HU hidden utility, PU public utility, H helper, C contextual, S standalone). Most
features are combo outcomes ("Infinite card draw", "Counter all spells"), not roles a card plays in an ordinary deck. The few that read like roles ("Doubler -
Tokens", "Card that Tutors", "Artifact that Taps to Add Multiple Mana (Mana Rock)") are 152 public utilities at most.

Terms: the site's About page ([commanderspellbook.com/about](https://commanderspellbook.com/about/), **Read** 2026-10-09) says the website and backend source code are open
source under the MIT licence, and that the project powers EDHREC's combo feature; the backend repository's licence is MIT (GitHub API, **Measured**). **No
licence for the data and no API terms were found**: the site's About and Privacy pages say nothing about data reuse; `/terms/` answered 404 and
no page of terms or API licence is linked from the home page (its links: About, Privacy policy, Syntax guide, Submit a combo, a Patreon and a Discord). EDHREC's terms (2.4) name Space Cow Media as owner; the backend repository belongs to the GitHub organisation
`SpaceCowMedia` (**Measured**), so the two services share an operator, which makes EDHREC's restrictions a hint of how the operator thinks, not proof of
Commander Spellbook's terms. `docs/compliance.md` keeps its row as "not read: no data licence found; on demand per request, nothing stored", and that stays true.

Verdict (**Recommendation**): not a source for roles. Its card-level features are sparse and combo-shaped, and copying them is the ingestion the compliance row
forbids without an answer from the maintainers. Keep it where it is (on-demand combos for a deck). If the owner wants more later, the question to ask the
maintainers (Discord, per `docs/data-sources.md`) is whether a nightly copy of `features` per card is welcome.

### 2.4 Deck-list popularity (what is played alongside what)

The idea: infer "same job" from cards that fill the same slot in many decks. Every site that has such lists says no or has not said yes:

| Source | What its terms say | Read | Verdict |
|---|---|---|---|
| EDHREC | [edhrec.com/terms](https://edhrec.com/terms), effective August 06, 2024, operated by Space Cow Media: a limited licence "solely for your own personal, noncommercial use"; no copying, republishing or building "a similar or competitive website"; no "automated searches, requests, or queries" | **2026-10-09** | Not used. Scryfall's card field `edhrec_rank` (a single global popularity rank, documented at [scryfall.com/docs/api/cards](https://scryfall.com/docs/api/cards), **Read** 2026-10-09) is the only EDHREC-derived number the Vault may hold, and it says nothing about roles |
| Moxfield | terms effective July 9, 2026: no robot, spider or automatic device, and no manual copying without written consent | 2026-10-07 (`docs/compliance.md`; not re-read today) | Not used for fetching |
| Archidekt | personal, non-commercial licence that excludes software that "generates automated searches, requests, or queries"; the owner's position covers one public deck per person's request | 2026-10-05 and 2026-10-07 (`docs/compliance.md`; not re-read today) | One deck on a request; no co-occurrence statistics, which need many decks |
| MTGGoldfish | "intended solely for personal, non-commercial use" | 2026-10-05 (`docs/data-sources.md`; not re-read today) | Links only |
| 17Lands | Creative Commons Attribution 4.0 | 2026-10-05 (`docs/data-sources.md`; not re-read today) | Legal to use but Limited only (draft picks), so not about Commander decks |

Verdict (**Recommendation**): no external popularity source. One internal signal is legitimate and cheap: the person's **own saved decks**. "You already run this
card in another deck" is the borrowed state the lab already shows (#165), and co-occurrence inside the person's own decks can break ties between equal
candidates without any outside data. Do not build it in v1; note it as a later tie-breaker.

### 2.5 An AI classifier

From #166: "an AI classifier only as a proposal that the person confirms". Agreed handling (`docs/card-roles-design.md`, section 4, and #126): an assistant's
proposal goes to the suggestion queue, a pipeline accepts or rejects it, an accepted row keeps `origin = assistant` and its author and is always shown as
AI-written. Terms of the model providers are out of scope here. The one constraint from this research is Scryfall's: never push such roles to Tagger (2.1).
It is not a source for the first release.

## 3. Summary of the terms position (each source, one line)

| Source | May the Vault load it for roles? | Recorded where |
|---|---|---|
| Scryfall oracle tags | Loaded already for the eight coarse roles. Showing tag-derived rows to accounts waits for the #62 record. Track by id, hide switch, credit Tagger | `docs/compliance.md` gate row `oracle_tags` (read 2026-10-05, re-read here 2026-10-09) |
| The Vault's own rules over Oracle text | Yes, labelled `computed` with the rule id | `docs/card-roles-design.md` |
| Commander Spellbook features | No ingestion: no data licence found; on-demand combos only | `docs/compliance.md` row `commander_spellbook` (unchanged) |
| EDHREC, Moxfield, Archidekt, MTGGoldfish popularity | No | section 2.4 |
| 17Lands | Allowed with attribution, but Limited only | `docs/data-sources.md` |
| AI proposals | Only through the review queue, labelled AI | `docs/card-roles-design.md` section 4 |

## 4. Recommended approach

1. **Vault-owned vocabulary first** (section 5). Slugs stable; descriptions in the Vault's own words.
2. **Release 1 runs on Vault rules only**, extended from the eight coarse roles to the vocabulary below, each rule with fixtures it must and must not match,
   labelled `computed` with the rule id. It has no dependency on the Scryfall question.
3. **Release 2 adds the Tagger as a second labelled input** once #62 records the terms check: a reviewed map from tag `id` to role, hide switch honoured, and a
   disagreement report (a rule says Treasure, no tag agrees) that feeds the review queue. Where both agree, the answer says both.
4. **Commander Spellbook and external popularity stay out.** The person's own decks can later break ties.
5. **Measure before widening.** First job once data exists: the agreement table between rules and tags over the whole catalog, and the per-deck report for the
   owner's four decks (roles per card, source per role, cards with no role). Publish the numbers in this doc.
6. **No role is written by a person or an assistant directly**; the queue in `docs/card-roles-design.md` is the only door.

## 5. Proposed role vocabulary (about 22 roles; small, plain, player-recognisable)

Principles. A player should recognise each name without a glossary. A role is an effect the card produces, not a theme. Two modifiers instead of more roles:
**repeatable** (does it again and again, versus once) and **scope** (one target, or all). Strength is `core` (the card exists to do this) or `incidental`, as
agreed. Examples are cards named in the issue or checked against Scryfall's tags on 2026-10-09 (marked with a tick when the tag was confirmed; **not** claims
that the owner owns them).

| Slug | Plain name | What it means | Example (checked) | Rules or tags that could find it |
|---|---|---|---|---|
| `mana-rock` | Mana rock | A non-creature permanent that taps for mana | Sol Ring ✓ | rule `ramp-add-mana`; tag `mana-rock` |
| `mana-creature` | Mana creature | A creature that taps for mana | Llanowar Elves ✓, Birds of Paradise ✓ | rule `ramp-add-mana`; tag `mana-dork` |
| `land-ramp` | Land ramp | Puts lands onto the battlefield or allows extra land drops | Cultivate ✓, Rampant Growth ✓ | rules `ramp-fetch-land`, `ramp-extra-land`; tag `land-ramp` |
| `treasure` | Treasure | Makes Treasure tokens (repeatable or once) | Smothering Tithe ✓ (repeatable) | rule `ramp-treasure`; tag `repeatable-treasures` for the repeatable ones; no tag for one-shot |
| `mana-multiplier` | Mana doubler | Makes lands or other sources produce more mana | Mana Reflection ✓, Nyxbloom Ancient ✓ | tag `mana-doubler`; a rule on "twice that much" wording |
| `draw-once` | Card draw (once) | A spell or effect that draws a fixed amount | Night's Whisper ✓, Divination ✓ | rule `draw-cards`; tags `pure-draw`, `card-advantage` |
| `draw-engine` | Card draw (every turn or every trigger) | Draws repeatedly while it stays | Rhystic Study ✓, Phyrexian Arena ✓, Mystic Remora ✓ | tag `repeatable-pure-draw`; a rule that needs "whenever" or "at the beginning of" |
| `tutor` | Tutor | Searches the library for a card that is not just a land | Demonic Tutor ✓, Vampiric Tutor ✓ | rule `tutor-library`; tag `tutor` |
| `recursion` | Get cards back | Returns cards from the graveyard to hand | Eternal Witness ✓, Regrowth ✓ | rule `recursion-to-hand`; tag `recursion` |
| `reanimate` | Reanimate | Returns creatures from a graveyard to the battlefield | Animate Dead ✓, Reanimate ✓ | rule `recursion-reanimate`; tag `reanimate` |
| `token-maker` | Token maker | Creates creature or other tokens, once or repeatedly | Rhys the Redeemed ✓ (repeatable) | tag `repeatable-token-generator`; a rule on "create ... token" |
| `token-doubler` | Token doubler | Doubles the tokens you create | Doubling Season ✓, Anointed Procession ✓, Parallel Lives ✓, Mondrak, Glory Dominus ✓ | tag `token-doubler`; a rule on "twice that many of those tokens" |
| `counter-doubler` | Counter doubler | Doubles counters put on your permanents | Doubling Season ✓, Vorinclex, Monstrous Raider ✓ | tag `counter-doubler`; a rule on "twice that many of those counters" |
| `counterspell` | Counterspell | Counters a spell or ability | Counterspell ✓, Mana Drain ✓, Fierce Guardianship ✓ | rule `counter-spell`; tag `counterspell` |
| `free-counterspell` | Free counterspell | A counterspell that can be cast without paying its mana cost | Fierce Guardianship ✓, Force of Will ✓ | tag `counterspell-free`; a rule on "without paying its mana cost" plus counter |
| `spot-removal` | Spot removal | Removes or neutralises one target permanent | Swords to Plowshares ✓, Path to Exile ✓ | rules `removal-*`; tag `spot-removal` |
| `bounce` | Bounce | Returns permanents to their owners' hands (one or all) | Cyclonic Rift ✓ | tag `removal-bounce`; a new rule on "return target ... to its owner's hand" |
| `sweeper` | Board wipe | Removes many permanents at once | Wrath of God ✓, Damnation ✓, Cyclonic Rift ✓ (overloaded) | rules `sweeper-*`; tag `sweeper` |
| `sacrifice-outlet` | Sacrifice outlet | Lets you sacrifice permanents repeatedly | Viscera Seer ✓, Ashnod's Altar ✓ | rule `sacrifice-outlet`; tag `sacrifice-outlet` |
| `protection` | Protect my stuff | Keeps your permanents from removal or counters | Heroic Intervention ✓, Teferi's Protection ✓ | tag `protection`; review only |
| `lifegain` | Lifegain | Gains life | (from the Vault's tag roots; not checked here) | tag `lifegain` |
| `evasion` | Evasion | Makes creatures hard to block | (from the tag roots; not checked here) | tag `evasion` |

Notes. Interaction in the owner's words (Cyclonic Rift, Fierce Guardianship) is three roles here (`bounce`, `sweeper`, `free-counterspell`/`counterspell`) because a
bounce spell and a counterspell do different jobs; a group name "interaction" can be a display heading above them without being a role. Multi-role cards are the
normal case: Doubling Season is `token-doubler` and `counter-doubler`; Cyclonic Rift is `bounce` and (overloaded) `sweeper`. The repeatable modifier is carried by
`draw-once` versus `draw-engine`, and by a `repeatable` flag on `treasure` and `token-maker`, so the Vault has no 40-slug tree. Growth is by evidence from the
suggestion queue, never by adding a role because a tag exists.

## 6. Matching rules

The inputs are a missing card, the target deck (or an explicit colour identity and format), and the person's collection. In order:

1. **Filter first** (nothing filtered out is ever shown): the candidate is owned; it shares a `core` role with the missing card; its colour identity is inside the
   deck's (the commander's in a Commander-style format with a commander, otherwise the colours of the deck's cards, as `find_upgrades` already does, so the answer
   says which rule it used); it is legal or restricted in the deck's format; the deck would not exceed the format's copy limit with it; it is not the missing card.
   Basic lands out. This is the behaviour of `docs/deck-ideas-lab-design.md` ("As built") carried over unchanged.
2. **Two tiers, not one.** *Same job*: the shared role also matches on the modifier (a repeatable draw for a repeatable draw; a free counter for a free
   counter; a mass effect for a mass effect). *Similar, with a difference*: the role matches but a modifier does not (a one-shot draw spell offered for Rhystic
   Study). The second tier is shown, labelled, below the first and collapsed on a phone; it is never merged into the first. (**Recommendation**: otherwise "nothing
   found" would be the answer far too often, and an unlabelled partial match would oversell.)
3. **Rank inside a tier:** a free copy first, then a copy another deck holds (borrowed), as built; then more shared core roles; then smaller mana-value difference;
   then name. Mana value is **ranked and shown, not a filter**: "costs 1 more" is information, and a hard cut at some range would hide a good answer.
   (**Recommendation**; a cut at a difference of 4 or more is the alternative if the list gets long.)
4. **Say what is missing.** For each candidate, list the missing card's roles the candidate does **not** have ("Anointed Procession doubles tokens; it does not
   double counters, which Doubling Season also does"). This is a computed difference of two role sets, so it costs nothing and prevents the commonest oversell.
5. **The reason is built from the role entries, not written freehand:** "same job: token doubler (core, Scryfall Tagger, strong); costs 1 less; you own 2". A test
   checks that the sentence can only name roles the data holds.

## 7. Honesty rules

1. A candidate is a **suggestion with a reason**, never "this is the same card" or "a replacement". The wording is "does a similar job".
2. **Both Oracle texts are shown** side by side, as Scryfall's data with the Fan Content notice, artist and Scryfall credit under any image; images are never
   cropped.
3. **Every role names its basis**: a Vault rule (with its id), Scryfall's Tagger (with its weight, credited to the Tagger community), or an accepted
   assistant suggestion (labelled AI-written, always). Tag-derived roles are not shown to accounts until the #62 record exists.
4. **Three different empty answers, three different sentences**: "the Vault knows no role for this card" (we do not know what it does), "you own nothing else that
   does this job in this deck's colours and format" (we know the job, you have no candidate), and "your candidates are all in other decks" (see state 3 below).
   A missing role is never reported as a missing equivalent.
5. **No power judgement.** The Vault does not say a candidate is as good; it says what roles match, what differs and what each costs.
6. **Dated and sourced:** the roles' version stamp (catalogue, rules, mapping) is shown, prices are Scryfall's with their date (as the lab already does).
7. **AI never launders itself:** an accepted suggestion keeps `origin = assistant`; the card shows it as AI-written even after review.
8. **No hidden filtering:** when the colour-identity or format filter removed candidates, the panel says how many and why ("3 owned cards do this job but are
   outside Sultai"), without naming them as options.

## 8. Mockups (text; illustrative data, not the owner's collection)

`[...]` is a control. The deck and the owned copies are invented for the picture; the roles shown are the ones **Measured** on Scryfall's tags in section 2.1. Images
and thumbnails follow the lab rule: about 40 px, only for the selected card and its candidates, never full art. No screenshots were made (no code exists); the 1400
and 390 px layouts below are the contract the build is checked against, with the same tests as the Ideas view (no horizontal scroll at 390, targets 44 px).

### State A: candidates found (the missing card is Doubling Season, deck Sliver Swarm, Commander)

```
1400 px
Doubling Season  {4}{G}  missing from Sliver Swarm                                    [Back]
Does: token doubler (core) | counter doubler (core)     basis: Scryfall Tagger, strong   [why?]
Checked for: Commander, colours WUBRG (from the deck's cards, no commander set)   roles as of 2026-10-09

Same job (1)
+--------------------------------------------------------------------------------------+
| (img) Anointed Procession   {3}{W}   x2 owned, free                                  |
|  Does: token doubler (core).  Costs 1 less than Doubling Season.                     |
|  Does not: counter doubler (Doubling Season does it too).                            |
|  Oracle text (both)  [Show]            [Swap into the deck]   [Open on Scryfall]     |
+--------------------------------------------------------------------------------------+
Similar, with a difference (1)  [Show]
  Mondrak, Glory Dominus: token doubler, but a creature (costs the same), x1 owned
Roles from Vault rules and Scryfall's Tagger. Unofficial Fan Content, not endorsed by Wizards or Scryfall.

390 px
Doubling Season          [Back]
token doubler | counter doubler (core)
Same job (1)
> Anointed Procession x2, free
  token doubler. Costs 1 less.
  Not: counter doubler.
  [Swap]  [Texts]  [Scryfall]
Similar, with a difference (1) >
Roles: Vault rules + Scryfall Tagger, 2026-10-09
```

### State B: nothing found

```
1400 px
Rhystic Study  {2}{U}  missing from Avatar Aang                                       [Back]
Does: draw engine (core)      basis: Vault rule + Scryfall Tagger, strong
You own nothing else that does this job in this deck's colours and format.
(2 owned cards do this job but are outside the deck's colour identity: not offered.)
[Buy for $x.xx (Scryfall, <date>)]  [Open on Scryfall]  [See upgrades on the deck page]
Looking for one-shot draw instead?  [Show similar, with a difference (3)]

390 px
Rhystic Study            [Back]
draw engine (core)
You own nothing else that does
this job here.
2 owned are outside the colours.
[Buy $x.xx, <date>] [Scryfall]
[Upgrades] [Similar (3) >]
```

The "(2 owned ...)" line is rule 8 of section 7: how many were removed and why, without listing them as options.

### State C: found, but borrowed by another deck

```
1400 px
Doubling Season  {4}{G}  missing from Sliver Swarm                                    [Back]
Same job (1)
+--------------------------------------------------------------------------------------+
| (img) Parallel Lives   {3}{G}   x1 owned, BORROWED by Avatar Aang                     |
|  Does: token doubler (core).  Costs 1 less.  Does not: counter doubler.              |
|  Using it here leaves Avatar Aang one short of it.                                   |
|  [Move it here from Avatar Aang]   [Buy another: $x.xx (Scryfall, <date>)]           |
+--------------------------------------------------------------------------------------+
Borrowing applies the owner's independence rules (#165): Move changes nothing by itself.

390 px
Doubling Season          [Back]
Same job (1)
> Parallel Lives x1, borrowed
  by Avatar Aang. Token doubler.
  Costs 1 less. Not: counter doubler.
  Leaves Avatar Aang 1 short.
  [Move] [Buy $x.xx, <date>]
```

### State D: the Vault knows no role for the card

```
1400 px
Cloudstone Curio  {3}  missing from Sliver Swarm                                      [Back]
The Vault knows no role for this card yet, so it cannot look for cards that do the same job.
That is not the same as "you own nothing like it".
Its text (Scryfall): <Oracle text shown here>
[Open on Scryfall]  [Suggest a role (assistant or you)]

390 px
Cloudstone Curio         [Back]
No role known for this card.
Not the same as "nothing like it".
[Text] [Scryfall] [Suggest a role]
```

`[Suggest a role]` goes to the review queue of `docs/card-roles-design.md` section 4 (a separate design, not built).

## 9. What the criteria of #166 look like after this note

| Criterion | State after this note |
|---|---|
| Research note with each source, its terms linked and dated, coverage, recommended approach. **Owner review.** | Written here. Terms read 2026-10-09 for Scryfall, EDHREC, Commander Spellbook (what exists), Wizards' Fan Content Policy; Moxfield, Archidekt, MTGGoldfish, 17Lands cited from the dated earlier reads and marked "not re-read". Coverage: Scryfall-wide numbers measured; **on the owner's collection not measured**. Waits for the owner |
| Role vocabulary agreed, with examples from the owner's four decks | Proposed in section 5 with examples from the issue and checked against Scryfall's tags. Which of them the owner's four decks hold was not looked at. **Waits for the owner's agreement** |
| Mockups at 1400 and 390 px, including "nothing found" and "found but borrowed" | Text mockups in section 8 (states A to D). No screenshots (no code). Owner review |
| No data ingested before its terms are recorded | True: this change is documents only. The rule that tag-derived rows wait for the #62 record is repeated in sections 2.1 and 4 |

## 10. Decisions for the owner

Answered on 2026-10-09: the owner took every recommendation below (1, 2, 3, 4 and 6 are built, section 12; decision 5, sending the question to Scryfall, is still only the owner's to make).

1. **Is the vocabulary in section 5 the right size and wording?** Recommendation: yes as a start (22 slugs, two modifiers); remove `lifegain` and `evasion` if you
   would rather keep equivalents about the jobs you named. Change a name, not a slug, as often as you like; slugs stay.
2. **Two tiers ("same job" and "similar, with a difference") or only the first?** Recommendation: two tiers, the second collapsed.
3. **Mana value: rank and show, or cut above a difference?** Recommendation: rank and show.
4. **Release order.** Recommendation: release 1 on Vault rules only; the Tagger joins after the #62 record. Nothing in this note needs the question to Scryfall to be sent
   first, but the Tagger input does.
5. **Send the Scryfall question** (`docs/outreach-drafts.md`, not sent), so release 2 is not blocked. Only the owner sends it.
6. **Fix the lab's example** (Cloudstone Curio is not a token doubler) when the doc is next edited; no decision needed, listed so it is not lost.

## 11. Tasks that follow (none started)

1. Agree the vocabulary (this note). 2. Extend `vault/role_rules.py` with the new rules and fixtures (token doubling, counter doubling, bounce, free counterspell,
repeatable versus one-shot draw, mana doubling), each with cards it must and must not match. 3. The agreement report between rules and tags over a loaded
catalog, and the per-deck report for the owner's four decks; publish both in this file. 4. Everything in the "Tasks that follow" of
`docs/card-roles-design.md` (schema, pipelines, `equivalents`, MCP tools, the suggestion queue).

## 12. As built (#166)

The owner approved the recommended defaults of section 10 on 2026-10-09 ("take the recommendations"): the Vault's own text rules first, the 22-role vocabulary of section 5, two tiers, mana value ranked and shown (not a filter), and Scryfall's community tags only as an internal second input after the #62 terms record. That record does not exist, so **no tag is read by any of this**: the code that finds roles for equivalents (`vault/equivalents.py`) imports no tag table, no catalog source and no network client (a test fails if it does), and **no data was ingested from any outside source** (the only inputs are the Oracle text and the type line the catalog already holds). Decision 5 (sending the question to Scryfall) is still the owner's to make and still blocks release 2.

What replaced what: `deck_ideas.equivalence` (two cards were equivalent when they shared a core coarse role, candidates found through Tagger tags) is gone. `deck_ideas.alternatives` now asks `vault.equivalents`; the lanes of the Ideas view and `deck_stats` keep the eight coarse roles (`vault/deck_tools.py`) exactly as they were. No migration at that point (the roles were computed from the text on request and cached in the process by catalog content hash; section 13 stores them with the card); the table, the pipelines and the suggestion queue of `docs/card-roles-design.md` stay for #174.

### 12.1 The vocabulary, as coded

Order is the priority: a card's **primary job** is its first `core` role in this order. Slugs are stable; names may be reworded. Roles of one **family** are neighbours (a one-shot draw is a neighbour of an every-turn draw).

| Slug | Plain name | Family | What it means |
|---|---|---|---|
| `mana-rock` | Mana rock | ramp | A non-creature permanent that taps for mana |
| `mana-creature` | Mana creature | ramp | A creature that taps for mana |
| `land-ramp` | Land ramp | ramp | Puts lands onto the battlefield or allows extra land drops |
| `treasure` | Treasure | ramp | Makes Treasure tokens, once or again and again |
| `mana-multiplier` | Mana doubler | ramp | Makes lands or other sources produce more mana |
| `draw-once` | Card draw (once) | draw | A spell or effect that draws a fixed amount, one time |
| `draw-engine` | Card draw (every turn or every trigger) | draw | Draws again and again while it stays |
| `tutor` | Tutor | tutor | Searches the library for a card that is not just a land |
| `recursion` | Get cards back | graveyard | Returns cards from the graveyard to hand |
| `reanimate` | Reanimate | graveyard | Returns creatures from a graveyard to the battlefield |
| `token-maker` | Token maker | tokens | Creates creature or other tokens, once or again and again |
| `token-doubler` | Token doubler | token-doubling | Doubles the tokens you create |
| `counter-doubler` | Counter doubler | counter-doubling | Doubles the counters put on your permanents |
| `counterspell` | Counterspell | counter | Counters a spell or ability |
| `free-counterspell` | Free counterspell | counter | A counterspell that can be cast without paying its mana cost |
| `spot-removal` | Spot removal | removal | Removes or neutralises one target permanent |
| `bounce` | Bounce | removal | Returns permanents to their owners' hands (one or all) |
| `sweeper` | Board wipe | removal | Removes many permanents at once |
| `sacrifice-outlet` | Sacrifice outlet | sacrifice | Lets you sacrifice permanents again and again |
| `protection` | Protect my stuff | protection | Keeps your permanents from removal or counters |
| `lifegain` | Lifegain | lifegain | Gains life |
| `evasion` | Evasion | evasion | Makes creatures hard to block |

### 12.2 The rules, as coded

Each rule is a function of the card's Oracle text (reminder text removed, the card's own name as `~`, every face) and its type line. Every role a card gets is shown as `basis: "computed"` with the id of its rule. A role is `core` (the card exists to do it) unless it is a **rider** (`treasure`, `token-maker` that does not repeat, `draw-once`, `protection`, `lifegain`, `evasion`) on a card that has another job, which makes it `incidental` (Mind Stone is a mana rock; its draw is a side effect). Whether a Treasure, a token or a draw **repeats** is read from the line that makes it: `whenever`, `at the beginning of`, `each time`, or an activated ability on a permanent repeats; a spell, an `enters` or `dies` trigger, or a cost that sacrifices the card itself does not. Rules version: `2026-10-09.1` (the answers carry it; it changes with any rule).

| Rule | Role | Matches | Known to get wrong |
|---|---|---|---|
| `mana-rock-taps` | `mana-rock` | a non-creature artifact or enchantment with an activated '{T}: Add ...' ability | a rock that adds mana only under a condition, or at a cost per use, is matched like Sol Ring; a Treasure-making card is not a rock |
| `mana-creature-taps` | `mana-creature` | a creature that is not a land with an activated '{T}: Add ...' ability | a creature that adds mana only under a condition is matched; a land creature (Dryad Arbor) is deliberately a land, not a mana creature |
| `land-ramp-fetch` | `land-ramp` | a non-land card that searches the library for a land (or a basic type) and puts it onto the battlefield | does not check that the land comes in untapped or that the spell is cheap; a land that fetches a land (Evolving Wilds) is deliberately not ramp |
| `land-ramp-extra-drop` | `land-ramp` | 'you may play an additional land' (or two, or any number), 'play lands from the top of your library', or a land put onto the battlefield from hand | a card that gives the extra land drop only to opponents is matched too |
| `treasure-create` | `treasure` | creates one or more Treasure tokens; repeats when it is a trigger or an activated ability of a permanent | a one-shot Treasure on a spell that is mostly something else is matched but marked incidental; a payoff that only watches Treasures being created is not |
| `mana-doubling` | `mana-multiplier` | 'if you tap a permanent for mana, it produces twice as much', 'whenever you tap a land for mana, add one mana of any type', 'adds an additional' | matches symmetrical versions that help every player; an effect worded in a way not listed here is missed |
| `draw-once` | `draw-once` | 'draw a card/two/X cards' in a spell, an 'enters' trigger or an ability that spends the card itself | a symmetrical wheel or a draw that is only a drawback is matched like a plain draw spell; a payoff that only watches draws ('whenever you draw') is not |
| `draw-engine` | `draw-engine` | a draw in a 'whenever', 'at the beginning of' or activated ability of a permanent (Phyrexian Arena, Rhystic Study) | a conditional trigger that rarely happens is matched like an upkeep draw; so is a creature that draws when it deals damage |
| `tutor-library` | `tutor` | 'search your library for' something that is not only a land | a card that fetches a named, narrow card is matched like Demonic Tutor |
| `recursion-to-hand` | `recursion` | 'return ... card(s) from a graveyard to your/its owner's hand' | a card that returns only itself ('return ~ from your graveyard') is not matched; one that returns cards of an opponent's graveyard is |
| `reanimate-to-battlefield` | `reanimate` | 'return/put ... card from a graveyard onto the battlefield', and 'return enchanted creature card to the battlefield' (Animate Dead) | a reanimation of only the card itself is not matched; a card that puts any card, not only creatures, is matched |
| `token-create` | `token-maker` | 'create ... token(s)' that are not only Treasure; repeats when it is a trigger or an activated ability of a permanent | a one-shot token on a spell or creature is core unless the card has another job; 'create a token that's a copy' is matched like any other token |
| `token-doubling` | `token-doubler` | 'if an effect would create one or more tokens under your control, it creates twice that many' (and the 'would be created' wording) | a doubler that applies only to some tokens (creature tokens: Parallel Lives) is matched like one for all tokens |
| `counter-doubling` | `counter-doubler` | 'if you would put one or more counters ... put/it puts twice that many' | an effect that adds one more ('that many plus one': Hardened Scales) is deliberately not a doubler; a doubler for only some counters is matched like one for all |
| `counter-spell` | `counterspell` | 'counter target spell' or 'counter target ... ability', 'counter that spell' | a counter with a heavy condition (only creature spells, only noncreature) is matched like an unconditional one |
| `counter-free` | `free-counterspell` | a counterspell whose text also lets it be cast 'without paying its mana cost' or 'rather than pay this spell's mana cost' | a counterspell with a free mode that has a steep cost (exiling a card, returning a land) is matched like one that is free in a plain situation |
| `removal-target` | `spot-removal` | 'destroy' or 'exile' target creature/artifact/enchantment/planeswalker/permanent, damage to a target, '-N/-N', fights, 'can't attack or block' Auras | an optional 'may' or a conditional clause is not read; 'exile target creature card from a graveyard' is correctly not matched |
| `removal-edict` | `spot-removal` | 'target player/opponent sacrifices a creature' (an edict) | an edict that the opponent can dodge by having a worse creature is matched like a precise removal spell |
| `bounce-owner-hand` | `bounce` | 'return target/all/each ... permanent, creature, artifact, enchantment, planeswalker, land or spell to its owner's hand' (not your own permanents, not cards from a graveyard) | a bounce that only rescues your own card ('target creature you control') is deliberately left out; a bounce with a restriction ('attacking creature') is matched like an unrestricted one |
| `sweeper-coarse` | `sweeper` | 'destroy/exile all creatures/permanents/...' (not 'you control'), damage to each creature, 'all creatures get -X/-X', 'each player sacrifices all creatures' | a sweeper that spares a type or has an exception in a later sentence is still matched |
| `sweeper-bounce-all` | `sweeper` | 'return all/each creatures/permanents ... to their owners' hands' | a mass bounce that hits only attackers or one colour is matched like a full wipe |
| `sweeper-overload` | `sweeper` | a spell with Overload that is also a bounce or removal spell (Overload turns 'target' into 'each') | the overload cost is often very high; it is read as the card's identity, not as a common mode |
| `sacrifice-outlet` | `sacrifice-outlet` | a cost of 'Sacrifice a/another/an/two creature(s), permanent ...:' before a colon (a repeatable outlet) | an outlet limited to one type of permanent (a Treasure, a land) is matched like a creature outlet |
| `protection-grant` | `protection` | permanents you control (or a target one) gain/have hexproof, indestructible, shroud or protection from something; 'phase out' | a keyword the card has printed on itself is not matched; a protection that only helps one named permanent is matched like a group effect |
| `lifegain-gain` | `lifegain` | 'you gain N life', 'you gain life equal to', 'target/each player gains N life' | an opponent's gain that is a drawback for them ('its controller gains life equal to its power') is not matched; a payoff that only watches life gain is not |
| `lifegain-lifelink` | `lifegain` | lifelink (a keyword, or granted to creatures) | always a side effect of a creature, so always marked incidental |
| `evasion-grant` | `evasion` | creatures you control, a target creature, or an Aura/Equipment's creature gain/have flying, menace, shadow, fear, intimidate or horsemanship | a keyword the creature has printed on itself is not matched; trample is not counted as evasion |
| `evasion-unblockable` | `evasion` | creatures you control or a target creature 'can't be blocked' | 'can't be blocked by' a narrow kind of creature is matched like a plain unblockable |

Not measured: how often these rules agree with Tagger over the whole catalog (that needs the tags, which stay off), and how many of the owner's real cards get no role (no access to the collection; the numbers belong in the first production check). The test table (`tests/test_equivalents.py`) holds one or more real cards for every rule and the cards each must not match, among them the owner's examples.

### 12.3 Matching, as coded

Filters first, as in section 6 and never hidden: the candidate is owned, not the card itself, not a basic land, in the deck's colour identity, legal (or restricted) in the format, with copies left under the format's limit and not only this deck's own copies. The answer **counts** what the colour, format and copy filters removed (`filtered_out`, `filtered_note`: "3 outside this deck's colour identity (WUG)") and names none of it as an option.

- **Same job (`same_job`):** the candidate has the target's primary job as a core role, and the modifiers agree: a Treasure or token maker that repeats for one that repeats, and, where the target's job has a refinement (`free-counterspell` refines `counterspell`), the candidate has it too. A candidate that does more is still the same job and says what it adds (`extra`: a free counterspell for a plain one).
- **Similar, with a difference (`similar`):** it shares another core role with the target, or has a core role of the same family (a one-shot draw for a draw engine), or has the primary job but not the modifier. `different` says which, `lacks` lists the target's core roles it does not do ("Does not: counter doubler"), and `why` is built from the role entries only (a test checks that it can name no role the data does not hold).
- **No role (`no_role`):** a target with no core role matches nothing, and the answer says "The Vault knows no role for this card yet ... That is not the same as 'you own nothing like it'". Cloudstone Curio is the fixture for it (it returns your own permanents; no role of the vocabulary covers that), and it replaces the Curio example of the lab design.
- **Order:** same job before similar, then a free copy before a card another deck holds, then more shared jobs, then the smaller mana value difference, then the name. The tiers page as one list (cursor), and `tiers` counts both over the whole list.
- **The type is said when it differs** (`type_note`: a creature for an enchantment), and both Oracle texts are in the answer (`oracle_text`, Wizards' text via Scryfall, shown with the Fan Content notice).

Examples as the tests hold them (`tests/test_equivalents_api.py`): Doubling Season (token doubler and counter doubler): Anointed Procession is the same job and does not double counters; Vorinclex is similar (counter doubler only, a creature). Rhystic Study (draw engine): Phyrexian Arena and Mystic Remora are the same job, Divination is similar (draws once). Cyclonic Rift (bounce, and a board wipe when overloaded): Evacuation is the same job with both, Unsummon the same job without the wipe, Wrath of God similar. Fierce Guardianship (counterspell, free): Force of Will is the same job, Counterspell is similar ("counterspell but not free counterspell"). Smothering Tithe (Treasure again and again): Dockside Extortionist is similar (Treasure once). Parallel Lives: Doubling Season, held by another deck, is the same job and borrowed.

### 12.4 The interface

Existing route and tool, new answer: `GET /decks/{id}/ideas/alternatives` and `get_card_alternatives` (documented in `docs/api.md`, `docs/ai-parity.md`, `public/llms.txt`). Each row has `tier`, `roles` (name, strength, rule, `repeatable`), `shared_roles`, `lacks`, `extra`, `different`, `type_note`, `oracle_text`, `why`, `mana_value_change`; the answer has `tiers`, `filtered_out`, `filtered_note`, `roles_version`, and the target's `roles`, `primary_role` and `oracle_text`. No new tool: the design of this note lists none, and `roles_of` / `equivalents` as tools belong to the data layer of #174 (a `card_roles` table). Provenance: a `computed` block naming the rules version, with Scryfall's card data and prices as inputs and the Fan Content notice; Scryfall's tags are not an input of this answer.

The Ideas view (`public/views/ideas.jsx`, wording in `public/lib/ideas.js`) draws the panel: "Same job (n)" with each candidate's roles, what it does not do, the difference in one sentence and an "Oracle text of both cards" fold; "Show similar, with a difference (n)" (collapsed until asked, at both widths); the nothing-found state with the counts of what the filters set aside and the similar ones one tap away; the borrowed state with the lender, Move and the price of a copy; and the no-role state with the card's own text. Screenshots of the real view (a local server on synthetic data, placeholder art, never production), 1400 and 390 px:

| State | 1400 px | 390 px |
|---|---|---|
| Found: same job, similar collapsed | `docs/screenshots/ideas-equivalents-found-1400.jpg` | `docs/screenshots/ideas-equivalents-found-390.jpg` |
| Found, with both Oracle texts open | `docs/screenshots/ideas-equivalents-texts-1400.jpg` | `docs/screenshots/ideas-equivalents-texts-390.jpg` |
| Found, with the similar tier open | `docs/screenshots/ideas-equivalents-similar-1400.jpg` | `docs/screenshots/ideas-equivalents-similar-390.jpg` |
| Nothing found for the same job (cards outside the colours counted, similar one tap away) | `docs/screenshots/ideas-equivalents-nothing-1400.jpg` | `docs/screenshots/ideas-equivalents-nothing-390.jpg` |
| Found, but borrowed by another deck | `docs/screenshots/ideas-equivalents-borrowed-1400.jpg` | `docs/screenshots/ideas-equivalents-borrowed-390.jpg` |
| No role known | `docs/screenshots/ideas-equivalents-norole-1400.jpg` | `docs/screenshots/ideas-equivalents-norole-390.jpg` |

The phone check (`scripts/measure_phone.js`) at 390 px passed in all six: no horizontal overflow, no tap target under 44 px, no text under 12 px (`scripts/equivalents_evidence.py` takes them against a local server; the synthetic seed data is not committed).

### 12.5 Left for later

Release 2 (Scryfall's tags as an internal second input, with the agreement report between rules and tags) waits for the #62 terms record and decision 5. The `card_roles` table, the reviewer pipeline, `role_suggestions` and the "Suggest a role" button wait for #174; so do `roles_of(card | deck)` as a tool and the deck-level role counts on the new vocabulary. The per-deck report on the owner's four decks (roles per card, cards with no role) needs the real collection and is a first production check. Co-occurrence inside the person's own decks as a tie-breaker (section 2.4) is not built.

## 13. As built (#435): what a card does, shown everywhere

The owner asked in chat on 2026-10-10: "if we have the 'what the card does' (token generator, mana generator, token duplicator, counter proliferation, counterspell, protection, removal and so on) that we use to power the strategy logic and the AI, can we show these attributes as a list of points and give a way to interact with those lists?" There is no separate design document; this section is the record, and where a point was open the conservative choice was taken (13.5). Nothing was ingested from outside: the roles are still only the rules of section 12 over the Oracle text and type line the catalog already holds, and Scryfall's Tagger tags stay a separate, labelled list.

### 13.1 Storage: read when the catalog loads, never per request

- `oracle_cards.roles` (JSONB, migration `0123`): `{slug: {strength, rule, repeatable}}` in the Vault's order, with a **GIN index** (`ix_oracle_cards_roles`), so "which cards have these roles" is `roles ?& array[...]` (all) or `?|` (any): one indexed query. `oracle_cards.roles_version` is the version of the rules that read the row (`equivalents.RULES_VERSION`; null means not read yet).
- `catalog_sync.oracle_card_row` reads the roles (`vault.card_roles.stored`) **before** the content hash is taken, and the hash covers them and the version. So the daily job's diff rewrites a row when its text **or the rules** change, and the first load after this change rewrites every row once: that is the backfill (the migration writes no data, so it cannot disagree with a later rule change). Until that load has run, a card whose `roles_version` is not the current one is read on the fly by the card endpoints (never shown as "no roles"), `/collection/roles` says `read_cards: 0` with a note, and the filter matches nothing rather than guess.
- **Unreadable text is reported, not hidden.** A card whose text makes the rules raise is stored with no roles and named in the sync result (`unread_roles`, with the first error per card in `unread_roles_why`) and in a log warning, the way `unread_deck_rules` names a deck wording the legality check cannot read; the load goes on.
- Timing on a large catalog (`tests/test_card_roles.py::test_filtering_a_large_collection_is_one_query_on_the_index`: 22,000 catalog cards, 5,500 owned printings, local Postgres): the role filter query took about 21 ms and the counts of all 22 roles about 25 ms.

### 13.2 What a person sees

The wording is one string everywhere (`card_roles.LABEL`): **"The Vault's reading of the card text, not an official classification"**; the answers' provenance is a `computed` block over Scryfall's Oracle text, naming the rules version.

- **Card panel** (any card, in Browse, Sets, Lab, Ideas, a deck): "What this card does": one short point per role ("Doubles tokens", "Counters a spell", "Draws cards again and again"), marked "main job" or "on the side", each opening "Why?": the rule that found it, what the rule matches and what it is known to get wrong (also the hover text), and "Show my cards that do this" (Browse filtered to that role). A card with no role says that is not the same as doing nothing. Scryfall's Tagger tags, when the card has any, are a separate line labelled a community's opinion.
- **Browse**: "What it does" opens the 22 roles with how many different cards you own with each; pick one or more, "All of them" (the default) or "Any of them", combined with the search, set, printing, type, mana value, bucket and tag filters. The line Browse already had, "N entries match", is the count. The filter is `GET /collection/cards?role=...&role_match=...` (also on `search_cards`), so a shared collection can be filtered the same way.
- **Deck page, "What it does"**: every one of the 22 roles with the number of the deck's cards that have it; pressing one lists them (each opens its panel); a role with none shows 0 and says that means its rules found nothing; cards with no role are named, lands are counted apart. It works for any deck the page can read (`POST /decks/roles` takes the text), saved or not.
- Phone first: 390 px, controls at least 44 px high, text at least 12 px, nothing scrolls sideways (`scripts/measure_phone.js` found no target under 44 px, no text under 12 px and no overflow in the card panel, the Browse filter and the deck tab).

| State | 1400 px | 390 px |
|---|---|---|
| Card panel, one role's rule open | `docs/screenshots/card-roles-card-1400.jpg` | `docs/screenshots/card-roles-card-390.jpg` |
| Browse, three roles picked, "Any of them" | `docs/screenshots/card-roles-browse-1400.jpg` | `docs/screenshots/card-roles-browse-390.jpg` |
| Browse, the filtered list | `docs/screenshots/card-roles-browse-list-1400.jpg` | `docs/screenshots/card-roles-browse-list-390.jpg` |
| A deck's "What it does" | `docs/screenshots/card-roles-deck-1400.jpg` | `docs/screenshots/card-roles-deck-390.jpg` |

The screenshots are of the real app against a local database of synthetic data (well-known cards, one demo account), never the owner's collection.

### 13.3 For assistants

One read-only tool, `card_roles` (scope read; provenance `computed`): `card` gives a card's roles, `role` (one or more, `match` all or any) the person's cards that have them (one row per card, with copies and the rule), `deck_id` what a saved deck does, nothing the 22 roles with how many of the person's cards have each; `share_id` reads a shared collection. Its answers carry the same `label`, `point`, `rule` and `why` as the web view, and the tool description says the roles are the Vault's reading and not Scryfall's tags. `search_cards` also takes `role` and `role_match`. `POST /decks/roles` is on the read-only token's allowlist (`READ_ONLY_POSTS`) because it only computes. The `collection-analyst` and `deck-upgrader` skills and the `vault-collection-analyst`, `vault-curator` and `vault-deckbuilder` agents name the tool (generated plugin files rebuilt).

### 13.4 Endpoints

`GET /catalog/roles` (the vocabulary and the rules), `GET /catalog/cards/roles` (one card), `GET /collection/roles` and `/collection/roles/cards` (also under `/shared/{id}/collection`), `GET /collection/cards?role=`, `POST /decks/roles`. Documented in `docs/api.md`, `docs/ai-parity.md`, `docs/agents.md`, `public/llms.txt` and the in-app Help.

### 13.5 Choices taken where the issue left a point open, and what was not built

- A role filter with several roles needs **all** of them by default (like the other filters, which narrow); "any" is one click. Core and incidental roles both match; the lists say which is which. A "core only" filter was not built.
- The Ideas lanes and `deck_stats` keep the eight coarse Tagger-based roles exactly as they were (the Stats tab now labels them a community's opinion); the 22 roles are a separate list.
- The Browse role choice is not kept in the address (the type and mana value filters are not either); opening Browse from a card's "Show my cards that do this" starts it with that role.
- The role counts in the picker are for the whole collection, not for the chosen bucket.
- Not built: filtering the Lab, Sets or Ideas by role; a role chart; letting a person correct a role (the reviewer pipeline of `docs/card-roles-design.md` stays with #174).
