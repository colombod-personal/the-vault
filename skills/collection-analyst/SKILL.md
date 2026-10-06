---
name: collection-analyst
description: >-
  Answer questions about a person's own Magic card collection in The Vault - value, gains and losses,
  what they own, what is shared with them, and how a deck fits it. Use for "what is my collection
  worth", "my most valuable cards", "do I own the cards for this deck", price changes, sets or colors
  in the collection, and small edits when they bought, sold or traded cards.
license: MIT
metadata:
  vault-tools: "get_collection_summary search_cards get_card list_sets get_collection_stats get_collection_breakdowns get_valuation get_value_history get_acquisition_timeline list_card_names check_decklist list_shared_with_me lookup_cards refresh_prices whoami update_owned_cards confirm_owned_cards_update undo_owned_cards_update"
---

# Collection analyst

This is the person's own private data plus Scryfall's prices. Report what the tools return; do not
estimate or fill gaps. Follow `vault-attribution` (if installed) for card data and prices.

## Procedure

1. **Start with `get_collection_summary`.** It has copies, printings, market value, amount paid, P&L (only
   over copies with a known price paid) and the date of the prices. State that date with any value.
2. **Pick the tool that answers the question** rather than paging through everything:
   - most valuable or specific printings: `search_cards` with `sort="-value"`, a small `limit`, `query`,
     `set` or `name` filters; follow `next_cursor` only if more is needed;
   - one printing in detail and its recent prices: `get_card`;
   - rolled up by card name (top N, by color or type): `list_card_names`;
   - sets: `list_sets`; highlights, gains and losses: `get_collection_stats`;
   - colors, types, mana values, rarities: `get_collection_breakdowns`;
   - value over time: `get_value_history`, `get_valuation`; buying pace: `get_acquisition_timeline`;
   - does the collection cover a deck: `check_decklist`; any card, owned or not: `lookup_cards`.
3. **Refresh only when asked or stale.** `refresh_prices` needs write access; call it until `remaining` is 0.
4. **Shared collections.** `list_shared_with_me` shows what others shared; pass `share_id` to the collection
   tools to read one. Never mix someone else's collection into the person's own totals.
5. **Report with dates and limits.** Prices are USD market prices from Scryfall on the stated date. "Paid"
   is what the person recorded; unknown costs are not zero. P&L covers only copies with a known price.

## Editing what they own

When the person says they bought, sold, traded or found cards:

1. Send their words as lines to `update_owned_cards` (add, remove, or set how many). It changes nothing.
2. A line with `choose_printing` lists the printings (theirs first): ask which one, in their words, and never
   pick for them. For an add they may say they do not know; then send `printing_unknown`. A line with
   `did_you_mean` names a card that does not exist: ask which they meant.
3. Show the preview: each card and printing, copies before and after, the value change. Apply with
   `confirm_owned_cards_update` (the same lines and the preview's `confirmation`) only after they say yes.
   If it is refused (expired, or the collection changed), preview again and ask again.
4. Say it is in their import history under this app's name and can be undone with `undo_owned_cards_update`
   (which also previews first). A later re-import from their app keeps these edits.
5. More than 50 lines or a big removal is refused: suggest importing a fresh export from their app instead.

## Privacy

The collection is private to its owner. Do not repeat it elsewhere, put it in a public place, or use it to
answer other people's questions. Credit the artist and Scryfall when you show a card image, and never crop it.

## Do not

- Add up values yourself when a tool gives the total, or round away the date.
- Assume a missing price is zero; say it is unknown.
