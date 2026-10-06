# The Lab: decide what to buy, sell, trade or keep (design for #162, epic #158)

Status: design for owner review. Nothing here is built; implementation is #164.

Owner direction (2026-10-05, `docs/graph-and-lab-review.md`): **the Lab helps with decisions about buying and selling.** Decks are a view; what matters is whether each deck can stand on its own, and whether a card should move or be bought. This page turns that into sections, data, wireframes and tests.

## What the Lab is today (checked in the code, 2026-10-06)

`public/views/lab.jsx` is labelled "Lab" in the navigation but headed "Insights" on the page, and shows: profit and loss winners and losers, biggest stockpiles, spend by month, colour, type and mana-value breakdowns, and a type's priciest cards. Its data comes from `GET /collection/stats`, `/valuation`, `/breakdowns` and `/names`. The review (#159) found most of it is composition numbers that the Vault overview and the assistant tools already give, and none of it says what to do. No production screenshots exist (#175), so the wireframes below are designs, not captures of today's page.

## The test every section must pass

Each section names (1) the decision it supports, (2) where the person acts on it, and (3) what the server computes. A section that cannot name all three is cut. Every number comes from the server (no arithmetic in the browser beyond formatting), and every list is paged so no response grows with the collection.

## Sections

### 0. The decision strip (top of the page)

Three server-computed counters, each a link that scrolls to its section: **decks that need a purchase** (and what finishing them costs, counted once per #165), **cards you could sell** (spare copies, with their market value), **biggest known loss and gain**. Each counter is the `total` or `value` field of the section response below, never a separate calculation. Phone: three stacked rows. If a section has nothing to say, its counter reads "nothing to decide" instead of 0.

### 1. Buy: what to buy to make every deck stand on its own

- **Decision:** buy a copy or move one from another deck (#165).
- **Data:** `GET /decks/overlap` as extended in `docs/deck-independence.md`: per deck `stands_alone`, `lacking`, `cost_to_complete`, per contested card the `move` and `buy` options, and the global cost counted once. The Lab shows contested cards first (the decision), then decks that lack cards that are simply not owned.
- **Acts on it:** a deck row links to the deck page and its buy list; each card links to the card; **Copy shopping list** uses the existing `shopping_list` text (the Vault does not contact stores; prices are Scryfall's cheapest, dated).
- **Owner question inside it:** none new; the allocation rule and its override live in #165.
- **Empty states:** no decks saved: "Save a deck to see whether your decks can all be built at once", with the import-a-deck action. Decks that all stand alone: one line, "All N decks can be built at the same time from what you own."

### 2. Sell or hold: what to let go

Two lists, because they answer different questions.

**2a. Spare copies (new).** Copies beyond what the saved decks need, counted by card name as everywhere else:

- Per name: `have` (all copies), `needed` (the most the saved decks need at the same time, so it equals `need_for_all` from #165), `spare = have - needed`, `market_value_of_spare`, whether Dragon Shield already marks copies for trade (`trade_quantity`), and the decks that use the card.
- Cards in no saved deck are all spare, **but only when at least one deck is saved**; with no decks the section asks the person to save decks first instead of calling the whole collection spare.
- Basic lands are left out (as in #165). It says what it does not know: copies for decks the person has not saved, and play-sets, so "spare" means "beyond your saved decks", never "worthless to you".
- Sorted by market value of the spare copies, descending; cursor-paged.
- **Acts on it:** the card page, and a link to the card's Scryfall page (which lists stores; the Vault does not recommend a seller or contact one). The collection export (`/collection/export.csv`, which keeps Dragon Shield's trade quantity column) is linked once at the top; the Vault has no separate trade-list export today.

**2b. Profit and loss.** The existing biggest gains and biggest losses (`GET /collection/stats`), over copies with a known price paid only, with the count of copies it covers ("based on 21,745 of 21,950 copies"). Decision: sell a winner, or hold a loser until it recovers. Each row links to the card. Hidden entirely, with a one-line reason, when the collection has no known prices paid or is a shared view that hides costs.

### 3. Value over time

- **Decision:** whether a sale now is a good moment, and whether the collection's value is tracking what was paid.
- **Data:** `GET /collection/history` (daily market value and cost). One chart, the last year by default, with the dates it covers; a note that history starts at the first price refresh, not at the first import.
- It sits last: it supports the other sections, it does not need action of its own.

### 4. Cut: the colour by type heatmap

The review kept this only "if it helps a buy or sell decision". It does not: knowing there is more blue than red does not say what to buy or sell, and the Vault overview and `get_collection_breakdowns` already give the numbers. **Cut.** The colour, type and mana-value breakdowns, spend by month, stockpiles (replaced by 2a, which is stockpiles measured against the decks) and a type's priciest cards (replaced by 2a sorted by value) go with it.

## Wireframes

Both widths show the same sections in the same order of decision urgency. `[...]` is a control or link.

### 1400 px

```
Lab                                          Prices: Scryfall, 6 Oct   [Export collection]
What should I buy, sell or keep?
+--------------------------+--------------------------+--------------------------+
| 2 decks need a purchase  | 14 cards you could sell  | Biggest known loss -$310 |
| finish all: $6.60        | spare value $842         | biggest gain +$1,204     |
+--------------------------+--------------------------+--------------------------+

BUY: can every deck stand on its own?          SELL OR HOLD
+---------------------------------------+      Spare copies (beyond your saved decks)
| The World Tree   have 2, 3 decks need |      Name              spare  value   in decks
|  [move from X]  [buy 1 for $6.60]     |      Sol Ring            27   $135    4 decks
+---------------------------------------+      Cavern of Souls      4    $120    4 decks
| Avatar Aang       complete            |      ...                                [more]
| Sliver Swarm      lacks 1   $6.60     |
| The dragon ...    complete            |      Profit and loss (21,745 of 21,950 copies)
| [Copy shopping list]                  |      Winners            paid   now     gain
+---------------------------------------+      ...                                [more]
                                               Losers  ...
VALUE OVER TIME  (1 Jan - 6 Oct)
[ line chart: market value, cost ]
```

### 390 px

```
Lab                      [Export]
Prices: Scryfall, 6 Oct

+----------------------------+
| 2 decks need a purchase    |
| finish all: $6.60        > |
+----------------------------+
| 14 cards you could sell  > |
+----------------------------+
| Loss -$310 / gain +$1,204>|
+----------------------------+

BUY
The World Tree  (3 decks need 1, you own 2)
 [move]   [buy 1 for $6.60]
> Avatar Aang       complete
> Sliver Swarm      lacks 1
[Copy shopping list]

SELL OR HOLD
Spare copies          (tap a row)
Sol Ring        27 spare  $135
...                      [more]
Winners | Losers  (tabs)

VALUE OVER TIME
[ chart ]
```

### States (each has its own mockup in the implementation)

- **Empty collection:** the page is one panel: "Import your collection to see what to buy or sell", with the import action.
- **No decks:** Buy is replaced by "Save a deck" and spare copies are hidden (see 2a); profit and loss and value over time still show.
- **No prices paid, or costs hidden on a shared view:** profit and loss is replaced by one line saying why; the counter shows "nothing to decide"; spare copies, Buy and value over time still show (value over time shows market only).
- **Stale prices:** the header says how old the prices are; a dated price is never shown as today's.

## API and assistant parity

| Section | REST | Assistant tool |
|---|---|---|
| Buy | `GET /decks/overlap` (extended, #165) | `get_deck_overlap` (extended, #165), `shopping_list` |
| Spare copies | **new** `GET /collection/spare` (cursor-paged, HAL `_links`, response model, rate limit, 404 for another person's data) | **new** `list_spare_copies` (read-only; returns prices, so classified Scryfall data and carries provenance) |
| Profit and loss | `GET /collection/stats` (existing) | `get_collection_stats` (existing) |
| Value over time | `GET /collection/history` (existing) | `get_value_history` (existing) |

`docs/ai-parity.md`, `public/llms.txt` and the skills that explain selling are updated with the new tool. The "decision strip" is not a separate endpoint: each counter reads the `total` or `value` field of its section's response.

## Tests (write first)

- `spare` is `have - need_for_all` by name; basic lands never appear; a card used by two decks needing 1 each with 3 owned has spare 1.
- With no saved decks the endpoint answers "no decks" and lists nothing; it never reports the whole collection as spare.
- Cards in no deck are fully spare once a deck exists.
- `trade_quantity` is reported without changing `spare`.
- Paging: page size never changes who is spare; sorted by market value of the spare copies.
- Cross-tenant: another person's decks and collection never appear; a shared view with costs hidden shows no paid or profit fields.
- The Lab page renders every state above (empty, no decks, no prices paid, hidden costs, stale prices) at 1400 and 390 px, with no horizontal scroll at 390.
- The old sections (spend by month, colour, type and curve breakdowns, type priciest cards, stockpiles) are gone and nothing links to them.
- Every counter equals the matching field in its section's response (a test compares the two).
- Real data: the owner's four decks give the contested card and the cost in #165, and a spare list whose first rows are cards owned in far greater numbers than the decks need.

## Decisions for the owner

1. **The name.** The navigation says "Lab" but the page heading and its screen label say "Insights". Recommendation: make the page say **Lab** too.
2. **Spare means beyond saved decks.** Recommendation: yes, labelled as such, because the Vault cannot know decks that are not saved. Alternative: also hold back a play-set of four for every card in a deck (rejected for most formats: it would hide real spares).
3. **Cut the colour by type heatmap** and the other composition views (they move nowhere). Recommendation: cut.
4. **Selling links.** Recommendation: link to the card's Scryfall page only, and never name or contact a seller, as with the shopping list.

## Tasks that follow (under #164)

1. Server: `GET /collection/spare` and `list_spare_copies`, tests above, docs.
2. Server: the extended `GET /decks/overlap` (#165), if not already done.
3. Web: the new Lab page (sections 0 to 3) with every state; remove the old sections; change the page heading and screen label to "Lab" if decision 1 is yes.
4. Docs: `docs/api.md`, `docs/ai-parity.md`, `public/llms.txt`, skills.
5. Production visual pass (#175) after it ships, at 1400 and 390 px.
