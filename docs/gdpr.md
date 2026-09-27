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
| `entries` | yes | collection rows, incl. purchase price/date and folders | `collection.csv`, `collection.json` | `purge_user` |
| `decks` | yes | saved decklists | `decks.json`, `decks/*.txt` | `purge_user` |
| `shares` | yes | who shared what with whom | `shares.json` (given and received) | `purge_user` (both directions) |
| `api_sessions` | yes | signed-in apps: client, device name, dates, token hashes | `app_sessions.json` (no hashes) | `purge_user` |
| `access_tokens` | yes | personal access tokens: name, prefix, scopes, dates, hash | `access_tokens.json` (no hashes) | `purge_user` |
| `idempotent_requests` | yes | stored answers to retried POSTs (24 hours) | – (short-lived copies of answers already in the export) | `purge_user` |
| `auth_codes` | yes | one-time sign-in codes for apps (2 minutes) | – (expire in minutes) | `purge_user` |
| `collection_values` | yes | daily market value and cost | `value_history.json` | `purge_user` |
| `cards`, `price_snapshots` | no | public Scryfall card data and prices | – | kept (not about people) |

`tests/test_privacy.py::test_every_table_referencing_users_is_purged` fails if a table
that references `users` is missing from `vault.privacy.personal_data`. When adding a
per-user table:

- add it to `personal_data` (erasure)
- add it to `export_archive` (access and portability)
- add it to the table above and to the privacy notice

## Tenant isolation

- Every endpoint gets the signed-in user from the session and scopes its queries by
  `user_id` (`vault/routes/api.py`).
- Another user's data is reachable only through `/api/shared/{share_id}/…`, which
  checks that the share was granted to the caller (`sharing.incoming_share`).
- Ids that aren't yours answer **404, not 403**, so other tenants' ids can't be probed.
- A deck share gives access to that deck only, never to the owner's collection.
  Coverage for a shared deck is computed against the *viewer's* own collection.
- `tests/test_tenancy.py` covers the cross-tenant cases. Extend it with every new
  endpoint that takes an id.

## Rights, and where they're implemented

| Right (GDPR article) | How |
|---|---|
| Information (13) | `public/privacy.html`, linked from the sign-in screen, the footer and the account panel; `public/credits.html` lists every service and what it receives |
| Access and portability (15, 20) | Account → Download my data → `GET /api/v1/me/export` (ZIP of CSV and JSON) |
| Rectification (16) | edit display name (`PATCH /api/v1/me`); re-import the collection |
| Erasure (17) | Account → Delete my account (download offered first) → `DELETE /api/v1/me` with `{"confirm": "DELETE"}`; also Meta's data-deletion callback for Facebook sign-ins |
| Restriction / objection (18, 21) | by e-mail to the controller (the only processing is providing the service) |

## Operational checklist before going public

- [ ] Fill in the controller name and contact e-mail in `public/privacy.html`.
- [ ] Sign the data processing agreements (DPAs) of Vercel, Neon and GitHub. Their
      standard DPAs cover EU transfers (SCCs / EU-US Data Privacy Framework).
- [ ] Neon point-in-time restore keeps deleted rows until the history window passes.
      Keep the window short (e.g. 7 days) and state it in the privacy notice.
- [ ] Vercel request logs contain IP addresses. Keep the default short retention and
      don't log request bodies.
- [ ] The daily price job runs on GitHub Actions and reads collection rows (not names
      or e-mails). Keep the `DATABASE_URL` secret restricted to that workflow.
- [ ] The browser loads card images and, on "Update now", card lists straight from
      Scryfall, so Scryfall sees the user's IP. This is disclosed in the notice.
      Serving images and prices from the Vault's own `cards` table removes it.
- [ ] Have a breach procedure: the supervisory authority must be notified within 72 hours.
- [ ] Decide on inactive-account retention (for example, warn after 24 months, then delete).
