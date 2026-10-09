---
name: shopping-assistant
description: >-
  Work out which cards of a Magic decklist a person still needs and give them a list to buy, with
  dated prices. Use when someone asks what they are missing for a deck, how much a deck will cost
  them, or wants a list to paste into Card Kingdom, Cardmarket or another store.
license: MIT
metadata:
  vault-tools: "whoami list_decks get_deck import_deck_from_link get_archidekt_deck parse_decklist shopping_list validate_deck_changes update_owned_cards confirm_owned_cards_update"
---

# Shopping assistant

Turn a decklist into what the person actually has to buy, using their collection in the Vault. The Vault
never contacts stores, fills carts or knows a store's price today; it gives a list and Scryfall's dated
prices. Follow `vault-attribution` (if installed) when you show prices.

**Check the connection when something is off.** If a tool fails or returns nothing you expected, or before you offer to save or change anything, call `whoami`: it says who you are connected as, which scopes you have (read, or also write) and which data versions the Vault holds (rules edition, card data and price dates).

## Flow: Buy what a deck is missing

Use when the person asks what they are missing for a deck, what it will cost, or wants a list for a store. The flow ends at a
paste-ready list: nothing is bought, imported into a store or put in a cart.

1. **Get the deck the way the person names it.**
   - Calls: `list_decks`, `get_deck`, `import_deck_from_link`, `get_archidekt_deck`, `parse_decklist`.
   - Show: People say "my sliver deck", not a number. Call `list_decks` with `query` set to their words; if it matches, that is
     the deck (use its `id`). Show the saved deck with `get_deck` and name it by its `overview` (format, commander(s), card count),
     never by card lines. Only played cards count unless the person asks for the sideboard or maybeboard. A list the person pasted
     goes to `parse_decklist` first (it shows the cards, quantities and sections it read) and then to `shopping_list` as `text`.
   - Stop: if nothing matches, `closest` lists near names: ask which one. **If the deck is not saved** and the person gives an
     Archidekt link, ask first, then save it with `import_deck_from_link` (it reads that one deck, keeps its sections and credits
     its author) and continue with its `deck_id`; for a quick look without saving, `get_archidekt_deck` reads the one deck. Never
     fetch other decks or search Archidekt (see `archidekt-deck-helper`).
2. **Ask what they will buy with, once, when it matters.**
   - Calls: none.
   - Show: if the person names a store, pass `format`: `cardkingdom` (Card Kingdom's Deck Builder), `tcgplayer` (Mass Entry, with
     set and collector number), `cardmarket` (want list, with the expansion's name), `csv`, or `plain`. If they have rules for the
     printing (only foil or nonfoil, a language, certain sets, a worst condition), pass `finish`, `language`, `sets` and
     `condition` instead of guessing.
   - Stop: none; do not ask again for what they already said.
3. **Call `shopping_list`.**
   - Calls: `shopping_list` with the `deck_id` (or `text`, for a list the person pasted) and those options.
   - Show: each card you do not own, the quantity missing, the price with its date (`price_date`), `total_usd`, how many lines
     have no price, and a paste-ready `text` in the format asked. With rules, each line says which printing it chose
     (`printing`: set, collector number, finish, language), and a card with no printing that fits is marked
     `no_qualifying_printing` with a `reason`: tell the person which cards those are, they are not in the `text`. `store_format`
     says what the store's own tool reads and what the paste cannot carry.
   - Stop: a line with `known_card: false` is a name that is not a card: ask for the correct name rather than guessing.
4. **Present it clearly.**
   - Calls: none.
   - Show: the list (name, quantity, unit price), the total and its date, the printings chosen if any, and the number of unpriced
     lines. Say the prices are Scryfall's (sourced from TCGplayer and Cardmarket) for the cheapest printing that fits, so a store
     may differ. Scryfall's prices are not per condition: a `condition` rule changes no price, say so.
   - Stop: none.
5. **Say how to buy.**
   - Calls: none.
   - Show: tell the person to paste the `text` into the store's own list tool, check what it matched (the tool reports lines it
     could not match), and compare there. Do not claim the list was imported anywhere, and do not say anything is "in your cart".
   - Stop: never use a browser or any other tool to open a store, fill its cart or paste the list there for them: the list is
     where your help ends.
6. **Budget help, if they have one.**
   - Calls: `validate_deck_changes`.
   - Show: changes checked against the budget, or cheaper alternatives only from tool results.
   - Stop: do not invent cheaper cards; present a change only when `valid` is true.
7. **When the cards arrive, offer to add them.**
   - Calls: `update_owned_cards`, `confirm_owned_cards_update`.
   - Show: the printings they actually got (ask; they may not know, then `printing_unknown`), then the preview.
   - Stop: `confirm_owned_cards_update` only after they say yes to the preview. Never add cards because they were on a shopping
     list: only what they say they received.

## Do not

- Quote a store's price, stock or shipping: you do not have them.
- Promise availability or condition: the Vault has no store's stock, and its prices are not per condition.
- Say which store is cheapest, or recommend a store for price: you have no store's price.
- Mix prices from different dates without saying so.
