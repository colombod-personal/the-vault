# The Graph and the Lab: what they are for (review for #159)

Status: owner decisions of 2026-10-05 are applied. The designs that follow are #161 (deck ideas lab) and #162 (Lab).

## The test applied to every view

Which question does a collector or player answer here that they cannot answer faster elsewhere in the Vault?

## Evidence

- The production collection, read through the Vault's own tools: 7,136 card names, 21,950 copies, 10,645 printings, 269 sets, about $30.2k market value, profit and loss known on 21,745 copies, card data complete (no unknown colour, type or mana value), four saved Commander decks of about 100 cards each.
- The code of `public/views/graph.jsx` (seven modes) and `public/views/lab.jsx`.
- Local browser runs at 1400 px and 390 px.

An earlier suspicion that missing card data explained the broken Deck map was wrong: production data is complete. The Deck map is broken because it cannot be reset once a deck is loaded and shows less than the deck page (#160, superseded).

## The Graph today: seven modes, none decides anything

| Mode | Question it answers | Already answered by | Verdict |
|---|---|---|---|
| Colour galaxy | Where my value sits by colour | Lab colour breakdown, Vault overview | Cut |
| Type roster | Value and count by type | Lab "What's in your library" | Cut |
| Set clusters | Which sets hold my value | Sets view, "Top sets" | Cut |
| Hierarchy (colour by type) | Where the value pockets are | Server `breakdowns.matrix` | Cut as a graph; keep the colour by type heatmap in the Lab only if it helps a buy or sell decision |
| Affinity web | Cards sharing set, type, colour, mana value, price, rarity | nothing | Cut: these similarities mean nothing to a player and are not synergy |
| Mana / price | Expensive cards at each mana value | Browse sorted by value | Cut |
| Deck map | Owned versus missing for a loaded deck | Deck page (coverage, cost to finish, buy list) | Cut: cannot be reset, shows less than the deck page |

Finding: the seven modes are seven ways to draw the same breakdowns. With 7,136 names the graph can only show the top 50 to 400 cards, so it never shows the collection, only a slice.

## The Lab today

| Section | Verdict |
|---|---|
| Profit and loss winners / losers | Keep, as a sell / hold decision (link to the card, the decks it is in, and the export) |
| Biggest stockpiles | Keep and sharpen into spare copies: counted against what the saved decks need, not against a playset of 4 |
| Spend by month | Cut (duplicate of the Vault's acquisition timeline) |
| Colour, type and curve breakdowns | Cut (duplicate), except an optional colour by type heatmap |
| Type's priciest cards | Cut, or link to Browse with the filter |

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
