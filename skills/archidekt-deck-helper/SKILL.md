---
name: archidekt-deck-helper
description: >-
  Take a public Archidekt deck, compare it with the cards a person owns in The Vault, suggest changes
  within a budget, and help them order what is missing. Use when someone shares an Archidekt deck link,
  asks what they still need to buy for it, wants it upgraded, or wants to find the missing cards in a shop.
license: MIT
metadata:
  vault-tools: "get_archidekt_deck deck_legality find_upgrades validate_deck_changes shopping_list"
---

# Archidekt deck helper

The person points at a deck on Archidekt (`archidekt.com/decks/<number>/...`). You read it once, compare it
with their Vault collection, and give them a change list and a buying list. **You never edit Archidekt**:
the person applies changes there themselves. Follow `vault-attribution` (if installed) for every price and
every piece of Archidekt data.

## Procedure

1. **Read the deck once.** Take the number from the link and call `get_archidekt_deck`. One deck per
   request: do not fetch other decks, other users' decks or lists of decks. Archidekt's terms do not allow
   automated searching, so stay with the single deck the person gave you. Say the deck data is Archidekt's
   and give the link back.
2. **Make a decklist.** Turn the cards into one line each (`1 Sol Ring`), the commander under a `Commander`
   header. Leave out cards in the sideboard, maybeboard or a "Considering" category unless the person wants
   them. If Archidekt marks a card as owned or not, ignore it: the Vault's collection is the source here.
3. **Check it.** Call `deck_legality` with the format. Report problems and cards that were not found; fix
   those first.
4. **Ask what they want**, if not clear: upgrades within a budget, only what to buy, or both. Get a budget
   in USD (the most any one added card may cost) before suggesting anything.
5. **Upgrades (optional).** Call `find_upgrades` with the budget, choose swaps that serve the deck's plan,
   and call `validate_deck_changes` with the exact cuts and adds. Present a plan only when `valid` is true,
   with the added cost against the budget.
6. **What is missing.** Call `shopping_list` with the decklist (with the swaps applied, if any). It lists
   each card the person does not own, the quantity, the cheapest known price with its date, the total and a
   paste-ready `text`.
7. **Give them two things to copy.**
   - The **change list**: cards to cut and add, so they can edit the deck on Archidekt.
   - The **buying list**: the `text` from `shopping_list`, to paste into a store's own list tool.
8. **Help them find the cards.** For each missing card, give plain search links the person clicks
   (encode the card name; spaces become `+`). Offer only the shops they name, or these three:
   - Card Kingdom: `https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=CARD+NAME`
   - Cardmarket: `https://www.cardmarket.com/en/Magic/Products/Search?searchString=CARD+NAME`
   - Magic Madhouse: `https://magicmadhouse.co.uk/?q=CARD+NAME`

   Say the links only open each shop's own search. You do not know any shop's price, stock or shipping
   today, so you cannot say which shop is cheapest. Tell them to compare totals themselves, and to use the
   shop's own saved-search or alert feature for cards they want to watch (explain how in the shop's words,
   not by doing it for them).

## Say plainly

- Prices come from Scryfall's data on the date shown (sourced from TCGplayer and Cardmarket), not from
  Archidekt or from any shop today.
- You did not change anything on Archidekt and did not order anything.

## Do not

- Fetch more than the one deck requested, or search Archidekt or any shop on the person's behalf.
- Log in to, write to or "sync" an Archidekt account, or claim a card is in a cart.
- Quote a shop's price, stock, condition or shipping, or pick a "best price" shop.
- Present data from Archidekt as the Vault's own.
