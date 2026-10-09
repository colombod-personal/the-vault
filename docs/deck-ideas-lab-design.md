# The deck ideas lab: what can I build, what is missing, what can stand in (design for #161, epic #158)

Status: agreed by the owner on 2026-10-08 (#287: decisions 5 to 9, all as recommended). Implementation is #163. Built so far: the server side of task 1 (`vault/deck_ideas.py`, `vault/api/ideas_api.py`, the tools `get_deck_ideas` and `get_card_alternatives`; "As built" below); the web view, the deck page change flow, removing the Graph and the links are still to do.

Owner direction (2026-10-05, `docs/graph-and-lab-review.md`): the Graph's seven modes are cut. The Graph becomes a **deck ideas lab**: how a deck could be built from the collection, what is missing, and for each missing card which owned card could stand in or deliver the same dynamic.

## What exists today (checked in the code, 2026-10-06)

- `public/views/graph.jsx` (1,355 lines) draws seven network or chart modes with the third-party cytoscape script loaded from unpkg (`public/index.html`). The Deck map mode cannot be reset and shows less than the deck page (#160).
- The **deck page** (`public/views/deck.jsx`) already has tabs for cards (owned versus missing, with copy counts), stats (roles, mana curve), legality, upgrades (`find_upgrades`: legal cards in the deck's colours, priced), combos (`find_combos`: in the deck and one card away) and a buy list.
- The Vault's roles today are the eight coarse ones in `vault/deck_tools.py` (`ROLE_TAGS`: ramp, draw, removal, sweeper, counterspell, tutor, recursion, sacrifice outlet). The finer roles (treasure generation, token doubling, repeatable draw) come with #166 and #174, which are designed but not built.

## The one job

**For a deck I care about: which cards am I missing, and for each, what do I already own that does the same job, what is another deck holding, and what would it cost to just buy it?** Nothing else belongs here. Stats, legality, the buy list, combos and upgrades already exist on the deck page, so the lab links to them and never repeats them.

## Where it lives, and how it connects (nothing duplicated)

- **Its own view, "Ideas", replacing the Graph in the navigation.** Not a section of the Lab: the Lab (#162) is where a person decides to buy or sell; this is where they explore a deck. `#/graph` redirects to `#/ideas`.
- **From the Lab:** a Buy row for a missing card gets **Find a substitute**, which opens Ideas on that card in that deck.
- **From the deck page:** a missing row in the cards tab gets the same link.
- **Back to them:** an owned alternative links to the card; **Swap into the deck** opens the deck page's change flow, which does **not exist yet** (today the deck page only saves the loaded list and its upgrades tab shows suggestions without applying them), so #163 includes it: a cut and add proposal, `validate_deck_changes` to check it, and a confirmation step before `update_deck`; a missing card's **Buy** links to that card's entry in the Lab's allocation-aware Buy list (`purchases`, #165) and to the card's Scryfall page, **not** to the per-deck buy list, which compares one deck with every owned copy and would omit a copy another deck is holding. The lab never reimplements those screens.
- **Read-only in v1.** The lab proposes. Applying a swap goes through the existing `validate_deck_changes` then `update_deck` (write scope, asks first), on the deck page (once #163 builds that flow) or through the assistant, which already has both tools.

## The flow

1. **Pick a deck.** v1 starts from a saved deck (default: the most recently edited) or an Archidekt link, which must be **saved first**: the lab asks "Save this deck to explore it" and uses the existing link import (`/decks/import-link`, which creates a saved deck the person can delete). The Vault still only reads Archidekt; nothing is analysed without a saved deck id, so the routes, pagination and `#/ideas/{deck}/{card}` URLs all work on a saved id. Transient analysis of an unsaved list is not offered. Starting from a commander or a theme (tokens, treasure) needs the finer roles and is phase 2 (below).
2. **See what the collection covers.** The deck's cards in role lanes (ramp, draw, removal, and so on), not by colour. Each card is a node: owned, partly owned, borrowed by another deck (#165), or missing. A header says "92 of 100 cards covered; 8 missing; 3 borrowed". Lines join cards that form a combo you already own (from `find_combos`).
3. **Select a missing card.** A panel lists owned alternatives, best first, each with the card image, mana cost, copies owned, **why it matches** ("both: treasure generation, core") and a flag when it is borrowed or outside the deck's colour identity or format (those are filtered out before ranking, per #166).
4. **Decide.** Use the alternative (**Swap into the deck**), move a copy from another deck (offered only when #165 says a donor copy exists), or buy (cheapest known price, dated, with a link).
5. **Always resettable.** A visible **Clear** control returns to the start; Esc does the same; every selection is a history entry (`#/ideas/{deck}/{card}`), so Back undoes one step. This is the bug in #160, and it is a requirement.

## Mockups

`[...]` is a control. **Text first, light on graphics (owner direction, 2026-10-06):** the lanes are plain text rows (name, status, a small coloured dot), not an art grid. Images are small thumbnails (about 40 px) shown only for the selected card and its alternatives, never full card art; a larger image opens only when the person taps the thumbnail. Where art does appear it keeps the circle mask and is credited (artist and Scryfall) in the card panel.

### 1. The start (nothing picked)

```
1400 px                                              390 px
Ideas                                                Ideas
Pick a deck to see what you could build.             Pick a deck
+--------------+ +--------------+ +-------------+   +------------------------+
| Sliver Swarm | | Avatar Aang  | | The dragon..|   | Sliver Swarm      94%  |
| 94% covered  | | 100% covered | | 88% covered |   | Avatar Aang      100%  |
| [Open]       | | [Open]       | | [Open]      |   | The dragon...     88%  |
+--------------+ +--------------+ +-------------+   | Nazgul            71%  |
[Paste a list or an Archidekt link]                  | [Paste or link]        |
                                                     +------------------------+
```

### 2. A deck with a few missing cards

```
1400 px
Sliver Swarm   92 of 100 covered | 8 missing | 3 borrowed        [Clear]
RAMP            DRAW              REMOVAL           WIN CONDITIONS
(o)(o)(o)(o)    (o)(o)(?)(o)      (o)(o)(?)         (o)(o)(?)(~)
 o owned   ? missing   ~ borrowed by another deck   --- combo you own
Missing cards are listed first on the right: [? Cloudstone Curio] [? ...]

390 px
Sliver Swarm  92/100 covered   [Clear]
Missing (8)  Borrowed (3)  All
> Cloudstone Curio   missing
> Hunter Sliver      borrowed
RAMP (4)  > ...
DRAW (4)  > ...
```

### 3. A missing card with alternatives

```
1400 px                                             390 px
[? Cloudstone Curio]  role: token doubling (core)   Cloudstone Curio       [Back]
Owned alternatives                                  role: token doubling (core)
+-----------------------------------------------+   +--------------------------+
| (img) Anointed Procession  x2 owned           |   | (img) Anointed Procession|
|  same job: token doubling (core). MV 4 vs 3   |   | x2 owned, same job:      |
|  [Swap into the deck]                         |   | token doubling. [Swap]   |
| (img) Doubling Season  x1, in Aang            |   +--------------------------+
|  borrowed from Avatar Aang [Move] [Buy $6.60] |   | Doubling Season  borrowed|
+-----------------------------------------------+   | [Move] [Buy $6.60]       |
Or buy Cloudstone Curio: $1.20 (Scryfall, 6 Oct) [Open on Scryfall]
```

### 4. A missing card with no alternative

```
1400 px                                                   390 px
[? Cloudstone Curio]  role: token doubling (core)         Cloudstone Curio    [Back]
You own nothing else that does this job in this           token doubling (core)
deck's colours and format.                                You own nothing else that
[Buy for $1.20 (Scryfall, 6 Oct)] [Open on Scryfall]      does this job here.
[See upgrades on the deck page]            [Back]         [Buy $1.20, 6 Oct]
                                                          [Scryfall] [Upgrades]
```

### 5. A fully covered deck

```
1400 px
Avatar Aang   100 of 100 covered | 0 missing | 0 borrowed        [Clear]
Every card is in your collection and no copy is borrowed.  Nothing to decide here.
[Open the deck page]  [See upgrades]
RAMP   (o)(o)(o)(o)   DRAW (o)(o)(o)   REMOVAL (o)(o)   OTHER (o)(o)   LANDS (o)...
(the role lanes stay visible so the deck can still be explored)

390 px
Avatar Aang  100/100 covered  [Clear]
Nothing to decide here.
[Deck page] [Upgrades]
RAMP (4) >   DRAW (3) >   REMOVAL (2) >
OTHER (2) >  LANDS (36) >
(tap a lane to open it; Clear sits at the top right)
```

## The data (every number from the server)

- **`GET /decks/{id}/ideas`**: the deck's cards grouped by primary lane (one lane per card, see Tests) with `have`, `need`, the #165 allocation quantities `gets`, `not_owned` and `held_by_other_deck` (so a mixed shortage is shown as it is: with two decks each needing two copies and one owned, the deck allocated none lacks one unowned copy, to buy, and one copy held by the other deck, to move), a `status` of `owned` (nothing lacking), `partial` or `missing` computed from them, and **borrowed as an independent annotation** (`borrowed_from`, a deck name, true when `held_by_other_deck > 0` or the deck holds a copy another deck also wants), counts for the header (`missing` counts cards with `not_owned > 0`; `borrowed` counts cards with `held_by_other_deck > 0`; a card can be both, and each is counted once per category), and the combos already owned as edges. Lanes are cursor-paged (`limit`, `cursor`, HAL `next`) so no response grows with the deck; the header counts are computed over the whole deck first.
- **`GET /decks/{id}/ideas/alternatives?card=NAME&format=commander`** (cursor-paged; `format` is validated against the formats the Vault supports, default `commander` as the deck tools already assume, because a saved deck stores no format; the Ideas header shows the chosen format as a chip, remembered per browser, the same selector the deck page uses; the assistant tool takes the same `format` argument): owned alternatives for a missing card, from `equivalents(card, deck_id)` in `docs/card-roles-design.md`: shared `core` role, colour identity, format legality and **remaining copy allowance** (a card the deck already contains up to its limit, such as an owned Sol Ring in a Commander deck that already runs it, is excluded, as `find_upgrades` already excludes cards already in the deck) applied as filters before ranking, owned first, then mana value difference. Each row: card, copies owned, `borrowed_from` (a deck name, from #165), `why` (the shared roles and their strength), `legal` and `in_colours`, price and its date for a card that would have to be bought, and the `move` option when #165 offers one.
- **Assistant tools (parity):** `get_deck_ideas` and `get_card_alternatives`, read-only; they return prices, so they are classified as Scryfall data with provenance and the Fan Content notice. A test fails if a tool that returns prices is classified own-data-only.
- **Tenancy and sharing:** own account only. A shared collection has no decks of the owner's to explore, so both routes answer 404 under `/shared/{id}/...` (decks and collections are separate share kinds). An Archidekt deck is explored only after the person saves it (see the flow); the Vault only reads Archidekt.
- **Positions:** the lanes are a deterministic grid computed in the browser from the server's order. There is no physics layout.

## Phasing, so it ships before the finer roles exist

- **Phase 1 (on today's eight coarse roles):** pick a saved deck or link, the lanes, missing and borrowed status, alternatives from the coarse roles, the decide actions, Clear and Back. The panel says "coarse roles" so a weak match is not oversold.
- **Phase 2 (after #174):** the finer roles replace the coarse ones with no change to the response shape; start from a commander or a theme ("what could I build from what I own", which needs a server operation over the collection's roles and colour identity).

## Performance budget

For a 100-card Commander deck and the owner's real collection (7,136 card names, four saved decks):

- **Server:** `ideas` first page under 400 ms at the 95th percentile on production; `alternatives` under 300 ms.
- **Payload:** the first `ideas` page under 60 KB gzipped (no images in the payload; only the selected card's panel loads thumbnails of about 40 px, lazily, and the lane rows carry none).
- **First render under 2 s on a mid-range phone**, measured with a throttled run (4x CPU slowdown, a 4G network profile) in a browser test that opens the deck and asserts the lanes are drawn and the main thread has no task over 200 ms.
- **No frozen tab:** no force simulation; at most a few dozen combo lines; lanes with more than 40 cards render windowed. The cytoscape script (loaded from unpkg today) is removed with the old graph, which also removes a third-party load from every page.

## Weight rule

The view stays light: no full-size art, no animation, no physics. A test fails if the first render requests more than 12 images or any image larger than the thumbnail size.

## What is removed

All seven Graph modes and `public/views/graph.jsx`, the `cytoscape` script tag in `public/index.html`, the Graph's navigation entry (replaced by Ideas) and any style used only by it. A test fails if any of them remains or if a link still points at a removed mode.

## Tests (write first)

- The five states above render at 1400 and 390 px (no horizontal scroll at 390).
- Clear, Esc and Back each return to the start or the previous selection and leave no stale selection.
- A missing card with a shared core role lists the owned card; one outside the colour identity or format never appears; with none, the empty message shows.
- A card held by another deck shows as borrowed with that deck's name, and `move` appears only when #165 says a donor copy exists.
- Mixed shortage: two decks each needing two copies of a card with one owned: the deck allocated none shows `not_owned` 1 and `held_by_other_deck` 1 and is counted under both missing and borrowed; Move and Buy are offered for the right halves.
- Copy limits: an owned Sol Ring is never offered as an alternative for missing ramp in a Commander deck that already contains Sol Ring.
- Lanes: every card copy is placed in **exactly one primary lane**, chosen by a fixed priority over the card's roles (ramp, draw, removal, sweeper, counterspell, tutor, recursion, sacrifice outlet); a card with no role goes to an **Other** lane and lands to a **Lands** lane, so nothing is dropped; a multi-role card shows its other roles as tags and is counted once. Header counts equal the sum over all lanes whatever the page size; lanes page independently. Tests: a multi-role card (counted once, one lane, tags for the rest), an untagged card (Other), basic lands (Lands), and the sum equal to the deck's card count.
- Own account only: 404 under `/shared/...`, no other person's decks.
- Provenance: both tools carry Scryfall provenance; prices carry their date.
- Performance: the budget above is a test with a recorded result, run on a 100-card deck.
- `#/graph` redirects to `#/ideas`; the removed code is gone.

## As built (server, #163)

Where the code settles a point the design left open (tests: `tests/test_deck_ideas.py`):

- **The shape of `ideas`.** `GET /decks/{id}/ideas` answers `deck` (name first), `summary`, `allocation`, the three notes, `lanes`, `prices_date` and `provenance`. `lane` narrows it to one lane, which pages with `cursor` (`next_cursor`, `_links.next`); without `lane` every lane shows its first `limit` cards (default 25, at most 100). A cursor without a lane is a 400. The header counts are computed over the whole deck before any page is cut.
- **Lanes.** The fixed priority is ramp, draw, removal, sweeper, counterspell, tutor, recursion, sacrifice outlet. **Every land goes to Lands**, even one with a ramp role (its roles stay as `tags`), so the mana base stays together; a card the catalog does not know goes to Other with `known: false`. Inside a lane the cards that need a decision (`missing`, then `partial`) come first, then by name.
- **Status and borrowing.** `status` is `owned` when nothing is lacking, `missing` when the deck holds none of the copies it needs under the allocation (`gets` is 0), `partial` otherwise. `borrowed` is true when `held_by_other_deck > 0` or the deck holds a copy another deck also wants. `borrowed_from` names the deck holding the copy this deck **lacks**; for the deck that holds a contested copy, `also_wanted_by` names the decks that want it. The header `missing` and `borrowed` count cards with `not_owned > 0` and `held_by_other_deck > 0`, as designed. Basic lands are not allocated (as in deck independence), so they are never short.
- **The allocation.** The default rule of #165 (the deck closest to complete first); `ideas` takes no `priority`. `move` is the donor `deck_independence` picks (the last deck in the order that holds a copy), offered only when `held_by_other_deck > 0`; `buy` only when `not_owned > 0`, priced as `shopping_list` prices (the cheapest known price, dated).
- **Combos.** `include_combos=true` asks Commander Spellbook (the deck's card names are sent only then) and returns the combos of the deck as `combos`, each with `owned` (every card fully held by this deck); a failing upstream is `checked: false` and the rest of the answer stands. They are off by default because they call an outside service.
- **The equivalence decision.** `deck_ideas.equivalence(target_roles, candidate_roles)` is the one function #166's `equivalents` will replace: two cards are equivalent when they share a **core** role (`role_strength`: a Tagger tag weighted weak is `incidental`, any other weight and every role found by a text rule is `core`). Candidates are found through Tagger tags only, among the cards the person owns; the target's own roles include the text rules.
- **Alternatives.** Filters before ranking: Tagger tag for a shared core role, `digital` and token layouts out, legal (or restricted) in `format`, inside the colour identity (the commander's in a Commander-style format with a commander, else the colours of the deck's cards, as `find_upgrades` does), basic lands out, and copies left under the format's limit (`remaining_allowance`; one in a singleton format, four otherwise, any number for cards that say so). A card whose every owned copy is already this deck's own is not offered. Ranking: a free copy (no saved deck needs it) first, then a card another deck holds (`borrowed_from`, `move`, and `buy` for a copy), then the number of shared core roles, the mana value difference, the name. `legal` and `in_colours` are always true on a row (filtered, not flagged). The asked-for card need not be in the deck (`in_deck` says); an unknown name is a 404 with near names.
- **The format.** `format` is validated against the Vault's formats. The design said a saved deck stores no format; it now can, so the default is the format set on the deck, else `commander` (`format_from` says which).
- **Rate limit.** 120 reads a minute per person across both routes (each lane page recomputes the allocation).
- **Measured** (`test_the_first_ideas_page_of_a_100_card_deck_makes_a_small_constant_number_of_queries`, a 100-card deck beside 12 other saved decks, local Postgres): the first `ideas` page makes 11 queries and `alternatives` 16, and the query count does not grow with the deck.

## Decisions for the owner

1. **A separate view named "Ideas", not part of the Lab.** Recommendation: yes. Alternative: a Lab section (shorter navigation, but the Lab is for decisions and this is for exploring).
2. **Read-only in v1**, with swaps applied through the deck page or the assistant. Recommendation: yes.
3. **Phase 1 on the coarse roles, finer roles and the commander or theme start in phase 2.** Recommendation: yes, so it ships before #174.
4. **Pick the most recently edited deck by default**, with the list of decks as the start screen. Recommendation: the list as the start (mockup 1), so nothing is chosen for the person.
5. **Remove the cytoscape script** with the old graph. Recommendation: yes.

## Tasks that follow (under #163)

0. Web (deck page): the cut and add change flow with `validate_deck_changes` and a confirmation before `update_deck` (needed by Swap into the deck; it does not exist today).
1. ~~Server: `GET /decks/{id}/ideas` and `/ideas/alternatives` (with the validated `format` input), tools `get_deck_ideas` and `get_card_alternatives`, tests above~~ Done ("As built"); alternatives run on the coarse roles behind `deck_ideas.equivalence`, which #166's `equivalents` replaces.
2. Web: the Ideas view with the five states, Clear, Esc and Back, lazy images and windowed lanes, the performance test.
3. Remove the Graph modes, `graph.jsx`, the cytoscape script and the dead styles; redirect `#/graph`.
4. Links: Lab Buy rows and deck-page missing rows to Ideas.
5. Docs: `docs/api.md`, `docs/ai-parity.md`, `public/llms.txt`, skills.
6. Phase 2 after #174: finer roles, commander and theme start.
