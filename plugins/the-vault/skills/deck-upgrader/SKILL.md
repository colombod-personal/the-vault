---
name: deck-upgrader
description: >-
  Suggest card swaps to improve a Magic deck within a budget, checked by The Vault so the budget and
  legality are guaranteed. Use when someone shares a decklist and wants upgrades, cuts, a budget
  build, a tune-up, or help with a weak role (ramp, draw, removal, sweepers), or wants to save, change or delete a deck.
license: MIT
metadata:
  vault-tools: "whoami list_decks get_deck import_deck_from_link get_archidekt_deck save_deck update_deck delete_deck deck_stats simulate_draws deck_legality get_deck_ideas get_card_alternatives card_roles find_upgrades validate_deck_changes find_combos get_card_oracle"
---

# Deck upgrader

The budget is a hard constraint, and the Vault enforces it with code. Your job is to choose well among
the candidates it returns, explain why, and present only a plan that the validator accepts. Follow
`vault-attribution` (if installed) when you show prices, tags or combos.

**Check the connection when something is off.** If a tool fails or returns nothing you expected, or before you offer to save or change anything, call `whoami`: it says who you are connected as, which scopes you have (read, or also write) and which data versions the Vault holds (rules edition, card data and price dates).

## Flow: Upgrade a deck within a budget

Use when someone wants upgrades, cuts, a budget build or a tune-up for a deck. The budget is a hard constraint and the Vault
enforces it in code: you choose well among what it returns and show only a plan it accepts.

1. **Get the deck and the constraints.**
   - Calls: `list_decks`, `get_deck`, `import_deck_from_link`, `get_archidekt_deck`.
   - Show: People name their decks ("tune my sliver deck"): call `list_decks` with `query` set to their words and use the matching
     deck's `id`. Show the saved deck with `get_deck`. Each deck's `overview` has its format, commander(s), card count and colour
     identity: say which deck you are working on that way ("Sliver Swarm: Commander, led by Sliver Overlord"). You need the
     format and a budget in USD (the most any single added card may cost; ask whether there is also a total cap). Every deck tool
     below takes `deck_id`, so the list is never resent. Otherwise ask for the list.
   - Stop: `closest` lists near names if nothing matches: ask which one. **If the deck is not saved** and the person gives an
     Archidekt link, ask first, then save it with `import_deck_from_link` (one deck, its author credited) and continue with its
     `deck_id`; for a look without saving, `get_archidekt_deck` reads the one deck. If `format_from` shows the format was only read
     from the list, confirm it with the person. Never fetch other decks or search Archidekt (see `archidekt-deck-helper`).
2. **Read the deck.**
   - Calls: `deck_stats`, `deck_legality`.
   - Show: what they say: size, lands, curve, colour identity, the roles it counted, any legality issues, and any card that was
     not found (`unmatched`).
   - Stop: fix legality problems first.
3. **Feel the curve.**
   - Calls: `simulate_draws`.
   - Show: in plain words what the numbers mean for this deck: how often it misses land drops, reaches its key mana (for example
     five mana by turn 5), or ends up discarding to hand size. Show one or two of the sample games. If the answer lists
     `discard_may_be_the_plan`, say that a full hand or discarding may be what the deck wants, not a flaw. The numbers are a hint
     from a simple simulation: repeat its `assumptions` that matter (colours are not checked).
   - Stop: none.
4. **Find the plan.**
   - Calls: none.
   - Show: what the deck is trying to do, if it is obvious from the cards; otherwise ask. Suggestions must serve that plan.
   - Stop: do not assume a strategy the cards do not show.
5. **See what the collection already gives.**
   - Calls: `get_deck_ideas`, `get_card_alternatives`.
   - Show: `get_deck_ideas` shows the deck's cards in role lanes with what the person owns, what is missing and what another deck
     holds (`borrowed_from`); `get_card_alternatives` with `card` lists owned cards that could stand in for a card of the deck,
     free copies first. The lanes' roles are the eight coarse roles, a community's opinion; the stand-ins' jobs are the Vault's own
     reading of the Oracle text: say which is which. `card_roles` with `deck_id` shows what the deck does as a whole, each of 22
     roles with its cards and the empty ones as gaps (the Vault's reading, not an official classification: repeat its `label`). A
     free card costs nothing, so offer it before a purchase.
   - Stop: a card another deck holds is a choice for the person (the other deck loses it): say which deck.
6. **Get candidates.**
   - Calls: `find_upgrades` with the budget (and `roles` if the person named a weak area).
   - Show: candidates are legal, inside the deck's colours, not already in the deck, and priced within budget. They are ordered by
     popularity (EDHREC rank). **Popularity is not power or fit**: say that once, and choose by how a card serves the plan.
   - Stop: none.
7. **Choose swaps.**
   - Calls: none.
   - Show: for each swap the card to cut and the card to add, the price (with its date) and the reason in one sentence tied to the
     plan. Cut candidates from the tool are the least-played untagged cards: a starting point, not a verdict. Prefer fewer,
     better swaps over many.
   - Stop: none.
8. **Validate.**
   - Calls: `validate_deck_changes` with the exact `cuts`, `adds`, format and budget.
   - Show: `added_cost_usd` against the budget. Cuts are not refunded: the total is the adds.
   - Stop: if `valid` is false, read `issues`, fix the plan and validate again. **Present the plan only when `valid` is true.**
9. **Optionally check combos.**
   - Calls: `find_combos`, `get_card_oracle`.
   - Show: combos the new cards complete, attributed to Commander Spellbook; read a card's text with `get_card_oracle` before you
     describe what it does.
   - Stop: none.
10. **Save it, only if they ask.**
    - Calls: `validate_deck_changes` with `include_text`, then `update_deck`.
    - Show: the change in words (cut these, add these, the deck's `overview` after). `deck_text` from the validation is the exact
      list to give `update_deck` with the deck's current name and format; the saved deck keeps an earlier version in its history.
    - Stop: `update_deck` only after they say yes to that change. Never save a plan that was not validated.

## Saving and deleting decks

- **Keep a list the person pasted:** `save_deck` with a `name`, the `text` and the `format` (and `source_url` and `source_author` if it
  came from a link). Ask first, then show the saved deck with `get_deck`. A deck read from Archidekt is saved with
  `import_deck_from_link`, not this.
- **Rename it or fix its format:** `update_deck` replaces a saved deck's name and text, so send the current text from `get_deck`
  with the change; ask first.
- **Delete a deck:** `delete_deck` without `confirm` shows the deck and changes nothing. Show it, say it cannot be brought back
  from here, and send `confirm` only after the person says yes to that deck. Deleting a deck never touches the collection.

## Say plainly

- Lead with the deck: its name, format, commander(s), card count and colour identity (the `deck` block every deck tool
  returns), before any card line.
- Numbers exactly as the tools returned them; the simulation's numbers are a hint, not a promise.
- A stand-in from `get_card_alternatives` is a suggestion, never "the same card": quote both Oracle texts, and say its
  roles are the Vault's own reading of the card text (`label`), not an official classification.
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
