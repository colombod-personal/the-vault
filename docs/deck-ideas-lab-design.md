# The deck ideas lab: what can I build, what is missing, what can stand in (design for #161, epic #158)

Status: design for owner review. Nothing here is built; implementation is #163.

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
- **Back to them:** an owned alternative links to the card; **Swap into the deck** opens the deck page's change flow; a missing card's **Buy** links to the deck's buy list and the card's Scryfall page. The lab never reimplements those screens.
- **Read-only in v1.** The lab proposes. Applying a swap goes through the existing `validate_deck_changes` then `update_deck` (write scope, asks first), on the deck page or through the assistant.

## The flow

1. **Pick a deck.** v1 starts from a saved deck (default: the most recently edited) or an Archidekt link (read-only, the Vault's existing link import). Starting from a commander or a theme (tokens, treasure) needs the finer roles and is phase 2 (below).
2. **See what the collection covers.** The deck's cards in role lanes (ramp, draw, removal, and so on), not by colour. Each card is a node: owned, partly owned, borrowed by another deck (#165), or missing. A header says "92 of 100 cards covered; 8 missing; 3 borrowed". Lines join cards that form a combo you already own (from `find_combos`).
3. **Select a missing card.** A panel lists owned alternatives, best first, each with the card image, mana cost, copies owned, **why it matches** ("both: treasure generation, core") and a flag when it is borrowed or outside the deck's colour identity or format (those are filtered out before ranking, per #166).
4. **Decide.** Use the alternative (**Swap into the deck**), move a copy from another deck (offered only when #165 says a donor copy exists), or buy (cheapest known price, dated, with a link).
5. **Always resettable.** A visible **Clear** control returns to the start; Esc does the same; every selection is a history entry (`#/ideas/{deck}/{card}`), so Back undoes one step. This is the bug in #160, and it is a requirement.

## Mockups

`[...]` is a control. Card art is cut by the circle mask and credited (artist and Scryfall) in the card panel.

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
Cloudstone Curio   role: token doubling (core)
You own nothing else that does this job in this deck's colours and format.
[Buy for $1.20, Scryfall, 6 Oct]   [See upgrades on the deck page]   [Back]
```

### 5. A fully covered deck

```
Avatar Aang   100 of 100 covered | 0 missing | 0 borrowed        [Clear]
Every card is in your collection and no copy is borrowed.
Nothing to decide here. [Open the deck page]  [See upgrades]
(the role lanes stay visible so the deck can still be explored)
```

## The data (every number from the server)

- **`GET /decks/{id}/ideas`**: the deck's cards grouped by role lane with `have`, `need`, `status` (`owned`, `partial`, `borrowed`, `missing`), counts for the header, and the combos already owned as edges. Lanes are cursor-paged (`limit`, `cursor`, HAL `next`) so no response grows with the deck; the header counts are computed over the whole deck first.
- **`GET /decks/{id}/ideas/alternatives?card=NAME`** (cursor-paged): owned alternatives for a missing card, from `equivalents(card, deck_id)` in `docs/card-roles-design.md`: shared `core` role, colour identity and format applied as filters before ranking, owned first, then mana value difference. Each row: card, copies owned, `borrowed_from` (a deck name, from #165), `why` (the shared roles and their strength), `legal` and `in_colours`, price and its date for a card that would have to be bought, and the `move` option when #165 offers one.
- **Assistant tools (parity):** `get_deck_ideas` and `get_card_alternatives`, read-only; they return prices, so they are classified as Scryfall data with provenance and the Fan Content notice. A test fails if a tool that returns prices is classified own-data-only.
- **Tenancy and sharing:** own account only. A shared collection has no decks of the owner's to explore, so both routes answer 404 under `/shared/{id}/...` (decks and collections are separate share kinds). Archidekt-link decks are read from the public link and never stored (the Vault only reads them).
- **Positions:** the lanes are a deterministic grid computed in the browser from the server's order. There is no physics layout.

## Phasing, so it ships before the finer roles exist

- **Phase 1 (on today's eight coarse roles):** pick a saved deck or link, the lanes, missing and borrowed status, alternatives from the coarse roles, the decide actions, Clear and Back. The panel says "coarse roles" so a weak match is not oversold.
- **Phase 2 (after #174):** the finer roles replace the coarse ones with no change to the response shape; start from a commander or a theme ("what could I build from what I own", which needs a server operation over the collection's roles and colour identity).

## Performance budget

For a 100-card Commander deck and the owner's real collection (7,136 card names, four saved decks):

- **Server:** `ideas` first page under 400 ms at the 95th percentile on production; `alternatives` under 300 ms.
- **Payload:** the first `ideas` page under 60 KB gzipped (no images in the payload; images load lazily, small size, only for the lane in view).
- **First render under 2 s on a mid-range phone**, measured with a throttled run (4x CPU slowdown, a 4G network profile) in a browser test that opens the deck and asserts the lanes are drawn and the main thread has no task over 200 ms.
- **No frozen tab:** no force simulation; at most a few dozen combo lines; lanes with more than 40 cards render windowed. The cytoscape script (loaded from unpkg today) is removed with the old graph, which also removes a third-party load from every page.

## What is removed

All seven Graph modes and `public/views/graph.jsx`, the `cytoscape` script tag in `public/index.html`, the Graph's navigation entry (replaced by Ideas) and any style used only by it. A test fails if any of them remains or if a link still points at a removed mode.

## Tests (write first)

- The five states above render at 1400 and 390 px (no horizontal scroll at 390).
- Clear, Esc and Back each return to the start or the previous selection and leave no stale selection.
- A missing card with a shared core role lists the owned card; one outside the colour identity or format never appears; with none, the empty message shows.
- A card held by another deck shows as borrowed with that deck's name, and `move` appears only when #165 says a donor copy exists.
- Header counts equal the sum over all lanes whatever the page size; lanes page independently.
- Own account only: 404 under `/shared/...`, no other person's decks.
- Provenance: both tools carry Scryfall provenance; prices carry their date.
- Performance: the budget above is a test with a recorded result, run on a 100-card deck.
- `#/graph` redirects to `#/ideas`; the removed code is gone.

## Decisions for the owner

1. **A separate view named "Ideas", not part of the Lab.** Recommendation: yes. Alternative: a Lab section (shorter navigation, but the Lab is for decisions and this is for exploring).
2. **Read-only in v1**, with swaps applied through the deck page or the assistant. Recommendation: yes.
3. **Phase 1 on the coarse roles, finer roles and the commander or theme start in phase 2.** Recommendation: yes, so it ships before #174.
4. **Pick the most recently edited deck by default**, with the list of decks as the start screen. Recommendation: the list as the start (mockup 1), so nothing is chosen for the person.
5. **Remove the cytoscape script** with the old graph. Recommendation: yes.

## Tasks that follow (under #163)

1. Server: `GET /decks/{id}/ideas` and `/ideas/alternatives`, tools `get_deck_ideas` and `get_card_alternatives`, tests above (alternatives use `equivalents` from #166; on the coarse roles for phase 1).
2. Web: the Ideas view with the five states, Clear, Esc and Back, lazy images and windowed lanes, the performance test.
3. Remove the Graph modes, `graph.jsx`, the cytoscape script and the dead styles; redirect `#/graph`.
4. Links: Lab Buy rows and deck-page missing rows to Ideas.
5. Docs: `docs/api.md`, `docs/ai-parity.md`, `public/llms.txt`, skills.
6. Phase 2 after #174: finer roles, commander and theme start.
