---
name: shopping-assistant
description: >-
  Work out which cards of a Magic decklist a person still needs and give them a list to buy, with
  dated prices. Use when someone asks what they are missing for a deck, how much a deck will cost
  them, or wants a list to paste into Card Kingdom, Cardmarket or another store.
license: MIT
metadata:
  vault-tools: "shopping_list validate_deck_changes"
---

# Shopping assistant

Turn a decklist into what the person actually has to buy, using their collection in the Vault. The Vault
never contacts stores, fills carts or knows a store's price today; it gives a list and Scryfall's dated
prices. Follow `vault-attribution` (if installed) when you show prices.

## Procedure

1. **Get the decklist.** One card per line (`1 Sol Ring`), commander under a `Commander` header. Ask whether
   to include the sideboard or maybeboard; by default only played cards count.
2. **Call `shopping_list`.** It returns each card you do not own, the quantity missing, the cheapest known
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

## Do not

- Quote a store's price, stock or shipping: you do not have them.
- Promise availability, condition or a specific printing; the list names cards only.
- Mix prices from different dates without saying so.
