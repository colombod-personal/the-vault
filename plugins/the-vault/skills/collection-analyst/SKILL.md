---
name: collection-analyst
description: >-
  Answer questions about a person's own Magic card collection in The Vault - value, gains and losses,
  what they own, what is shared with them, and how a deck fits it. Use for "what is my collection
  worth", "my most valuable cards", "do I own the cards for this deck", price changes, sets or colors
  in the collection, small edits when they bought, sold or traded cards, and bringing in a fresh export from their app.
license: MIT
metadata:
  vault-tools: "whoami get_collection_summary search_cards get_card list_sets get_collection_stats get_collection_breakdowns get_valuation get_value_history get_acquisition_timeline list_card_names check_decklist list_shared_with_me lookup_cards refresh_prices update_owned_cards confirm_owned_cards_update undo_owned_cards_update show_owned_printings import_collection_csv start_collection_upload get_staged_upload confirm_staged_upload list_buckets create_bucket rename_bucket move_cards delete_bucket list_tags tag_cards untag_cards rename_tag delete_tag get_card_metadata set_card_metadata get_bucket_metadata set_bucket_metadata"
---

# Collection analyst

This is the person's own private data plus Scryfall's prices. Report what the tools return; do not
estimate or fill gaps. Follow `vault-attribution` (if installed) for card data and prices.

**Check the connection when something is off.** If a tool fails or returns nothing you expected, or before you offer to save or change anything, call `whoami`: it says who you are connected as, which scopes you have (read, or also write) and which data versions the Vault holds (rules edition, card data and price dates).

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

## Buckets, tags and notes

- **Buckets** are the places copies live in (a binder, a deck box, a trade box): `list_buckets`, and `search_cards` with `bucket`
  answers "what is in my trade binder?". `move_cards` moves copies between buckets and rewrites their folder; a move of more than
  10 copies is shown first, so ask the person before you apply it. `create_bucket`, `rename_bucket`, `delete_bucket` (an empty one).
- **Tags** are the person's own labels on a card (`trade`, `commander-staple`, `deck:sliver`), on the card so every printing has them:
  `list_tags`, `search_cards` with `tag` (each card also lists its `tags`), `tag_cards`, `untag_cards`, `rename_tag`, `delete_tag`.
  A tag you write is recorded as written by this app and shown so: never say the person made it. More than 25 cards at once is shown
  first; ask before you apply it. A copy the Vault could not match to a card can't take a tag.
- **Notes** (metadata) are structured detail about a card or bucket that is not something to filter by (a score, why you suggested a
  card): `get_card_metadata` and `set_card_metadata` (`get_bucket_metadata`, `set_bucket_metadata` for a bucket). You write only your
  own namespace, never the person's `user` one or another app's, and the answer says who wrote what and when.
- A tag is the person's opinion or plan, not what a card does: when asked to "tag my ramp", offer the tags as your suggestions and say
  so; roles (what a card does) are the Vault's reviewed data, not tags.

## Importing a fresh export

When they bring a new export from their app (Dragon Shield, Moxfield, CSV): a file that fits in the chat goes to
`import_collection_csv`; a big one goes through `start_collection_upload` (give them the link) and then
`get_staged_upload` once they say it is uploaded. An import applies only what changed in their app since their last
import and keeps the edits made here.

By default a file is the person's whole collection. If the file is only one binder, box or folder, ask which bucket it is for
(`list_buckets`) and pass `bucket_id` (to `import_collection_csv`, or to `start_collection_upload` when the link is made): the file is
then compared with that bucket only and replaces only it. Every other bucket, with its tags and notes, stays as it is, and the cards
land in that bucket whatever folder the file names. The preview names the bucket and totals what it leaves alone; say both. If the
person did not pick a bucket on the upload page the preview says `bucket: null`: the whole collection.

1. Preview first (no `confirm`). Show three things from `merge`: what their app changed (the only part that is
   applied), the edits made here that are kept, and each conflict.
2. A conflict is a card changed in both places, or dropped by their app but edited here. Ask about each one, one card
   at a time, in their words, with copies at the last import, in their app and here. If they do not choose, the edit
   made here is kept. Send their answers as `use_app_value` (the conflict `id`s where they want their app's value),
   or `conflicts` for all of them.
3. Import with `confirm` (`import_collection_csv`, or `confirm_staged_upload` with the preview's `content_hash`, so only the file they saw is imported) only after they say yes. If they want
   the file to replace everything, use `replace_everything` and say first how many edits made here it discards.
4. Say what the import kept and what it asked, as the preview showed it; do not describe changes it did not make.

## Privacy

The collection is private to its owner. Do not repeat it elsewhere, put it in a public place, or use it to
answer other people's questions. Credit the artist and Scryfall when you show a card image, and never crop it.

## Do not

- Add up values yourself when a tool gives the total, or round away the date.
- Assume a missing price is zero; say it is unknown.
