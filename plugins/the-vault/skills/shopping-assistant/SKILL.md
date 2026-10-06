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
2. **Call `shopping_list`** with the `deck_id` (or `text`, for a list the person pasted). It returns each card you do not own, the quantity missing, the cheapest known
   price (with its date), `total_usd`, how many lines have no price, and a paste-ready `text`.
3. **Present it clearly.** Show the list (name, quantity, unit price), the total and its date, and the
   number of unpriced lines. Say the prices are Scryfall's cheapest priced paper printing (sourced from
   TCGplayer and Cardmarket), so a store may differ.
4. **Say how to buy.** Tell the person to paste the `text` into the store's own list or deck tool (for
   example Card Kingdom's Deck Builder) or to enter it on the store's site, check how names are matched,
   and compare. Do not claim the list was imported anywhere, and do not say anything is "in your cart".
5. **Budget help.** If the person has a budget, use `validate_deck_changes` to check changes against it,
   or suggest cheaper alternatives only from tool results. Do not invent cheaper cards.
6. **If a card is not found**, the line says `known_card: false`: ask for the correct name rather than guessing.
7. **When the cards arrive**, offer to add them: `update_owned_cards` with the printings they actually got (ask;
   they may not know, then `printing_unknown`), show the preview, and `confirm_owned_cards_update` only after
   they say yes. Never add cards because they were on a shopping list: only what they say they received.

## Do not

- Quote a store's price, stock or shipping: you do not have them.
- Promise availability, condition or a specific printing; the list names cards only.
- Mix prices from different dates without saying so.
