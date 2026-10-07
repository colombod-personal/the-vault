---
name: shopping-assistant
description: >-
  Work out which cards of a Magic decklist a person still needs and give them a list to buy, with
  dated prices. Use when someone asks what they are missing for a deck, how much a deck will cost
  them, or wants a list to paste into Card Kingdom, Cardmarket or another store.
license: MIT
metadata:
  vault-tools: "list_decks shopping_list validate_deck_changes update_owned_cards confirm_owned_cards_update"
---

# Shopping assistant

Turn a decklist into what the person actually has to buy, using their collection in the Vault. The Vault
never contacts stores, fills carts or knows a store's price today; it gives a list and Scryfall's dated
prices. Follow `vault-attribution` (if installed) when you show prices.

## Procedure

1. **Get the deck.** People name a deck ("what am I missing for my sliver deck"): call `list_decks` with
   `query` set to their words and use the matching deck's `id`; if nothing matches, `closest` lists near names, or
   ask for the list or an Archidekt link (see `archidekt-deck-helper`). Only played cards count unless the person
   asks for the sideboard or maybeboard.
2. **Ask what they will buy with, once, when it matters.** If the person names a store, pass `format`: `cardkingdom`
   (Card Kingdom's Deck Builder), `tcgplayer` (Mass Entry, with set and collector number), `cardmarket` (want list, with
   the expansion's name), `csv`, or `plain`. If they have rules for the printing (only foil or nonfoil, a language,
   certain sets, a worst condition), pass `finish`, `language`, `sets` and `condition` instead of guessing.
3. **Call `shopping_list`** with the `deck_id` (or `text`, for a list the person pasted) and those options. It returns each
   card you do not own, the quantity missing, the price with its date (`price_date`), `total_usd`, how many lines have no
   price, and a paste-ready `text` in the format asked. With rules, each line says which printing it chose
   (`printing`: set, collector number, finish, language), and a card with no printing that fits is marked
   `no_qualifying_printing` with a `reason`: tell the person which cards those are, they are not in the `text`.
   `store_format` says what the store's own tool reads and what the paste cannot carry.
4. **Present it clearly.** Show the list (name, quantity, unit price), the total and its date, the printings chosen if
   any, and the number of unpriced lines. Say the prices are Scryfall's (sourced from TCGplayer and Cardmarket) for
   the cheapest printing that fits, so a store may differ. Scryfall's prices are not per condition: a `condition`
   rule changes no price, say so.
5. **Say how to buy.** Tell the person to paste the `text` into the store's own list tool, check what it matched
   (the tool reports lines it could not match), and compare there. Do not claim the list was imported anywhere, and
   do not say anything is "in your cart".
6. **Budget help.** If the person has a budget, use `validate_deck_changes` to check changes against it,
   or suggest cheaper alternatives only from tool results. Do not invent cheaper cards.
7. **If a card is not found**, the line says `known_card: false`: ask for the correct name rather than guessing.
8. **When the cards arrive**, offer to add them: `update_owned_cards` with the printings they actually got (ask;
   they may not know, then `printing_unknown`), show the preview, and `confirm_owned_cards_update` only after
   they say yes. Never add cards because they were on a shopping list: only what they say they received.

## Do not

- Quote a store's price, stock or shipping: you do not have them.
- Promise availability or condition: the Vault has no store's stock, and its prices are not per condition.
- Say which store is cheapest, or recommend a store for price: you have no store's price.
- Mix prices from different dates without saying so.
