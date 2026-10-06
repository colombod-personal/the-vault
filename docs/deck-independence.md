# Deck independence: can each deck stand on its own? (design for #165)

Status: design for owner review. Nothing here is built.

## The question

Owner request (2026-10-05): "see if the decks are able to stand on their own, or if something in a deck must be borrowed from another deck. This helps decide whether to move a card or buy a new one."

For the saved decks: can they all be built **at the same time** from the copies owned? If not, which cards are contested, which deck would lose each one, and what does a copy cost to end the borrowing.

## What the owner's data says today (2026-10-06, via `get_deck_overlap`)

- 4 saved decks (Avatar Aang, Nazgûl, Sliver Swarm tuned with rage, The dragon in the night).
- 28 cards appear in more than one deck. **27 of them are not contested**: the collection holds at least as many copies as all the decks need together (Sol Ring: needed by 4 decks, owned 31; Cavern of Souls: needed 4, owned 8; Leyline of the Guildpact: needed 3, owned exactly 3).
- **1 is contested: The World Tree.** Three decks (Avatar Aang, Sliver Swarm, The dragon in the night) each need 1; the collection holds 2. One of the three decks has to go without it, or a third copy is bought (Scryfall's cheapest price today: $6.60).

So the useful answer is narrow and specific, and it is easy to lose in a long list. The design therefore reports only **contested** cards as borrowing, and says plainly when nothing is contested.

## Definitions

Counted by card name (basic lands left out), the same way `get_deck_overlap` and the deck coverage already count: any printing owned counts.

For each card in a deck, with `need` copies in that deck:
- **free**: the collection holds enough copies for every deck that uses the card (`have >= need_for_all`). Nothing is borrowed. Most shared cards are this.
- **borrowed**: `have < need_for_all` but the deck still gets its copies under the allocation below. Another deck that wants the card goes without, or a copy has to move.
- **short**: not covered even after allocation (`need` exceeds the copies this deck can be given). The deck page already shows this as "missing"; here it is split into *not owned at all* and *owned but allocated to another deck*.

A deck **stands alone** when it has no borrowed and no short cards, under the allocation.

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
borrowed        [{ card, quantity, contested_with: [deck names that lose out] }]
short           [{ card, quantity, owned_elsewhere, price }]
stands_alone    true when borrowed and short are both empty
independence    free / need, rounded, for sorting only
cost_to_stand_alone   the cheapest known prices of the copies to buy so this deck stands alone (borrowed + short), with `unpriced` counted
```

and per **contested card**, once, because that is the decision:

```
card, have, need_for_all
decks: [{ deck, need, gets: N }]
options:
  - move:  "give the copy to <deck>; <other deck> then needs one" (what changes)
  - buy:   "buy N copy(ies) at $X (Scryfall's cheapest price, dated)" (what it costs; both decks stand alone after)
```

The server computes every number; the web view and assistants only show them. Prices are Scryfall's, labelled with their date, as everywhere else.

## API and tools

- **Extend** `GET /api/v1/decks/overlap` (as the issue says): keep the existing fields (`decks_checked`, `shared_cards`, `short_cards`, `cards`) so nothing that reads them breaks, and add `decks` (the per-deck answer above), `contested` (the per-card decisions) and `allocation` (the rule used and the order). New optional query parameter `priority`.
- HAL `_links` to each deck, a response model (`S.DeckOverlap`), the usual rate limit, and the tenancy rule: a deck id in `priority` that is not one of the caller's decks answers 404, like every other deck route (no way to tell another person's deck from a missing one).
- **MCP:** the same data through the existing `get_deck_overlap` tool (output grows, input gains `priority`), so no new tool to classify; the tool description says what contested means. `docs/ai-parity.md` and `public/llms.txt` updated.
- Performance: it reads each saved deck once and the collection once (as today); decks are few (tens), so no paging beyond today's 200-card cap on the card list; the `decks` list is capped at 50 with a note when more exist.

## The web view

A section of the Lab (#162), phone-first, "Do your decks stand on their own?":
- **Nothing contested:** one line, "All N decks can be built at the same time from what you own", and the list collapsed.
- **Contested:** one row per contested card (the decision), with the two options and their prices; below it one row per deck (name, stands-alone tick or the number of borrowed cards) that expands to its borrowed and short cards. Each deck row links to the deck page and its buy list.
- At 390 px the contested rows come first and are full width; at 1400 px the contested cards and the deck rows sit side by side.

## Tests (write first)

- A card owned once and used by two decks: contested; the deck that comes first in the order gets it; the other has it as `short` with `owned_elsewhere`.
- Owned twice, used by three decks (The World Tree): one deck loses; `move` and `buy` options; buying one copy makes all three stand alone.
- Owned in enough copies for all decks (Sol Ring): not contested, not borrowed.
- A card owned zero times: `short`, not "borrowed".
- Basic lands never appear.
- Deck order: the default order, and `priority` overriding it; an id in `priority` that is not the caller's (another person's or missing) answers 404 and reveals nothing.
- A fully independent set of decks: `stands_alone` for all, `contested` empty.
- An unreadable saved deck is skipped, as today.
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
