---
name: archidekt-deck-helper
description: >-
  Take a public Archidekt deck, compare it with the cards a person owns in The Vault, suggest changes
  within a budget, and help them order what is missing. Use when someone shares an Archidekt deck link,
  asks what they still need to buy for it, wants it upgraded, or wants to find the missing cards in a shop.
license: MIT
metadata:
  vault-tools: "whoami list_decks get_deck import_deck_from_link refresh_deck get_archidekt_deck deck_legality find_upgrades validate_deck_changes shopping_list"
---

# Archidekt deck helper

The person points at a deck on Archidekt (`archidekt.com/decks/<number>/...`). You read it once, compare it
with their Vault collection, and give them a change list and a buying list. **You never edit Archidekt**:
the person applies changes there themselves. Follow `vault-attribution` (if installed) for every price and
every piece of Archidekt data.

**Check the connection when something is off.** If a tool fails or returns nothing you expected, or before you offer to save or change anything, call `whoami`: it says who you are connected as, which scopes you have (read, or also write) and which data versions the Vault holds (rules edition, card data and price dates).

## Flow: Evaluate a deck from a link

Use when the person shares an Archidekt link (or names a deck saved from one) and wants it checked against their collection:
is it legal, what would improve it, what is missing. The link is one deck; you read it once.

1. **Find the deck the way the person names it.**
   - Calls: `list_decks`.
   - Show: People say "my sliver deck", not a number. Call `list_decks` with `query` set to the words they used; if it matches,
     that is the deck (use its `id` below). Name decks by their `overview`: format, commander(s), card count ("Sliver Swarm:
     Commander, led by Sliver Overlord, 100 cards"), never by card lines.
   - Stop: if nothing matches, `closest` lists near names: ask which one. Only if the deck is not saved, ask for its Archidekt link.
2. **Refresh a saved deck** ("refresh my sliver deck", "pull the latest from Archidekt").
   - Calls: `refresh_deck` with its `id`.
   - Show: what changed on Archidekt, per section and card.
   - Stop: replace the saved list (`confirm` true with the preview's `fingerprint`) only once the person says yes. A deck from
     Moxfield or another site can't be read: ask them to paste a fresh export.
3. **If it is not saved, read it, and save it from the link if they want.**
   - Calls: `get_archidekt_deck`, `import_deck_from_link`.
   - Show: for a quick look without saving, `get_archidekt_deck` reads the one deck. To keep it, `import_deck_from_link` with the
     link: the server reads the one deck, keeps its sections and credits its author. Do not convert the deck yourself. Say the
     deck is Archidekt's (the saved deck's `credit` has the notice to repeat) and give the link back.
   - Stop: ask the person before you save it. One deck per request: never fetch other decks, other people's decks or lists of
     decks, and do not search or crawl Archidekt.
4. **Check it.**
   - Calls: `get_deck`, `deck_legality`.
   - Show: `get_deck` shows the saved deck with, for each card, its section and how many copies the person owns. Call
     `deck_legality` with the saved deck's `deck_id` and the format (no need to send the list). Report problems and cards that
     were not found. The sideboard and maybeboard are not part of the deck unless the person asks.
   - Stop: fix legality problems before anything else.
5. **Ask what they want**, if not clear.
   - Calls: none.
   - Show: upgrades within a budget, only what to buy, or both. Get a budget in USD (the most any one added card may cost)
     before suggesting anything.
   - Stop: no budget, no upgrade suggestions.
6. **Upgrades (optional).**
   - Calls: `find_upgrades`, `validate_deck_changes`.
   - Show: swaps that serve the deck's plan, with the exact cuts and adds validated, and the added cost against the budget.
   - Stop: present a plan only when `valid` is true.
7. **What is missing.**
   - Calls: `shopping_list` with the `deck_id` (or the list with the swaps applied, if any).
   - Show: each card the person does not own, the quantity, the cheapest known price with its date, the total and a paste-ready
     `text`.
   - Stop: none.
8. **Give them two things to copy.**
   - Calls: none.
   - Show: the **change list** (cards to cut and add, so they can edit the deck on Archidekt) and the **buying list** (the `text`
     from `shopping_list`, to paste into a store's own list tool).
   - Stop: you never edit Archidekt: the person applies the changes there.
9. **Help them find the cards.**
   - Calls: none.
   - Show: for each missing card, plain search links the person clicks (encode the card name; spaces become `+`). Offer only
     the shops they name, or these three:
     - Card Kingdom: `https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=CARD+NAME`
     - Cardmarket: `https://www.cardmarket.com/en/Magic/Products/Search?searchString=CARD+NAME`
     - Magic Madhouse: `https://magicmadhouse.co.uk/search.php?search_query=CARD+NAME`

     Say the links only open each shop's own search. You do not know any shop's price, stock or shipping today, so
     you cannot say which shop is cheapest. Tell them to compare totals themselves, and to use the shop's own
     saved-search or alert feature for cards they want to watch (explain how in the shop's words, not by doing it for them).
   - Stop: do not open a shop, search it or fill a cart for them.

## Say plainly

- Prices come from Scryfall's data on the date shown (sourced from TCGplayer and Cardmarket), not from
  Archidekt or from any shop today.
- You did not change anything on Archidekt and did not order anything.

## Do not

- Fetch more than the one deck requested, or search Archidekt or any shop on the person's behalf.
- Log in to, write to or "sync" an Archidekt account, or claim a card is in a cart.
- Quote a shop's price, stock, condition or shipping, or pick a "best price" shop.
- Present data from Archidekt as the Vault's own.
