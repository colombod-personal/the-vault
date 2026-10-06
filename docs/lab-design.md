# The Lab: decide what to buy, sell, trade or keep (design for #162, epic #158)

Status: design for owner review. Nothing here is built; implementation is #164.

Owner direction (2026-10-05, `docs/graph-and-lab-review.md`): **the Lab helps with decisions about buying and selling.** Decks are a view; what matters is whether each deck can stand on its own, and whether a card should move or be bought. This page turns that into sections, data, wireframes and tests.

## What the Lab is today (checked in the code, 2026-10-06)

`public/views/lab.jsx` is labelled "Lab" in the navigation but headed "Insights" on the page, and shows: profit and loss winners and losers, biggest stockpiles, spend by month, colour, type and mana-value breakdowns, and a type's priciest cards. Its data comes from `GET /collection/stats`, `/valuation`, `/breakdowns` and `/names`. The review (#159) found most of it is composition numbers that the Vault overview and the assistant tools already give, and none of it says what to do. No production screenshots exist (#175), so the wireframes below are designs, not captures of today's page.

## The test every section must pass

Each section names (1) the decision it supports, (2) where the person acts on it, and (3) what the server computes. A section that cannot name all three is cut. Every number comes from the server (no arithmetic in the browser beyond formatting), and every list is paged so no response grows with the collection.

## Sections

### 0. The decision strip (top of the page)

Three server-computed counters, each a link that scrolls to its section: **decks that need a purchase** (and what finishing them costs, counted once per #165), **cards you could sell** (spare copies, with their market value), **biggest known loss and gain**. Each counter reads named fields the server already returns or will return (never a browser calculation): **purchase counter** = `summary.decks_needing_purchase` and `summary.finish_all_cost` (with `summary.unpriced` when a price is unknown) on `GET /decks/overlap`, a `summary` object that #165's extended response gains (decks analysed, decks needing a purchase, the global cost counted once); **sell counter** = `summary.names`, `summary.copies` and `summary.market_value` on `GET /collection/spare`, computed over every spare card, not just the page returned; **profit and loss counter** = new sign-filtered fields on `GET /collection/stats`: `pnl_extremes.biggest_gain` (`pnl.winners[0]`, or `null`) and `pnl_extremes.biggest_loss` (`pnl.losers[0]`, or `null`), plus `pnl_extremes.covered_copies` and `pnl_extremes.total_copies`. The existing `biggest_gains` and `biggest_losses` arrays are only sorted, not sign-filtered, so when every priced holding is profitable `biggest_losses[0]` is still a gain; the counter therefore never reads them directly. A `null` side reads "nothing to decide". Phone: three stacked rows. If a section has nothing to say, its counter reads "nothing to decide" instead of 0.

### 1. Buy: what to buy to make every deck stand on its own

- **Decision:** buy a copy or move one from another deck (#165).
- **Data:** `GET /decks/overlap` as extended in `docs/deck-independence.md`: per deck `stands_alone`, `lacking`, `cost_to_complete`, per contested card the `move` and `buy` options, and the global cost counted once. The Lab lists the cards to buy **cheapest first** (issue #162) from a server-returned, cursor-paged `purchases` list that #165's extended response gains (also added to `docs/deck-independence.md`). Each entry: `card`, `global_deficit`, `unit_price`, `cost` (`global_deficit` times the cheapest known price), `price_status` (`priced` or `unpriced`), `contested` (true when some copies are owned but short) and, for contested cards, the `move` option. It contains both cards the collection owns too few of and cards it does not own at all (the existing `contested` list excludes `have == 0`, so the browser never has to merge lists or deduplicate per-deck `lacking`). Order: cost ascending, ties by card name, unpriced last; a contested card is badged **move possible** and is not placed ahead of a cheaper purchase (owner decision 5 offers contested first).
- **Acts on it:** a deck row links to the deck page and its buy list; each card links to the card; **Copy shopping list** uses the existing `shopping_list` text (the Vault does not contact stores; prices are Scryfall's cheapest, dated).
- **Owner question inside it:** none new; the allocation rule and its override live in #165.
- **Empty states:** no decks saved: "Save a deck to see whether your decks can all be built at once", with the import-a-deck action. Decks that all stand alone: one line, "All N decks can be built at the same time from what you own."

### 2. Sell or hold: what to let go

Two lists, because they answer different questions.

**2a. Spare copies (new).** Copies beyond what the saved decks need, counted by card name as everywhere else:

- Per name: `have` (all copies), `needed` (the total copies the saved decks need at the same time, summed across decks, which is `need_for_all` from #165), `spare = max(0, have - needed)`, `market_value_of_spare`, and the decks that use the card.
- **Which physical copies are spare (deterministic).** Rows and prices are per printing, finish and condition, so the allocation is stated: copies **not** marked for trade in Dragon Shield satisfy the decks' need first, cheapest first (the decks keep the least valuable copies, so the spare value shown is an upper bound on what selling releases, never an overstatement of what is safe to keep); copies marked for trade fill any remaining need last (the marked pool of a row is `min(trade_quantity, quantity)`: an import can store a `trade_quantity` larger than `quantity`, and the allocation clamps it rather than reporting impossible copies), so they are spare whenever the other copies cover the decks. Ties break by printing id so the answer never changes between calls.
- Each name returns its spare **printing rows** (`scryfall_id`, set, finish, condition, `spare_quantity`, unit price, `trade_marked_quantity`) so the person sees exactly which copies, the value is the sum of those rows, and the selling link points at that printing's Scryfall page. The list sort (value of the spare copies) uses those rows.
- Cards in no saved deck are all spare, **but only when at least one deck is saved**; with no decks the section asks the person to save decks first instead of calling the whole collection spare.
- Basic lands are left out (as in #165). It says what it does not know: copies for decks the person has not saved, and play-sets, so "spare" means "beyond your saved decks", never "worthless to you".
- Only names with `spare > 0` are listed, so a card the collection is short on never appears as a negative spare (it belongs to Buy). Sorted by market value of the spare copies, descending; cursor-paged.
- **Acts on it:** the card page, and a link to each spare printing's Scryfall page (which lists stores; the Vault does not recommend a seller or contact one). The collection export (`/collection/export.csv`, which keeps Dragon Shield's trade quantity column) is linked once at the top; the Vault has no separate trade-list export today.

**2b. Profit and loss.** New sign-filtered lists on `GET /collection/stats`: `pnl.winners` (holdings with `gain > 0`, most profitable first) and `pnl.losers` (holdings with `gain < 0`, biggest loss first), each possibly empty, over copies with a known price paid only (the existing `biggest_gains` and `biggest_losses` arrays are sorted but not sign-filtered, so with every holding profitable `biggest_losses` would still show gains; the tabs never use them). `pnl_extremes` is the first entry of each list. An empty tab says "Nothing here: none of your priced holdings are below cost" (or above).  with the count of copies it covers ("based on 21,745 of 21,950 copies"). Decision: sell a winner, or hold a loser until it recovers. Each row links to the card in the Vault and to that printing's Scryfall page, where its current selling value and stores can be inspected (links only, no seller named). Hidden entirely, with a one-line reason, when the collection has no known prices paid.

### 2c. Context for the sell or hold decision: value over time

- **Decision:** whether a sale now is a good moment, and whether the collection's value is tracking what was paid.
- **Data:** `GET /collection/history` (daily market value and cost). One chart, the last year by default, with the dates it covers; history starts at the first import (the import computes that day's value in the same transaction, `test_an_import_starts_the_value_history`); the daily price refresh then adds later days and updates the current one.
- **Server summary.** `GET /collection/history` gains a `summary` over the requested range, computed server-side: `from`, `to`, `market_start`, `market_end`, `market_change`, `cost_end` (`null` when no cost is known) and `below_cost`, defined as `market_end < cost_end` on the **latest day** in the range (`null` when `cost_end` is `null`). The browser never aggregates the paged points to decide.
- **Acts on it:** it is not a standalone section. It is the context for 2a and 2b, with two action targets: a **Show losers** link when the server says the collection is below cost (`summary.below_cost` is true; jumps to the losers list) and a **Show spare copies** link otherwise. If the collection has no known cost, only the market line shows and the action is **Show spare copies** (there is no below-cost state).

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
SELL OR HOLD, CONTEXT: value over time (1 Jan - 6 Oct)   [Show losers]
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

SELL OR HOLD, CONTEXT: value over time   [Show losers]
[ chart ]
```

### States

Each state is shown at both widths. `[...]` is a control.

**Empty collection** (nothing imported): the page is one panel.

```
1400 px                                          390 px
Lab                                              Lab
+----------------------------------------+       +---------------------------+
| Import your collection to see what to  |       | Import your collection to |
| buy or sell.        [Import a file]    |       | see what to buy or sell.  |
+----------------------------------------+       | [Import a file]           |
                                                 +---------------------------+
```

**No decks yet:** Buy asks for a deck; spare copies are hidden (see 2a); profit and loss and the value chart still show.

```
1400 px                                          390 px
Lab                    Prices: Scryfall, 6 Oct   Lab        Prices: Scryfall, 6 Oct
+-----------------+ +-------------+ +--------+  +---------------------------+
| nothing to      | | needs decks | | loss/  |  | Buy: save a deck to see   |
| decide          | | first       | | gain   |  | if your decks can all be  |
+-----------------+ +-------------+ +--------+  | built at once [Add a deck]|
BUY: Save a deck to see whether your decks      | SELL OR HOLD              |
can all be built at once.   [Add a deck]        | Winners | Losers (tabs)   |
SELL OR HOLD: Profit and loss | value chart     | value chart               |
```

**No prices paid** (own account): profit and loss and the chart's cost line are replaced by one line saying why; the chart shows the market line only, and its action is **Show spare copies**.

```
1400 px                                          390 px
Lab                    Prices: Scryfall, 6 Oct   Lab        Prices: Scryfall, 6 Oct
+-----------------+ +-------------+ +--------+  +---------------------------+
| 2 decks need    | | 14 cards    | | nothing|  | 2 decks need a purchase > |
| a purchase      | | you could   | | to     |  | 14 cards you could sell > |
|                 | | sell        | | decide |  | nothing to decide (P&L)   |
+-----------------+ +-------------+ +--------+  +---------------------------+
BUY ...            SELL OR HOLD                 BUY ...
                   Spare copies ...             SELL OR HOLD
                   Profit and loss: "No prices   Spare copies ...
                   paid are known for these      P&L: no prices paid known
                   copies" (market line only)    [value chart: market only]
```

- **Stale prices:** the header says how old the prices are; a dated price is never shown as today's.

## Shared collection views

A shared collection (`/shared/{id}/collection`) grants the viewer the owner's collection, not the owner's saved decks (decks and collections are separate share kinds, `docs/collections.md`, `docs/gdpr.md`). Everything deck-derived is therefore **own-account only**: Buy, spare copies and the purchase and sell counters are never computed or returned for a shared view (no `/shared/...` route for them; asking returns 404, and the page shows no such section), and they must not fall back to computing against the viewer's own decks. The Lab is therefore **own-account only**: a shared collection has no Lab (its navigation entry is not shown), because a viewer cannot act on someone else's collection and every Lab section is either deck-derived or about what the owner paid; the Vault overview and Browse already serve shared views. This changes today's behaviour, where a shared view reuses the Lab with costs hidden (owner decision 7).

## API and assistant parity

| Section | REST | Assistant tool |
|---|---|---|
| Buy | `GET /decks/overlap` (extended, #165) | `get_deck_overlap` (extended, #165), `shopping_list` |
| Spare copies | **new** `GET /collection/spare` (cursor-paged, HAL `_links`, response model, rate limit, 404 for another person's data) | **new** `list_spare_copies` (read-only; returns prices, so classified Scryfall data and carries provenance) |
| Profit and loss | `GET /collection/stats` (existing) | `get_collection_stats` (existing) |
| Value over time | `GET /collection/history` (existing) | `get_value_history` (existing) |

The implementation (task 4) must update `docs/ai-parity.md`, `public/llms.txt` and the skills that explain selling for the new tool; this design makes no parity claim today. The "decision strip" is not a separate endpoint: each counter reads the named fields in section 0.

## Tests (write first)

- `spare` is `max(0, have - need_for_all)` by name and only positive spares are listed: a card owned once and needed by two decks is not listed and no spare is ever negative; basic lands never appear; a card used by two decks needing 1 each with 3 owned has spare 1.
- `summary.names`, `summary.copies` and `summary.market_value` cover every spare card whatever the page size, and `summary.decks_needing_purchase` and `summary.finish_all_cost` on `GET /decks/overlap` match the sum of the per-deck answers (counted once per card).
- With no saved decks the endpoint answers "no decks" and lists nothing; it never reports the whole collection as spare.
- Cards in no deck are fully spare once a deck exists.
- `trade_quantity` is reported without changing `spare`.
- Paging: page size never changes who is spare; sorted by market value of the spare copies.
- Shared views: the spare and overlap endpoints answer 404 under `/shared/{id}/...`, no Lab navigation entry exists on a shared view, and no deck name or deck requirement crosses a collection share (a test shares only the collection and asserts no deck data appears in any response).
- Mixed-price printings: a name with one cheap copy and one expensive foil, needed once, leaves the expensive foil as the spare under the stated rule; trade-marked copies fill the need last; the spare printing rows sum to `market_value_of_spare`; ordering is stable between calls.
- `pnl_extremes`: when every priced holding is profitable `biggest_loss` is `null` (and vice versa); the counter then reads "nothing to decide".
- Buy ordering: cheapest purchase first, unpriced last, ties by name; a contested card with a `move` option is not placed ahead of a cheaper purchase.
- Cross-tenant: another person's decks and collection never appear; `GET /collection/stats` and the history endpoint never return paid or profit fields to a shared viewer whose owner hides costs.
- The Lab page renders every state above (empty, no decks, no prices paid, stale prices) at 1400 and 390 px, with no horizontal scroll at 390.
- The old sections (spend by month, colour, type and curve breakdowns, type priciest cards, stockpiles) are gone and nothing links to them.
- Every counter equals the matching field in its section's response (a test compares the two).
- Real data: the owner's four decks give the contested card and the cost in #165, and a spare list whose first rows are cards owned in far greater numbers than the decks need.

## Decisions for the owner

1. **The name.** The navigation says "Lab" but the page heading and its screen label say "Insights". Recommendation: make the page say **Lab** too.
2. **Spare means beyond saved decks.** Recommendation: yes, labelled as such, because the Vault cannot know decks that are not saved. Alternative: also hold back a play-set of four for every card in a deck (rejected for most formats: it would hide real spares).
3. **Cut the colour by type heatmap** and the other composition views (they move nowhere). Recommendation: cut.
4. **Selling links.** Recommendation: link to the printing's Scryfall page only, and never name or contact a seller, as with the shopping list.
5. **Buy ordering.** Recommendation: cheapest purchase first (the issue's wording), with contested cards badged. Alternative: contested cards first, because they are the decision between moving and buying.
6. **Which copies count as spare.** Recommendation: the decks keep the cheapest copies and trade-marked copies fill the need last, so spare value is an upper bound on what selling releases. Alternative: keep the most valuable copies, which understates what can be sold.
7. **No Lab on shared views.** Recommendation: yes (see "Shared collection views"); the Vault overview and Browse serve shared collections. Alternative: keep a reduced Lab with the market-value chart only.

## Tasks that follow (under #164)

1. Server: `GET /collection/spare` and `list_spare_copies`, tests above, docs.
2. Server: the extended `GET /decks/overlap` (#165), if not already done.
3. Web: the new Lab page (sections 0, 1, 2a, 2b and 2c) with every state; remove the old sections; change the page heading and screen label to "Lab" if decision 1 is yes.
4. Docs: `docs/api.md`, `docs/ai-parity.md`, `public/llms.txt`, skills.
5. Production visual pass (#175) after it ships, at 1400 and 390 px.
