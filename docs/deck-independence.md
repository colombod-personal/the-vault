# Deck independence: can each deck stand on its own? (design for #165)

Status: design for owner review. Nothing here is built.

## The question

Owner request (2026-10-05): "see if the decks are able to stand on their own, or if something in a deck must be borrowed from another deck. This helps decide whether to move a card or buy a new one."

For the saved decks: can they all be built **at the same time** from the copies owned? If not, which cards are contested, which deck would lose each one, and what does a copy cost to end the borrowing.

## What the owner's data says today (2026-10-06, via `get_deck_overlap`)

- 4 saved decks (Avatar Aang, Nazgûl, Sliver Swarm tuned with rage, The dragon in the night).
- 28 cards appear in more than one deck. **27 of them are not contested**: the collection holds at least as many copies as all the decks need together (Sol Ring: needed by 4 decks, owned 31; Cavern of Souls: needed 4, owned 8; Leyline of the Guildpact: needed 3, owned exactly 3).
- **1 is contested: The World Tree.** Three decks (Avatar Aang, Sliver Swarm, The dragon in the night) each need 1; the collection holds 2. One of the three decks has to go without it, or a third copy is bought (Scryfall's cheapest price today: $6.60).

So the useful answer is narrow and specific, and it is easy to lose in a long list. The design therefore reports only **contested** cards as a borrowing question, and says plainly when nothing is contested.

## Definitions

Counted by card name (basic lands left out), the same way `get_deck_overlap` and the deck coverage already count: any printing owned counts.

For each card in a deck, with `need` copies in that deck, and `need_for_all` and `have` for the whole set of saved decks:
- **free**: the collection holds enough copies for every deck that uses the card (`have >= need_for_all`). Most shared cards are this.
- **contested**: `0 < have < need_for_all`: some copies exist and some deck has to go without. The allocation below decides how many copies each deck is given (`gets`). A card owned zero times (`have = 0`) is not contested: there is no copy to give or move, so every deck that needs it simply lacks it (`not_owned`).
- A deck **holds** `gets` copies of a contested card whenever `gets` is above zero (another deck wants those copies too), and **lacks** `lacking = need - gets` copies when that is above zero. Both can be true of the same card: with A and B each needing 2 and 3 owned, A holds 2, B holds 1 and lacks 1. Each lacking copy is one of two kinds, defined against this deck's own demand and the total owned: `not_owned` copies are those the collection could not supply even if this deck were given every owned copy (`max(0, need - have)`), so they must be bought whatever the other decks do; `held_by_other_deck` copies are the rest (`lacking - not_owned`): they exist in the collection but were given to another deck, so they are available by moving. A mixed shortage is split, not labelled with one reason.

A deck is **complete** when it lacks nothing under the allocation (`stands_alone` is true), and **independent** when it also holds no contested card, so that no other deck's completeness depends on it.

The **global deficit** of a contested card (or of a card owned zero times, where it is the whole demand) is `need_for_all - have`. Under the allocation the decks' `lacking` copies of that card add up to exactly the global deficit, whatever the order: the order only decides *which* deck lacks. So the cost to finish every deck is the global deficit times the price, counted once, and the per-deck costs below are shares of it and must never be added to a separate total.

## The allocation rule (who gets a contested copy)

A contested card has fewer copies than the decks want. Any rule is a choice, so the rule is stated, deterministic and can be overridden:

1. **Default: finish the deck that is closest to complete.** Decks are ordered by how many cards they still miss overall (fewest first, then most recently edited, then name). Walking that order, each deck takes the copies it needs while any remain. The reasoning: a copy does the most good in the deck that is nearest to being playable.
2. **Override:** the request can pass `priority=<deck ids, in order>` (the web view lets the person drag decks), and the answer uses that order. The priority is not stored in v1.
3. The answer always says which rule was used.

Limits to state in the UI: it counts copies, not play (a deck the person does not play counts as much as one they do), and it is by name, not by printing.

## The answer, per deck

```
deck: id, name
need            cards the deck needs (basics left out)
free            how many of those are not contested
holds           [{ card, quantity (= gets, any positive number), also_wanted_by: [deck names] }]
lacking         [{ card, quantity, not_owned, held_by_other_deck, price }]   (quantity = not_owned + held_by_other_deck)
stands_alone    true when lacking is empty (the deck is complete under the allocation)
independent     true when lacking and holds are both empty
independence    free / need, rounded, for sorting only; a deck with nothing to count (need is 0, for example a deck of only basic lands) scores 1.0 and is complete and independent, so there is no division by zero
cost_to_complete   the cheapest known prices of the lacking copies, `unpriced` counted: what buying finishes this deck, with the others keeping theirs
```

and per **contested card**, once, because that is the decision:

```
card, have, need_for_all, global_deficit
decks: [{ deck, need, gets: N, lacking: N }]
options:
  - move:  "give the copy to <deck>; <other deck> then lacks one" (what changes; the global deficit does not). Offered only when the target deck has `held_by_other_deck > 0` and the source deck holds a positive allocation of the card; otherwise only `buy` is offered, in the response and in the view. For a contested card (`have > 0`) both conditions hold, so both options appear; a card owned zero times is not contested and never offers `move`.
  - buy:   "buy global_deficit copies at $X (Scryfall's cheapest price, dated)" (the cost to finish every deck for this card, counted once)
```

Worked examples (they become tests): three decks each need 1, one owned: global deficit 2; deck A (first in the order) gets it, B and C lack 1 each (`held_by_other_deck`); buying 1 completes B only and the card stays contested until the second is bought. Deck A needs 2, B needs 1, two owned: global deficit 1; A gets both, B lacks 1; one purchase finishes everything, even though A "holds" two contested copies.

The server computes every number; the web view and assistants only show them. Prices are Scryfall's, labelled with their date, as everywhere else.

## API and tools

- **Extend** `GET /api/v1/decks/overlap` (as the issue says): keep the existing fields (`decks_checked`, `shared_cards`, `short_cards`, `cards`) so nothing that reads them breaks, and add `decks` (the per-deck answer above), `contested` (the per-card decisions), `allocation` (the rule used and the order), `purchases` (cursor-paged, cheapest first: `card`, `global_deficit`, `unit_price`, `cost`, `price_status`, `contested`, and the `move` option for contested cards; it includes cards owned zero times, which `contested` excludes; the Lab reads it, `docs/lab-design.md`), `summary` (`decks_analysed`, `decks_needing_purchase`, `finish_all_cost` counted once per card, `unpriced`; the Lab's counters read these, `docs/lab-design.md`), `decks_analysed` (a count) and `decks_skipped` (`[{ deck_id, name, reason }]` for each saved deck that could not be read, so a caller or the view never presents a partial check as complete). New optional query parameter `priority`.
- HAL `_links` to each deck, a response model (`S.DeckOverlap`), the usual rate limit, and the tenancy rule: a deck id in `priority` that is not one of the caller's decks answers 404, like every other deck route (no way to tell another person's deck from a missing one).
- **MCP:** the same data through the existing `get_deck_overlap` tool (output grows, input gains `priority`), so no new tool to classify; the tool description says what contested means. `docs/ai-parity.md` and `public/llms.txt` updated.
- **Paging:** the new lists (`decks` and `contested`) are cursor-paged with `limit`, `cursor` and a HAL `next` link, like the other lists. The allocation is computed across **all** decks first and then sliced, so the page size never changes an answer. The legacy `cards` field keeps its existing 200-card cap for compatibility.
- **Provenance:** `get_deck_overlap` is classified `OWN_DATA_ONLY` today; adding prices makes it Scryfall data, so it moves to `SCRYFALL_DATA` (the wrapper then attaches Scryfall provenance and the Fan Content notice), and the prices carry their date. A test fails if a tool that returns prices is classified own-data-only.
- Performance: it reads each saved deck once and the collection once (as today); decks are few (tens).

## The web view

A section of the Lab (#162), phone-first, "Do your decks stand on their own?":
- **Nothing contested:** one line, "All N analysed decks can be built at the same time from what you own" (and only when `decks_skipped` is empty), with the list collapsed. When a deck could not be checked the line is qualified: "N of M decks checked; K could not be read", naming them and linking to each, because an empty contested list says nothing about a deck that was skipped.
- **Contested:** one row per contested card (the decision), with the options that apply (`buy` always; `move` only when the rule below allows it) and their prices; below it one row per deck (name, a complete tick or the number of lacking cards) that expands to the cards it holds and lacks. Each deck row links to the deck page and its buy list.
- At 390 px the contested rows come first and are full width; at 1400 px the contested cards and the deck rows sit side by side.

## Tests (write first)

- A card owned once and used by two decks: contested; the deck that comes first in the order gets it; the other lacks it (`held_by_other_deck`).
- Owned twice, used by three decks (The World Tree): global deficit 1; one deck lacks it; `move` and `buy` options; buying one copy completes all three.
- The worked examples above: three decks needing 1 with one owned (deficit 2, buying 1 completes one deck only); A needs 2, B needs 1, two owned (one purchase finishes everything); the decks' `cost_to_complete` shares add up to the global deficit times the price, in any order.
- Paging: `limit` and `cursor` return all decks across pages with the same answers as one page; the page size never changes who lacks what.
- Provenance: the tool carries Scryfall provenance and the price date.
- Owned in enough copies for all decks (Sol Ring): not contested, held and lacked by no deck.
- A card owned zero times: all its lacking copies are `not_owned`, it is not in `contested`.
- A mixed shortage: A and B each need 2, 3 owned: B lacks 1, and it is `held_by_other_deck` (available by moving); with only 1 owned and A and B each needing 2, A (first) gets it and lacks 1 (`not_owned` 1), while B gets none and lacks 2 (`not_owned` 1, `held_by_other_deck` 1).
- Basic lands never appear; a deck of only basic lands has need 0, independence 1.0, `stands_alone` and `independent` true, and no error.
- Partial allocation: A and B each need 2, 3 owned: A holds 2; B holds 1 and lacks 1 (the same card in both `holds` and `lacking`).
- Deck order: the default order, and `priority` overriding it; an id in `priority` that is not the caller's (another person's or missing) answers 404 and reveals nothing.
- A fully independent set of decks: `stands_alone` for all, `contested` empty.
- An unreadable saved deck is skipped, as today, and reported in `decks_skipped`; with one complete readable deck and one unreadable deck the view never says all decks are buildable.
- A card owned zero times is not contested and has no `move` option; every contested card has one.
- Cross-tenant: another person's decks and collection never appear.
- Existing `get_deck_overlap` fields unchanged.
- Real data: the owner's four decks give 27 not contested and 1 contested (The World Tree), checked against the collection.

## Decisions for the owner

1. **The default order.** Recommendation: closest to complete first. Alternatives: most recently edited first, or a priority the person sets once and the Vault remembers (needs storing, so a later step).
2. **By name or by printing.** Recommendation: by name for v1 (as overlap and coverage already do). Printing-specific decks (a foil or a set the person cares about) are a later refinement.
3. **Where it lives.** Recommendation: a Lab section (#162), with the same answer through the assistant tool. (The deck ideas lab, #161, can show a deck's borrowed cards on its own page later.)

## Tasks that follow

1. Implement: extend `/decks/overlap` and `get_deck_overlap` (allocation, per-deck and contested answers, tests above).
2. Web: the Lab section (with #162).
3. Docs: `docs/api.md`, `docs/ai-parity.md`, `public/llms.txt`, and the skills that explain decks (they should say what contested means).
