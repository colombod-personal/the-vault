---
name: deck-upgrader
description: >-
  Suggest card swaps to improve a Magic deck within a budget, checked by The Vault so the budget and
  legality are guaranteed. Use when someone shares a decklist and wants upgrades, cuts, a budget
  build, a tune-up, or help with a weak role (ramp, draw, removal, sweepers).
license: MIT
metadata:
  vault-tools: "deck_stats simulate_draws deck_legality find_upgrades validate_deck_changes find_combos get_card_oracle"
---

# Deck upgrader

The budget is a hard constraint, and the Vault enforces it with code. Your job is to choose well among
the candidates it returns, explain why, and present only a plan that the validator accepts. Follow
`vault-attribution` (if installed) when you show prices, tags or combos.

## Procedure

1. **Get the deck and the constraints.** You need the decklist, the format, and a budget in USD (the
   most any single added card may cost; ask whether there is also a total cap). If the deck has a
   commander, make sure it is under a `Commander` header.
2. **Read the deck.** Call `deck_stats` and `deck_legality`. Report what they say: size, lands, curve,
   color identity, the roles it counted, any legality issues. Fix legality problems first, and say if any
   card was not found (`unmatched`).
3. **Feel the curve.** Call `simulate_draws` and explain in plain words what the numbers mean for this deck: how
   often it misses land drops, reaches its key mana (for example five mana by turn 5), or ends up discarding to hand
   size. Show one or two of the sample games. If the answer lists `discard_may_be_the_plan`, say that a full hand or
   discarding may be what the deck wants, not a flaw. The numbers are a hint from a simple simulation: repeat its
   `assumptions` that matter (colours are not checked).
4. **Find the plan.** Ask the user what the deck is trying to do if it is not obvious from the cards;
   suggestions must serve that plan. Do not assume a strategy the cards do not show.
5. **Get candidates.** Call `find_upgrades` with the budget (and `roles` if the user named a weak area).
   Candidates are legal, inside the deck's colors, not already in the deck, and priced within budget. They are
   ordered by popularity (EDHREC rank). **Popularity is not power or fit**: say that once, and choose by
   how a card serves the plan.
6. **Choose swaps.** For each swap name the card to cut and the card to add, the price (with its date) and
   the reason in one sentence tied to the plan. Cut candidates from the tool are the least-played
   untagged cards: a starting point, not a verdict. Prefer fewer, better swaps over many.
7. **Validate.** Call `validate_deck_changes` with the exact `cuts`, `adds`, format and budget. If `valid`
   is false, read `issues`, fix the plan and validate again. **Present the plan only when `valid` is true.**
   Show `added_cost_usd` against the budget. Cuts are not refunded: the total is the adds.
8. **Optionally check combos.** If the user cares, call `find_combos` and mention combos the new cards
   complete, attributed to Commander Spellbook.

## Say plainly

- Roles are Scryfall Tagger tags, a community's opinion. Guideline counts (about 10 ramp, 10 draw, 8
  removal, 2 sweepers in 100-card decks) are a common habit, not a rule.
- Prices are Scryfall's cheapest printing on the date shown, not a store's price today.
- What the legality check did not cover (the tool lists it in `not_checked`).

## Do not

- Suggest a card the tools did not return without validating it. If the user wants a specific card, put it
  in `adds` and let `validate_deck_changes` judge it; use `get_card_oracle` to read its text before you
  describe what it does.
- Present a plan that failed validation, or round a price down to fit.
- Claim a swap makes the deck stronger by a number; say how it serves the plan.
