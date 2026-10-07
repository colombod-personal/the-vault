# The Graph and the Lab: what they are for (review for #159)

Status: owner decisions of 2026-10-05 are applied. The designs that follow are #161 (deck ideas lab) and #162 (Lab). The browser pass at 1400 and 390 px was done on 2026-10-07 and its screenshots are saved next to this file (`docs/screenshots/`).

## The test applied to every view

Which question does a collector or player answer here that they cannot answer faster elsewhere in the Vault?

## Evidence, and what could not be checked

- **Production data (checked).** The owner's real collection, read through the Vault's own tools: 7,136 card names, 21,950 copies, 10,645 printings, 269 sets, about $30.2k market value, profit and loss known on 21,745 copies, card data complete (no unknown colour, type or mana value), four saved Commander decks of about 100 cards each.
- **Code (checked).** `public/views/graph.jsx` (seven modes), `public/views/lab.jsx`, `public/views/browse.jsx`, `public/views/dashboard.jsx`, `docs/ai-parity.md` and the MCP tool list in `vault/api/mcp.py`. Every claim below about what a view does cites the code.
- **UI on screen (checked, 2026-10-07, screenshots saved).** Every Graph mode and every Lab section, the Decks library, a deck page (Cards and Stats) and Help were run in a real browser (the in-app browser, a local server from this branch) at **1400 x 900 and 390 x 844**, and each row of the two tables below links its two screenshots (`docs/screenshots/`). The data is the Vault's demo account (`vault/reviewer.py`: 110 copies, 42 printings, a 100-card Sliver Commander deck 44% owned and a 60-card Pauper deck fully owned), with **real Scryfall card data and prices** loaded for those cards: the cards by name from Scryfall's API into the catalog, and each owned printing matched to its Scryfall printing and priced from Scryfall's price for it, the same fields the daily sync fills. Two honest limits: the demo account is small (the Hierarchy matrix and the Set clusters are sparse, the Acquisition chart has a single month because the demo copies share one purchase date), and **the production UI at https://mtgvault.cards was still not opened**, so there is no screenshot of the owner's 7,136-name collection in the UI; that pass stays open as #175.
- **Tap targets (measured).** `scripts/measure_tap_targets.js` was run in the browser at 390 px on every view and panel: Vault, Browse, a card's panel, Sets, Decks (both add-a-deck tabs), a saved deck with each of its six tabs, Lab, Graph, Value, Help and the Account panel. Before the fix (the same script, same data) the Decks page had six controls under 36 px (the logo 28.5 px, Import CSV, the account button, Got it and the two "From a link" / "Paste a list" chips, all 34 px), Browse had 38 card names 19.5 px high, and the Graph's seven colour filters were 28 px. After it, every view lists none under 36 px (counts of controls measured: Vault 35, Browse 60, a card's panel 61, Sets 15, Decks 17, a deck 83 with Cards, 20 Stats, 21 Legality, 23 Upgrades, 20 Combos, 21 Buy list, Lab 25, Graph 38, Value 19, Help 10, Account panel 59). `tests/test_tap_targets.py` keeps the CSS from slipping back (it fails on the old 34 px rules).

An earlier suspicion that missing card data explained the broken Deck map was wrong: production data is complete. The Deck map is broken because it cannot be reset once a deck is loaded and shows less than the deck page (#160, superseded).

## The Graph today: seven modes

Each row: the job, the overlap with something else, the verdict, and how an AI assistant gets the same information today. "Numbers" means the underlying data is available through a tool; "picture" means the visualization itself is not.

| Mode | Job | Overlap | Verdict | AI access today | Seen on real data (screenshots) and what they show |
|---|---|---|---|---|---|
| Colour galaxy | Where my value sits by colour | Lab colour breakdown, Vault overview | Cut | Numbers: `get_collection_breakdowns` (colour). Picture: none | [1400](screenshots/graph-color-galaxy-1400.png) · [390](screenshots/graph-color-galaxy-390.png). Five colour anchors with cards orbiting them. Clear and readable at both widths; at 390 px the controls fill the first screen and the canvas starts below it. |
| Type roster | Value and count by type, top 4 names per type | Lab "What's in your library" | Cut | Numbers: `get_collection_breakdowns` (type, colour by type), `list_card_names` (top names). Picture: none | [1400](screenshots/graph-type-roster-1400.png) · [390](screenshots/graph-type-roster-390.png). A ledger: one row per type with value, share and four thumbnails. Works at both widths; it is the one mode that is mostly a table. |
| Set clusters | Which sets hold my value | Sets view, "Top sets" | Cut | Numbers: `list_sets` (sort by value). Picture: none | [1400](screenshots/graph-set-clusters-1400.png) · [390](screenshots/graph-set-clusters-390.png). Opens on a spotlight of one set (here Commander Masters) with its top cards; at 390 px the spotlight fills the stage, so the bubbles that choose the set are not visible without scrolling the stage. |
| Hierarchy (colour by type) | Where value pockets are | Server `breakdowns.matrix` (whole collection) | Cut as a graph; keep a colour by type heatmap in the Lab only if it helps a buy or sell decision | Numbers: `get_collection_breakdowns` (the matrix). Picture: none | [1400](screenshots/graph-hierarchy-1400.png) · [390](screenshots/graph-hierarchy-390.png). A colour by type matrix with value heat. Readable at 1400; at 390 it scrolls sideways inside its panel. **Its caption still says "compound rings = colour, node shape = type"** (the old graph), but the mode now draws a matrix. |
| Affinity web | Cards sharing set, type, colour, mana value, price, rarity | none | Cut: these similarities mean nothing to a player and are not synergy | None, and none needed | [1400](screenshots/graph-affinity-web-1400.png) · [390](screenshots/graph-affinity-web-390.png). A web of cards linked by shared set, type, colour, mana value, price and rarity. Draws fine, and shows why the verdict is Cut: the links say nothing a player can act on. |
| Mana / price | Mana value against unit price | Not Browse: Browse has no mana value filter and sorts by total holding value (`browse.jsx`), so this comparison is **removed, not moved** | Cut | Numbers: `list_card_names` (mana value and unit price per name). Picture: none | [1400](screenshots/graph-mana-price-1400.png) · [390](screenshots/graph-mana-price-390.png). A scatter of mana value against log price. At 390 px the mana axis labels sit under the legend and cannot be read. |
| Deck map | Owned versus missing for a loaded deck | Deck page (coverage, cost to finish, buy list) | Cut: cannot be reset, shows less than the deck page | `get_deck_overlap`, `check_decklist`, `get_deck` (`docs/ai-parity.md` marks the Graph "partly: the clusters themselves stay visual") | [1400](screenshots/graph-deck-map-1400.png) · [390](screenshots/graph-deck-map-390.png). **Broken, as #160 said, at both widths.** With a 100-card deck loaded every card lands in one thin strip inside a single grey compound box; nothing can be read, and there is no way to reset it. |

**Finding.** The seven modes are seven ways to draw numbers that the server already returns and that assistants can already read. What the Graph adds is the picture, and none of the pictures leads to a decision. Coverage detail: Set clusters load every set; Type roster and Hierarchy use whole-collection totals from the server; the cards drawn as dots or listed as top names are a sample (the depth control offers 50 to 800 names, most valuable first, out of 7,136). So the totals are complete and the card-level detail is partial; the cut does not rest on the graph "only showing 50 to 400 cards", which was an earlier, incorrect claim.

## The Lab today

| Section | Job | Overlap | Verdict | AI access today | Seen on real data (screenshots) and what they show |
|---|---|---|---|---|---|
| Profit and loss winners / losers | What gained or lost against what I paid | none | Keep, as a sell / hold decision (link to the card, its decks and the export) | Numbers: `get_collection_stats` (biggest gains and losses), `get_collection_summary` (profit and loss) | [1400](screenshots/lab-profit-loss-1400.png) · [390](screenshots/lab-profit-loss-390.png). Four hero numbers, then winners and losers as a table with spent, now, P&L and %. At 390 px the table scrolls sideways inside its panel with the card name pinned. |
| Biggest stockpiles | Where I have the most copies | none | Keep and sharpen into spare copies: counted against what the saved decks need, not a playset of 4 | Numbers: `get_collection_stats` (most copies); spare-copy counts need #165 | [1400](screenshots/lab-stockpiles-spend-colour-1400.png) · [390](screenshots/lab-stockpiles-spend-390.png). Copies per card (here 40 Mountains first). It counts copies of a name, not copies beyond what the decks need: that is what #165 adds. Reads "1 prints" for one printing (grammar). |
| Spend by month | When I paid | The Vault's acquisition timeline plots **copies acquired**, not money paid, so it does not duplicate this chart | Cut as a separate chart (owner decision); amount paid by month remains available as numbers. Where it appears next, if anywhere, is a question for #162 | Numbers: `get_valuation` (paid by month), `get_acquisition_timeline` (copies) | [1400](screenshots/lab-stockpiles-spend-colour-1400.png) · [390](screenshots/lab-stockpiles-spend-390.png). One bar here, because the demo copies share one purchase date; the chart's shape on a real history is not shown by this data. |
| Colour, type and curve breakdowns | Composition | Graph modes and the Vault overview | Cut, except an optional colour by type heatmap | Numbers: `get_collection_breakdowns` | [1400](screenshots/lab-types-curve-1400.png) · [390](screenshots/lab-colours-types-390.png). Seven colour tiles, a type ledger and the mana value bars; fine at both widths (the tiles wrap four and three at 390). Lands count as mana value 0, which is why 0 holds 48%. |
| Type's priciest cards | Top cards per type | Browse sorts by value but has no type filter (search, set and printing only), so this is **removed, not moved** | Cut. A type-filtered replacement, if wanted, is design work for #162 | Numbers: `list_card_names` sorted by value (it carries the main type); `search_cards` has no type filter | [1400](screenshots/lab-type-expanded-1400.png) · [390](screenshots/lab-type-expanded-curve-390.png). Clicking a type (here Creature) lists its priciest cards, eight at most, with set and price; at both widths it works. |

## What the browser pass found beyond the two tables

Seen on screen, fixed in the same pull request as this review (tests in brackets):

- **Deck page, 390 px: the commander's row hid its "Have" number.** The five colour pips and the type line ("Legendary Creature") did not wrap and drew over the Have column, so the 0 could not be read ([before](screenshots/deck-page-before-fix-390.png), [after](screenshots/deck-page-cards-390.png)). The line now wraps (`public/views/deck.jsx`).
- **Help, 390 px: the section a "?" opened sat under the bottom tab bar.** Following "?" from Decks scrolled the Decks heading to 819 px of an 844 px screen, behind the tab bar. The heading is now scrolled to the top ([help-decks-390](screenshots/help-decks-390.png); `tests/test_help.py::test_a_help_link_scrolls_its_section_heading_to_the_top_not_just_into_view`).
- **Tap targets under 36 px** (see Evidence; `tests/test_tap_targets.py`).
- **Help said things the app does not do** (the Graph being "replaced", "Save deck", a "Value view"); see issue #152's test `tests/test_help.py` (names in curly quotes are checked against the labels in the views).

Seen on screen and **not** fixed here (each is inside a view that is being cut or redesigned, so a fix would be thrown away; they are recorded for #161 and #162): the Deck map (above), the stale Hierarchy caption, the Mana / price axis under the legend at 390 px, the Set clusters spotlight covering the bubbles at 390 px, "1 prints" in the Lab.

## What replaces what is cut (so nothing useful is lost)

**Nothing has been removed from the product.** The cut is a decision (2026-10-05); the code still ships (`public/views/graph.jsx`, the Lab sections), and this review changes none of it. The rule this review proposes is: a view is cut in the pull request that ships what replaces it, never before.

| Cut | What it did that nothing else does today | What replaces it, today | Recommendation (the owner decides) |
|---|---|---|---|
| Colour galaxy, Type roster, Set clusters, Hierarchy | Pictures of totals the server returns | The numbers are available to a person in the Lab (colour, type, curve) and Sets, and to an assistant (`get_collection_breakdowns`, `list_sets`) | Cut with the Graph, as decided. Nothing is lost but the picture. |
| Affinity web | Similarity of cards by set, type, colour, mana value, price, rarity | Nothing, and none is needed (these similarities are not synergy) | Cut as decided. |
| Deck map | Owned versus missing for a loaded deck | The deck page (coverage, cost to finish, buy list); the map itself is broken (screenshots above) | Cut now: it is unusable, and the deck page is the replacement. |
| **Mana / price** | **Compares mana value with price: Browse has no mana value filter or sort** | **Nothing. Only the numbers via `list_card_names`** | **Keep the mode until Browse can filter and sort by mana value (a small API and Browse change), then cut it. If the owner prefers to cut it now, the decision is that this comparison is lost.** |
| **Type's priciest cards** (Lab) | **The dearest cards of one type: Browse has no type filter** | **Nothing in the app. `list_card_names` carries the type but `search_cards` cannot filter by it** | **Keep until Browse gets a type filter (the same change as above), then cut. Until then the Lab section is the only home of this question.** |
| Spend by month | When money was paid | Numbers only (`get_valuation`); the Vault's acquisition timeline counts copies, not money | Owner decision already taken: cut as a chart, the number stays available; where it comes back, if anywhere, is #162. |

The two rows in bold are the ones that lose their only home. The smallest real replacement for both is one change to Browse and `/collection/cards`: a type filter and a mana value filter (and sort). It also gives an assistant the same two answers through `search_cards`. It is not built by this pull request; it is a recommendation for #162 (Lab) to take with its design, and until it ships those two things stay where they are.

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
- Scryfall's API terms say "You may not simply repackage, republish, or proxy Scryfall data. Your software must create additional value for end-users" ([scryfall.com/docs/api](https://scryfall.com/docs/api); recorded with how the Vault meets it in [docs/compliance.md](compliance.md), Scryfall section). So the layer must be the Vault's own vocabulary and mappings, with the Scryfall tag as a labelled input, not a mirror.
- Tags that an AI adds are labelled as such (#126).
- Scryfall tags are keyed by their stable `id`, never by slug or label, and each can be switched off.

## Task map

- #158 epic. #159 this review.
- #161 design the deck ideas lab, #163 implement.
- #162 design the Lab, #164 implement.
- #165 deck independence (borrowed cards): design and API.
- #166 functional equivalents: roles vocabulary, data sources, terms check.
- #160 (Deck map bug) is superseded: the mode is removed, and "reset the view" is a requirement of #161.
- #175: a production visual pass of the current Graph and Lab at 1400 px and 390 px (screenshots saved with the review), because the review environment could not open mtgvault.cards. The pass on local data with real Scryfall cards is done and saved (Evidence); the pass on the owner's real collection in the production UI is what remains of #175.
