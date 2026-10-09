# GDPR and multi-tenancy

How the Vault handles personal data, and what to keep true when changing it.
This is an engineering document, not legal advice: have the privacy notice
(`public/privacy.html`) reviewed before opening the app to other people.

## Principles

1. **Private by default.** A user's collection, imports, decks and value history
   are visible only to that user.
2. **Sharing is explicit and revocable.** The owner creates a one-time invite link
   for their collection or for one deck. The recipient accepts it while signed in,
   and access is bound to their account. The owner can revoke it and the recipient
   can leave. Prices paid stay hidden unless the owner opts in.
3. **Everything can be exported and erased.** "Download my data" returns the whole
   account as a ZIP. "Delete my account" offers that download first, then removes
   every row.

## Data map

| Table | Personal? | Contents | Export | Erasure |
|---|---|---|---|---|
| `users` | yes | display name, e-mail (from the sign-in provider) | `account.json` | `purge_user` |
| `identities` | yes | provider, provider user id, e-mail | `account.json` | `purge_user` |
| `imports` | yes | file name, date, change summary | `imports.json` | `purge_user` |
| `collection_baselines` | yes | the cards (copies, condition, folder, price and date paid) of the last file you imported, so the next import can tell what changed in your app from what was edited here | `last_import_cards.json` | `purge_user` |
| `bucket_baselines` | yes | the same record as `collection_baselines`, for a file imported into one bucket (#124): the cards of the last file imported into that bucket, so a re-import of that bucket applies only what changed in your app; one per bucket, replaced by the next import into it, forgotten by the next whole-collection import | `last_import_cards.json` (`by_bucket`) | `purge_user` (before the buckets), and with its bucket (`ON DELETE CASCADE`) |
| `entries` | yes | collection rows, incl. purchase price/date and folders, and the bucket each copy is in | `collection.csv` (Dragon Shield), `collection-moxfield.csv`, `collection-generic.csv`, `collection.json` | `purge_user` |
| `buckets` | yes | the places your copies are grouped in (#121): one per folder of your files, and "Unsorted"; the name, its kind and position, and notes (`vault_metadata`) written by you or an assistant | `buckets.json` (with how many copies each holds) | `purge_user` (after the entries) |
| `tag_assignments` | yes | tags you, or an assistant you allowed, put on cards (keyed by the card, not a printing): the card's id, the tag, who wrote it (person, assistant or system, and which app) and notes (`vault_metadata`) | `tags.json` | `purge_user` |
| `card_annotations` | yes | notes about a card written by you or an assistant (`vault_metadata`, at most 8 KB), keyed by the card | `card_annotations.json` | `purge_user` |
| `reset_snapshots` | yes | the undo of the latest reset of the collection or of one bucket (#129): the copies it removed (every column of each row, with its bucket and folder), the baselines of that scope and, when the reset cleared them, the tags, notes and import history it removed, as zlib-compressed JSON; one per person (the next reset replaces it), at most 20 MB of JSON, **valid 7 days** | `last_reset.json` (the removed rows readable, while it exists) | deleted by the daily retention job when its 7 days are over, when the undo is used, when the next reset replaces it, and by `purge_user` |
| `decks` | yes | saved decklists, with the link and author of the deck they came from | `decks.json`, `decks/*.txt` | `purge_user` |
| `deck_versions` | via its deck | each saved deck's earlier lists (at most 20 a deck): the decklist text, when and from what | `decks.json` (`versions` of each deck) | deleted with the deck (`ON DELETE CASCADE`), so with the account |
| `shares` | yes | who shared what with whom | `shares.json` (given and received) | `purge_user` (both directions) |
| `api_sessions` | yes | signed-in apps: client, device name, dates, token hashes | `app_sessions.json` (no hashes) | `purge_user` |
| `retired_refresh_tokens` | yes | hashes of already-rotated app refresh tokens, kept until they would expire, to detect a copied token | – (hashes only) | `purge_user`, and with their app session |
| `passkeys` | yes | WebAuthn credentials: public key, name, dates (the private key never leaves the person's device) | `passkeys.json` (names and dates) | `purge_user` |
| `oauth_grants` | yes | one row per connection of an app the person connected with OAuth (ChatGPT, Claude, ...; each device or re-add is one): client id, scopes allowed, resource, dates, and hashes of the current access and refresh token | `connected_apps.json` (one entry per connection, no hashes) | `purge_user`; disconnecting an app deletes all its rows; a connection nobody refreshes stops working after 30 days and its row is deleted the next time the person opens Connected apps or anyone connects an app |
| `oauth_retired_refresh_tokens` | yes | hashes of already-rotated OAuth refresh tokens, kept until they would expire, to detect a copied token | – (hashes only) | `purge_user`, and with their grant |
| `oauth_consents` | yes | a consent screen shown and not yet answered: hash of a one-time nonce, the authorization request (client, redirect URI, scopes), 10 minutes | – (expire in minutes) | `purge_user`; deleted when answered or expired |
| `oauth_codes` | yes | one-time authorization codes (hash, client, redirect URI, challenge; 60 seconds) | – (expire in a minute) | `purge_user`; deleted when expired |
| `oauth_clients` | no | what an AI app said about itself: its client id (a metadata URL or a registered id), name, redirect URIs. No person is named | – | deleted when unused and expired (a day for registrations and cached documents) |
| `access_tokens` | yes | personal access tokens: name, prefix, scopes, dates, hash | `access_tokens.json` (no hashes) | `purge_user` |
| `idempotent_requests` | yes | stored answers to retried POSTs (24 hours) | – (short-lived copies of answers already in the export) | `purge_user` |
| `staged_uploads` | yes | a collection file an assistant asked the person to upload (the file itself, its name, when, and the bucket it goes into, if any), until it is applied or its link expires (one hour); only a hash of the link is stored | – (the person's own file, gone within the hour; the import it becomes is in the export) | applied, deleted when expired (the daily job, and whenever someone starts a link), `purge_user` |
| `auth_codes` | yes | one-time sign-in codes for apps (2 minutes) | – (expire in minutes) | `purge_user` |
| `collection_values` | yes | daily market value and cost | `value_history.json` | `purge_user` |
| `native_nonces` | no | hashes of used native sign-in nonces and Facebook data-deletion requests, until they would expire | – | deleted when expired |
| `passkey_challenges` | no | a passkey ceremony someone tried to finish (random id, five minutes; the challenge itself is in the session cookie), naming no one | – | deleted once expired |
| `rate_hits` | no | per-minute request counts to the sign-in endpoints, keyed by a keyed hash of endpoint and IP (no IP address stored) | – | deleted after a few minutes |
| `cards`, `price_snapshots` | no | public Scryfall card data and prices | – | kept (not about people) |

`tests/test_privacy.py::test_every_table_referencing_users_is_purged` fails if a table
that references `users` is missing from `vault.privacy.personal_data`. When adding a
per-user table:

- add it to `personal_data` (erasure)
- add it to `export_archive` (access and portability)
- add it to the table above and to the privacy notice

## Resetting the collection versus erasing the account

A **reset** (Account → Reset collection, `POST /api/v1/collection/reset`, the MCP tool `reset_collection`; #129) empties the whole
inventory or one bucket. It is not erasure: the account, the buckets (left empty), the saved decks, the shares, the sign-in methods and
the connected apps stay; tags, notes and the import history stay unless the person asks to clear them (they are the person's own work).
What it removes is the copies (`entries`) of its scope, and their baseline: it is an import with an empty target in replace mode
(`docs/collections.md`, decision 6), added to the import history as an entry of its own kind (`reset`).

To make that undoable the reset keeps **one snapshot per person** in `reset_snapshots`, for **7 days**, and only the latest (the next
reset replaces it; the undo uses it up). The snapshot holds the same personal data the removed copies held, so it is listed in the data map,
exported (`last_reset.json`), erased with the account, and deleted by the daily retention job (`vault/retention.py`) when its 7 days
are over, whether or not anyone asked. A person who wants the copies gone at once uses `no_undo` (nothing is kept) or deletes the
account; the 7 days are stated in the privacy notice. Database backups (Neon point-in-time restore) still hold deleted rows for their
own window, as for any deletion (see the checklist below). **Erasure** (`DELETE /api/v1/me`) removes every row, the snapshot included.

## Tenant isolation

- Every endpoint gets the signed-in user (session or token) and scopes its queries by
  `user_id` (`vault/api/v1.py`).
- Another user's data is reachable only through `/api/v1/shared/{share_id}/…`
  (`/collection/…` or `/deck`), which checks that the share was granted to the caller
  (`sharing.incoming_share`).
- Ids that aren't yours answer **404, not 403**, so other tenants' ids can't be probed.
- A deck share gives access to that deck only, never to the owner's collection.
  Coverage for a shared deck is computed against the *viewer's* own collection.
- `tests/test_tenancy.py` covers the cross-tenant cases. Extend it with every new
  endpoint that takes an id.

- OAuth grants are listed (grouped by app) and revoked by their owner only (`/api/v1/me/apps`, 404 otherwise) and, like personal access
  tokens, never carry account-level powers (`docs/mcp-oauth-threat-model.md`).

## Rights, and where they're implemented

| Right (GDPR article) | How |
|---|---|
| Information (13) | `public/privacy.html`, linked from the sign-in screen, the footer and the account panel; `public/credits.html` lists every service and what it receives |
| Access and portability (15, 20) | Account → Download my data → `GET /api/v1/me/export` (ZIP of CSV and JSON). Account → Move your collection exports to Dragon Shield, Moxfield, Archidekt, generic CSV or text, so people can switch apps |
| Rectification (16) | edit display name (`PATCH /api/v1/me`); re-import the collection |
| Erasure (17) | Account → Delete my account (download offered first) → `DELETE /api/v1/me` with `{"confirm": "DELETE"}`; also Meta's data-deletion callback for Facebook sign-ins (deletes the account when Facebook is its only sign-in, otherwise only the Facebook link and the e-mail it brought; requests older than an hour are refused); and linking a sign-in that has its own *empty* account to another account (README → "Sign-in providers") deletes that empty account once it has no sign-in left, through the same `personal_data` statements |
| Restriction / objection (18, 21) | by e-mail to the controller (the only processing is providing the service) |

## Operational checklist before going public

- [ ] Fill in the controller name and contact e-mail in `public/privacy.html`.
- [ ] Sign the data processing agreements (DPAs) of Vercel, Neon and GitHub. Their
      standard DPAs cover EU transfers (SCCs / EU-US Data Privacy Framework).
- [ ] Neon point-in-time restore keeps deleted rows until the history window passes.
      Keep the window short (e.g. 7 days) and state it in the privacy notice.
- [ ] Vercel request logs contain IP addresses. Keep the default short retention and
      don't log request bodies.
- [ ] The daily price job runs on GitHub Actions and reads collection rows (card names,
      sets, collector numbers, quantities and prices), not account names or e-mail addresses.
      Keep the `DATABASE_URL` secret restricted to that workflow.
- [ ] The browser loads card images and set icons from Scryfall's CDN, so Scryfall sees the
      user's IP. This is disclosed in the notice. Card data and prices come from the Vault
      (it calls Scryfall's API itself, without user data).
- [ ] The recipients of data, read from the code on 2026-10-08 (`grep` for the hosts in `vault/` and `jobs/`), and where the privacy
      notice names each: Vercel (hosting, request logs, Web Analytics and Speed Insights, which are anonymous and cookieless; the
      DPA covers them), Neon, GitHub (the price job), the four sign-in providers in `vault/auth.py` (Google, Microsoft, Apple,
      Facebook) and passkeys (no third party), Scryfall (card images in the browser; identifiers only from the server),
      Commander Spellbook (`vault/combos.py` sends a deck's card names and nothing that identifies the person), Archidekt
      (a GET of a public deck by number) and the AI assistant the person connects (their own choice, scoped, revocable).
      `tests/test_legal_pages.py` fails if the notice stops naming one of them. Add any new outbound host here and in the notice.
- [ ] Have a breach procedure: the supervisory authority must be notified within 72 hours.
- [ ] Decide on inactive-account retention (for example, warn after 24 months, then delete).
