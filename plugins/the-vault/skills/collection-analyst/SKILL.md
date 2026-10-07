---
name: collection-analyst
description: >-
  Answer questions about a person's own Magic card collection in The Vault - value, gains and losses,
  what they own, what is shared with them, and how a deck fits it. Use for "what is my collection
  worth", "my most valuable cards", "do I own the cards for this deck", price changes, sets or colors
  in the collection, small edits when they bought, sold or traded cards, and bringing in a fresh export from their app.
license: MIT
metadata:
  vault-tools: "get_collection_summary search_cards get_card list_sets get_collection_stats get_collection_breakdowns get_valuation get_value_history get_acquisition_timeline list_card_names check_decklist list_shared_with_me lookup_cards refresh_prices whoami update_owned_cards confirm_owned_cards_update undo_owned_cards_update show_owned_printings import_collection_csv start_collection_upload get_staged_upload confirm_staged_upload"
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
   - "show me my Sol Rings", "which printings do I have": `show_owned_printings` (pictures, most copies first);
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
2. A line with `choose_printing` lists the printings (theirs first), each with its picture: hosts that show the
   Vault's views display them to tap, so just ask them to tap the one they have; otherwise ask in their words.
   Never pick for them, and never draw your own grid of card images (the chat blocks those images: use the
   Vault's view). For an add they may say they do not know; then send `printing_unknown`. A line with
   `did_you_mean` names a card that does not exist: ask which they meant.
3. Show the preview: each card and printing, copies before and after, the value change. Apply with
   `confirm_owned_cards_update` (the same lines and the preview's `confirmation`) only after they say yes.
   If it is refused (expired, or the collection changed), preview again and ask again.
4. Say it is in their import history under this app's name and can be undone with `undo_owned_cards_update`
   (which also previews first). Tell them a later import of their app's file keeps this edit and applies only what
   changed in their app; a card changed in both places is listed as a conflict in that import's preview, and the
   edit made here is kept unless they say otherwise.
5. More than 50 lines or a big removal is refused: suggest importing a fresh export from their app instead.

## Importing a fresh export

When they bring a new export from their app (Dragon Shield, Moxfield, CSV): a file that fits in the chat goes to
`import_collection_csv`; a big one goes through `start_collection_upload` (give them the link) and then
`get_staged_upload` once they say it is uploaded. An import applies only what changed in their app since their last
import and keeps the edits made here.

1. Preview first (no `confirm`). Show three things from `merge`: what their app changed (the only part that is
   applied), the edits made here that are kept, and each conflict.
2. A conflict is a card changed in both places, or dropped by their app but edited here. Ask about each one, one card
   at a time, in their words, with copies at the last import, in their app and here. If they do not choose, the edit
   made here is kept. Send their answers as `use_app_value` (the conflict `id`s where they want their app's value),
   or `conflicts` for all of them.
3. Import with `confirm` (`import_collection_csv`, or `confirm_staged_upload`) only after they say yes. If they want
   the file to replace everything, use `replace_everything` and say first how many edits made here it discards.
4. Say what the import kept and what it asked, as the preview showed it; do not describe changes it did not make.

## Privacy

The collection is private to its owner. Do not repeat it elsewhere, put it in a public place, or use it to
answer other people's questions. Credit the artist and Scryfall when you show a card image, and never crop it.

## Do not

- Add up values yourself when a tool gives the total, or round away the date.
- Assume a missing price is zero; say it is unknown.
