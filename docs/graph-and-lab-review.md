# The Graph and the Lab: what they are for (review for #159)

Status: owner decisions of 2026-10-05 are applied. The designs that follow are #161 (deck ideas lab) and #162 (Lab).

## The test applied to every view

Which question does a collector or player answer here that they cannot answer faster elsewhere in the Vault?

## Evidence, and what could not be checked

- **Production data (checked).** The owner's real collection, read through the Vault's own tools: 7,136 card names, 21,950 copies, 10,645 printings, 269 sets, about $30.2k market value, profit and loss known on 21,745 copies, card data complete (no unknown colour, type or mana value), four saved Commander decks of about 100 cards each.
- **Code (checked).** `public/views/graph.jsx` (seven modes), `public/views/lab.jsx`, `public/views/browse.jsx`, `public/views/dashboard.jsx`, `docs/ai-parity.md` and the MCP tool list in `vault/api/mcp.py`. Every claim below about what a view does cites the code.
- **UI on screen (partly checked).** Each view was run in a browser at 1400 px and 390 px against local test data. Those screenshots were not saved, so this document does not link them. **The production UI at https://mtgvault.cards could not be opened from the browser available here (the network proxy blocks it), so no production screenshots exist.** A production visual pass at both widths is tracked as its own task (see the task map).

An earlier suspicion that missing card data explained the broken Deck map was wrong: production data is complete. The Deck map is broken because it cannot be reset once a deck is loaded and shows less than the deck page (#160, superseded).

## The Graph today: seven modes

Each row: the job, the overlap with something else, the verdict, and how an AI assistant gets the same information today. "Numbers" means the underlying data is available through a tool; "picture" means the visualization itself is not.

| Mode | Job | Overlap | Verdict | AI access today |
|---|---|---|---|---|
| Colour galaxy | Where my value sits by colour | Lab colour breakdown, Vault overview | Cut | Numbers: `get_collection_breakdowns` (colour). Picture: none |
| Type roster | Value and count by type, top 4 names per type | Lab "What's in your library" | Cut | Numbers: `get_collection_breakdowns` (type, colour by type), `list_card_names` (top names). Picture: none |
| Set clusters | Which sets hold my value | Sets view, "Top sets" | Cut | Numbers: `list_sets` (sort by value). Picture: none |
| Hierarchy (colour by type) | Where value pockets are | Server `breakdowns.matrix` (whole collection) | Cut as a graph; keep a colour by type heatmap in the Lab only if it helps a buy or sell decision | Numbers: `get_collection_breakdowns` (the matrix). Picture: none |
| Affinity web | Cards sharing set, type, colour, mana value, price, rarity | none | Cut: these similarities mean nothing to a player and are not synergy | None, and none needed |
| Mana / price | Mana value against unit price | Not Browse: Browse has no mana value filter and sorts by total holding value (`browse.jsx`), so this comparison is **removed, not moved** | Cut | Numbers: `list_card_names` (mana value and unit price per name). Picture: none |
| Deck map | Owned versus missing for a loaded deck | Deck page (coverage, cost to finish, buy list) | Cut: cannot be reset, shows less than the deck page | `get_deck_overlap`, `check_decklist`, `get_deck` (`docs/ai-parity.md` marks the Graph "partly: the clusters themselves stay visual") |

**Finding.** The seven modes are seven ways to draw numbers that the server already returns and that assistants can already read. What the Graph adds is the picture, and none of the pictures leads to a decision. Coverage detail: Set clusters load every set; Type roster and Hierarchy use whole-collection totals from the server; the cards drawn as dots or listed as top names are a sample (the depth control offers 50 to 800 names, most valuable first, out of 7,136). So the totals are complete and the card-level detail is partial; the cut does not rest on the graph "only showing 50 to 400 cards", which was an earlier, incorrect claim.

## The Lab today

| Section | Job | Overlap | Verdict | AI access today |
|---|---|---|---|---|
| Profit and loss winners / losers | What gained or lost against what I paid | none | Keep, as a sell / hold decision (link to the card, its decks and the export) | Numbers: `get_collection_stats` (biggest gains and losses), `get_collection_summary` (profit and loss) |
| Biggest stockpiles | Where I have the most copies | none | Keep and sharpen into spare copies: counted against what the saved decks need, not a playset of 4 | Numbers: `get_collection_stats` (most copies); spare-copy counts need #165 |
| Spend by month | When I paid | The Vault's acquisition timeline plots **copies acquired**, not money paid, so it does not duplicate this chart | Cut as a separate chart (owner decision); amount paid by month remains available as numbers. Where it appears next, if anywhere, is a question for #162 | Numbers: `get_valuation` (paid by month), `get_acquisition_timeline` (copies) |
| Colour, type and curve breakdowns | Composition | Graph modes and the Vault overview | Cut, except an optional colour by type heatmap | Numbers: `get_collection_breakdowns` |
| Type's priciest cards | Top cards per type | Browse sorted by value, with a type filter if one exists | Cut, or link to Browse with the filter | Numbers: `search_cards` and `list_card_names` sorted by value |

## Owner decisions (2026-10-05)

1. Decks are a view. What matters is whether each deck can stand on its own with the collection you have, or borrows a card from another deck. That decides whether to move a card or buy one.
2. The Lab is for decisions about buying and selling.
3. The Graph becomes a deck ideas lab (part of the Lab or its own view): how a deck could be built from the collection, what is missing, and for each missing card which owned card can stand in or achieve the same dynamic (treasure generation, token doubling, card draw, a counterspell).
4. The seven current Graph modes are cut.

## What each view becomes

**Lab: decide what to buy, sell, trade or keep.**
- Profit and loss as a sell / hold list.
- Deck independence: for each deck, the cards it borrows from another deck and the cards that are free (design and API: #165, which extends `get_deck_overlap`).
- Spare copies: copies beyond what the saved decks need.
- Value over time, from `/collection/history`.

**Deck ideas lab (replaces the Graph): see what you can build from what you own.**
- Start from a deck or a commander.
- Show what the collection covers, what is missing and what the missing cards cost.
- For each missing card, offer owned cards that do the same job (design: #161, data: #166).

## The data this needs: card roles

"Does anything I own do the same job" needs to know what a card does. The Vault already loads Scryfall's community oracle tags daily (`CATALOG_SOURCES` includes `oracle_tags`), which describe functions such as ramp, card advantage, repeatable counterspell and unique doubler, with a weight per tagging and a hierarchy.

Owner direction: build a Vault-owned layer of card roles that anyone can read and only the Vault's pipelines write and review. User experience can come in as suggestions that a pipeline reviews, never as direct edits.

Constraints, recorded for #166 and the compliance gate (#62):
- Scryfall's terms forbid simply republishing or proxying its data, so the layer must be the Vault's own vocabulary and mappings, with the Scryfall tag as a labelled input, not a mirror.
- Tags that an AI adds are labelled as such (#126).
- Scryfall tags are keyed by their stable `id`, never by slug or label, and each can be switched off.

## Task map

- #158 epic. #159 this review.
- #161 design the deck ideas lab, #163 implement.
- #162 design the Lab, #164 implement.
- #165 deck independence (borrowed cards): design and API.
- #166 functional equivalents: roles vocabulary, data sources, terms check.
- #160 (Deck map bug) is superseded: the mode is removed, and "reset the view" is a requirement of #161.
- #175: a production visual pass of the current Graph and Lab at 1400 px and 390 px (screenshots saved with the review), because the review environment could not open mtgvault.cards.
