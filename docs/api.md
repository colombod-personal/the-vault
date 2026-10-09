# The Vault API (v1)

One API for the web app and native apps (the planned iOS app), under `/api/v1`.

- **Discover by links.** `GET /api/v1` returns `_links` to everything the caller may use.
  Every resource carries HAL-style `_links` (`self`, `next`, related resources).
  Clients follow links instead of building URLs.
- **Never a huge response.** Lists come in pages: at most 500 items (default 100), with a
  `next` link carrying an opaque cursor. The cursor is keyset-based, so pages stay stable
  while you walk them. A collection of 20,000 printings is ~22 pages of about 26 KB gzipped each.
  The summary (`/collection`) never lists cards.
- **Cheap to re-check.** Collection resources send an `ETag`. Send it back as `If-None-Match`
  and you get `304 Not Modified` until an import or the daily price sync changes something.
- **Safe retries.** GETs can always be repeated. POSTs that create something (imports, decks,
  shares, access tokens) accept `Idempotency-Key: <uuid>`: a retry with the same key replays the first answer
  (`Idempotent-Replayed: true`) instead of doing the work twice. A retry that arrives while the
  first attempt is still running gets `409` with `Retry-After`. A replayed invite shows the
  same link until the invite is accepted (then 409; the link is derived again from the server's secret, and the stored answer never keeps it), and
  accepting an invite (`/shares/accept`) takes a key too.
  An access token's secret is shown once and never stored, so a retried token creation answers
  `409` with the token's id (in `Location`) instead of making a second one: revoke it and create
  a new one.
- **A version to cache by.** `GET /api/v1/collection` has a `version` that changes whenever the
  collection or its prices change. The web app keeps the answers it got in IndexedDB and asks
  again only when the version moves.
- **For agents.** There are personal access tokens (read, or read and write), OAuth for ChatGPT, Claude and other MCP
  clients (`/oauth/*`, not under `/api/v1`), and an MCP server at `/api/mcp`; `/llms.txt` explains them to an agent.
  See [`agents.md`](agents.md).
- **Errors are `application/problem+json`** (RFC 9457): `{"type", "title", "status", "detail"}`.
  A missing or expired sign-in is `401` with `WWW-Authenticate: Bearer`. Another user's ids
  answer `404`, never `403`.
- **OpenAPI 3.1** at `/api/openapi.json` (interactive at `/api/docs`). Swift clients can be
  generated from it with [swift-openapi-generator](https://github.com/apple/swift-openapi-generator).

## Page shape

```json
{
  "items": [ ... ],
  "count": 100,
  "total": 10645,
  "_links": {
    "self":  {"href": "/api/v1/collection/cards?limit=100"},
    "first": {"href": "/api/v1/collection/cards?limit=100"},
    "next":  {"href": "/api/v1/collection/cards?limit=100&cursor=WyJi..."}
  }
}
```

There is no `next` on the last page. `limit` above 500 is capped, not refused.

## Authentication

The web app uses a session cookie (set by `/api/auth/login/{provider}`). `POST /api/auth/logout`
signs the browser out; `?everywhere=true` signs out every browser on the account (copied cookies
stop working too). Native apps use
**bearer tokens**: `Authorization: Bearer <access_token>`. Bearer requests never use cookies,
so they need no CSRF protection.

| Token | Lifetime | Notes |
|---|---|---|
| access token | 1 hour | opaque |
| refresh token | 60 days | **rotated on every use**; presenting an old one revokes the session (reuse detection) |
| app code | 2 minutes | one-time, bound to a PKCE S256 challenge |

The server stores only SHA-256 hashes of tokens.

### Option A: native SDK sign-in (Apple, Google)

1. The app signs in with *Sign in with Apple* (`ASAuthorizationAppleIDProvider`) or Google
   Sign-In for iOS. It passes a fresh random nonce (Apple: `SHA-256(nonce)` as the request nonce).
   The nonce is required (16-200 characters) and each one signs in once: a replayed ID token is refused.
2. `POST /api/v1/auth/native/{apple|google}`:

   ```json
   {"id_token": "<JWT from the SDK>", "nonce": "<raw nonce>", "name": "Cy Doe", "device_name": "Cy's iPhone"}
   ```

   Apple gives the app the user's name only on the first sign-in; send it as `name`.
3. The server checks the token against the provider's published keys, issuer, audience,
   expiry and nonce. It answers with:

   ```json
   {"access_token": "...", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "...", "session_id": 7}
   ```

   Sent with the app's own bearer token, a new Apple or Google sign-in is linked to that
   account. One that already has its own account moves over if that account is empty (no
   collection, imports, decks, shares, unexpired access tokens or value history), and that account
   is deleted once it has no sign-in left; one whose account holds data answers `409` and nothing
   changes (README → "Sign-in providers"). Personal access tokens can't link sign-ins.

Audiences: `APPLE_APP_BUNDLE_ID` for Apple and `GOOGLE_IOS_CLIENT_ID` for Google. Tokens from the
web sign-in are refused. Google Sign-In for iOS configured with the server (web) client id is
accepted: its tokens carry the web client id as `aud` and the iOS client as `azp`.

### Option B: browser sign-in handed to the app (any provider, including Microsoft and Facebook)

1. The app makes a `code_verifier` (RFC 7636: 43-128 characters from `A-Z a-z 0-9 - . _ ~`) and opens this in `ASWebAuthenticationSession`:
   `/api/auth/login/{provider}?app_redirect_uri=vault://auth&code_challenge=<BASE64URL(SHA256(verifier))>&code_challenge_method=S256`
2. After sign-in the Vault asks the person to confirm ("Sign in to the Vault app?" at
   `/api/auth/app-handoff`), so a sign-in link someone else started can't hand their code to
   another app. Then the browser is sent to `vault://auth?code=...`, or to `?error=...` if it failed
   or they declined (`access_denied`)
   (`identity_in_use` when a signed-in person tries to link a sign-in owned by another account
   that holds data; one from an empty account is moved, as in Option A).
   `app_redirect_uri` must be listed in `APP_REDIRECT_URIS`.
3. `POST /api/v1/auth/token`:

   ```json
   {"grant_type": "authorization_code", "code": "...", "code_verifier": "...", "redirect_uri": "vault://auth"}
   ```

### Passkeys (WebAuthn, web)

Passkeys need no third party: Face ID, Touch ID, Windows Hello, a phone or a security key.
Each ceremony is two POSTs. The challenge is kept on the server (`passkey_challenges`); the
signed session cookie holds only the ceremony's id. A challenge can be claimed once, within 5 minutes.
User verification is required (`userVerification: "required"`): a passkey can be an account's
only way in, so the authenticator must check a PIN or biometric. A credential or assertion without
the UV flag is refused (`400` when creating a passkey, `401` when signing in).

| Step | Endpoint |
|---|---|
| New account | `POST /api/auth/passkey/signup/options {"name"}`, then `.../signup/verify {"credential", "name"}` |
| Sign in (discoverable credentials) | `POST /api/auth/passkey/login/options`, then `.../login/verify {"credential"}` |
| Add a passkey to your account | `POST /api/auth/passkey/register/options`, then `.../register/verify {"credential", "name"}` |
| List / remove | `GET /api/v1/me/passkeys`, `DELETE /api/v1/me/passkeys/{id}` (your last sign-in method can't be removed) |

`credential` is `PublicKeyCredential.toJSON()`. The relying-party id is `BASE_URL`'s host; on
Vercel, a deployment without `BASE_URL` uses its own address. Personal access tokens can't add
passkeys. `GET /api/auth/providers` says whether passkeys are available (`"passkeys": true` on
https and localhost).

### Rate limits

The sign-in endpoints are limited per client IP, per endpoint, in fixed one-minute windows
counted in the database (so they hold across serverless instances; only a keyed hash of the IP
is stored). Over the limit they answer `429` (problem+json) with `Retry-After` in seconds.

| Endpoints | Requests per minute | Setting |
|---|---|---|
| `POST /api/auth/passkey/{signup,register,login}/options`, `GET /api/auth/login/{provider}`, `GET\|POST /api/auth/callback/{provider}`, `POST /api/facebook/data-deletion`, `GET /api/facebook/deletion-status` | 30 | `AUTH_RATE_LIMIT` |
| `POST /api/auth/passkey/{signup,register,login}/verify`, `POST /api/v1/auth/native/{provider}`, `POST /api/v1/auth/token` | 10 | `AUTH_VERIFY_RATE_LIMIT` |
| `POST /api/v1/collection/refresh` (per signed-in user, not per IP) | 20 | `REFRESH_RATE_LIMIT` |
| `POST /api/v1/cards/lookup` with `refresh: true` (per signed-in user; a call to Scryfall for up to 75 printings) | 20 | `LOOKUP_REFRESH_LIMIT` |
| `GET /api/v1/archidekt/decks/{id}`, `POST /api/v1/decks/import-link` and `POST /api/v1/decks/{id}/refresh`: the calls that really go to Archidekt, together (per signed-in user; a read served from the cache is never limited, and over the limit the copy the Vault holds is served, only a deck it holds no copy of is refused) | 30 | `ARCHIDEKT_LIMIT` |
| `POST /api/v1/imports` and `POST /api/v1/imports/preview` together (per signed-in user; up to 20 MB of parsing each) | 10 | `IMPORT_LIMIT` |

Past a limit these answer `429` with `Retry-After`. Reads of different Archidekt decks that are not cached also share one interval for the whole
process (`ARCHIDEKT_INTERVAL`, one second), so a walk over deck ids cannot reach Archidekt faster than `docs/compliance.md` promises. A person
keeps at most 200 saved decks (`MAX_DECKS`); saving one more, by text or by link, answers `409` with "Delete one first".
| `GET\|POST /oauth/authorize`, `POST /oauth/token`, `POST /oauth/revoke` | 120 | `OAUTH_RATE_LIMIT` |
| `POST /oauth/register` | 20 | `OAUTH_REGISTER_RATE_LIMIT` |

A passkey ceremony's challenge lives in the signed session cookie (nothing is written until someone tries to finish it), so
there is no cap on pending ceremonies for a flood to fill: only the per-IP limit above applies to `…/options`. On Vercel the client IP is the first
`x-forwarded-for` entry (or `x-real-ip`), which Vercel's edge sets; elsewhere those headers are
ignored and the connection's address is used.

### Keeping and ending sessions

- Refresh: `POST /api/v1/auth/token` with
  `{"grant_type": "refresh_token", "refresh_token": "..."}`. Store the new refresh token and
  discard the old one.
- Sign out this app: `POST /api/v1/auth/revoke` with its bearer token.
- `GET /api/v1/me/sessions` lists signed-in apps. `DELETE /api/v1/me/sessions/{id}` signs one out remotely.

## Endpoints

| Method | Path | |
|---|---|---|
| GET | `/api/v1` | entry point: links for the caller |
| GET | `/api/v1/auth` | enabled web and native providers, app redirect URIs |
| POST | `/api/v1/auth/native/{provider}` | native ID token → tokens |
| POST | `/api/v1/auth/token` | redeem an app code (PKCE) or rotate a refresh token |
| POST | `/api/v1/auth/revoke` | sign this app out |
| GET / PATCH / DELETE | `/api/v1/me` | profile / change display name / delete account (`{"confirm": "DELETE"}`) |
| GET | `/api/v1/me/export` | everything held about you, as a ZIP (GDPR) |
| GET / DELETE | `/api/v1/me/sessions[/{id}]` | signed-in apps |
| GET / DELETE | `/api/v1/me/apps[/{id}]` | apps connected with OAuth (ChatGPT, Claude, ...): one row per app: name, domain, scopes, number of connections, first connected, last used, idle; disconnect revokes all its connections. Account endpoints: not for tokens, no MCP tool |
| GET | `/.well-known/oauth-protected-resource[/api/mcp]`, `/.well-known/oauth-authorization-server` | OAuth discovery (RFC 9728, RFC 8414) |
| GET / POST | `/oauth/authorize` | OAuth authorization request (PKCE S256, `resource`) and the consent answer; HTML pages, see `agents.md` |
| POST | `/oauth/token`, `/oauth/revoke`, `/oauth/register` | OAuth token endpoint (authorization_code, refresh_token), revocation, dynamic client registration |
| POST / GET / DELETE | `/api/v1/me/tokens[/{id}]` | personal access tokens for agents and scripts (shown once) |
| GET | `/api/v1/collection` | summary: copies, printings, value, cost, dates, links. P&L over the copies with a known cost only (a non-zero price paid recorded): `pnl` (`known_cost_market - known_cost_paid`, null when no cost is known), `pnl_pct`, `known_cost_paid`, `known_cost_market`, `known_cost_copies`, `unknown_cost_copies` (all null when costs are hidden) |
| GET | `/api/v1/collection/buckets` | the places your copies are grouped in (#121, #123): one per folder of your files and "Unsorted", plus the ones you make; each with `kind` (default, folder or made), `position`, `copies`, `entries` (rows), `vault_metadata`; cursor-paged; the buckets' copies add up to the inventory |
| GET | `/api/v1/collection/buckets/{id}` | one bucket; another person's id is 404 |
| GET | `/api/v1/collection/cards?bucket=<id>`, `/collection/export.csv?bucket=<id>`, `/collection/export/{format}?bucket=<id>` | only the copies in one of your buckets (so "what is in my trade binder?" has an answer): the same items, `value_total` and `total` for that bucket alone, or the export of its rows; another person's id, and any bucket on a shared collection, is 404 |
| POST | `/api/v1/collection/buckets` | make a bucket `{name}` (write scope; `Idempotency-Key` honoured): names are unique per person ignoring case (409), at most 100 made by hand (409) |
| PATCH | `/api/v1/collection/buckets/{id}` | rename and/or move in the list `{name?, position?}`; the name is the Vault's label only: `entries.folder`, and so every export, is not rewritten |
| POST | `/api/v1/collection/buckets/{id}/move` | move copies to another of your buckets `{to, lines: [{card_id, quantity}], confirm?}` (card ids from `/collection/cards?bucket=`; write scope; `Idempotency-Key` honoured): splits a stack when part of it moves, merges into an identical row there, rewrites the moved rows' folder to the target's name (the only way an export's folder changes) and is recorded as a change set (`kind: move`; not undoable, move back instead); more than 10 copies is shown first (`applied: false`) until sent again with `confirm: true`; more than the bucket holds is 422, a card not in it 404 |
| DELETE | `/api/v1/collection/buckets/{id}` | delete an empty bucket (409 while it holds copies: move them first, `POST /collection/buckets/{id}/move`) |
| GET | `/api/v1/collection/tags` | your tags (#127), paged by name, `q` filters by text: each with `cards` (cards that have it, owned or not) and `by_source` (how many assignments the person, an assistant or the system wrote). A tag is on the card (its oracle id, so every printing), survives imports, and exists while a card has it: there is nothing to create first |
| GET | `/api/v1/collection/tags/{tag}` | one tag with `owned_cards` (the rest are tagged but not owned) and `assistants` (the apps that wrote it on some cards); 404 when no card has it. Tags are lower case letters, digits, `-` and `:` (1 to 40) |
| POST | `/api/v1/collection/tags/{tag}/cards` | tag cards `{card_ids, confirm?}` (ids from `/collection/cards`; write scope; `Idempotency-Key` honoured): answers what was added, what was already tagged and how many assistant tags the person accepted (`accepted`: tagging a card whose tag an assistant wrote makes the assignment the person's), and `written_by` (`person`, or `assistant` when an OAuth app or personal token wrote it, recorded with its name). More than 25 cards is only shown (`applied: false`) until sent again with `confirm: true`. At most 50 tags per card and 500 different tags per person (409); a copy the Vault could not match to a card can't take a tag (422) |
| POST | `/api/v1/collection/tags/{tag}/cards/remove` | take the tag off cards `{card_ids, confirm?}` (same shape and confirmation as tagging; the cards stay) |
| PATCH | `/api/v1/collection/tags/{tag}` | rename on every card `{name}`; a card that already has the new name keeps one |
| DELETE | `/api/v1/collection/tags/{tag}` | remove the tag from every card (the cards stay) |
| GET | `/api/v1/collection/cards?tag=<tag>` | only the cards you tagged (every printing); each card item also lists its `tags` on your own collection. Not on a shared collection: tags are private (404 with `tag`, no `tags` field) |
| GET | `/api/v1/collection/cards` | printings, paged. Filters: `q`, `set`, `name`, `finish`, `condition`. `printing` (the printing label: `Normal`, `Foil`, `Etched`, …, any case). `type`: the main type the breakdowns and `/collection/names` use (`Creature`, `Land`, `Artifact`, `Enchantment`, `Instant`, `Sorcery`, `Planeswalker`, `Battle`, `Other`; any case; a card has one, so an Artifact Creature is a Creature). `mana_value`: the breakdowns' bucket (`0` to `7`, `8+`; a fraction counts down). Any other value is a 400 that lists the allowed ones. Printings whose card data is not stored yet are left out of both filters, never guessed. `sort`: `name`, `-name`, `-value`, `value`, `-quantity`, `set`, `-acquired`, `acquired` (first bought first; undated last), `mana_value`, `-mana_value` (cheapest or dearest to cast first; printings without card data last). `value_total` is the market value of every printing matching the filters, across all pages. Each printing has `paid` (total paid), `paid_quantity` (how many copies that covers; copies with no price recorded are left out) and `gain` (today's value of those copies minus `paid`; `null` when no cost is known), all `null` when costs are hidden. Each printing also carries `card`: Scryfall's data for it (type line, colours, mana cost, mana value, rarity, layout, power/toughness/loyalty, oracle text, image with artist credit, every finish's latest price), kept in the Vault's card table by the daily sync and read for the whole page in one query; `null` until the printing is matched and synced |
| GET | `/api/v1/collection/cards/{id}` | one printing: the rows it came from (the first 500; `copies_total` counts all), card data, image with artist credit, 90-day price history |
| GET | `/api/v1/collection/sets` | value by set, paged. `q` (text in the code or name); `sort`: `-value` (default), `value`, `-quantity`, `quantity`, `-unique`, `unique` (distinct printings), `name`, `code`, `release`, `-release` (by Scryfall's release date; unknown dates last; may answer 503 while Scryfall's set list is unavailable). Each set has `colors` (copies by colour identity: `W` `U` `B` `R` `G`, `M` multicolour, `C` colourless, `unknown`) and `released_at` when the Vault has the set list |
| GET | `/api/v1/collection/timeline` | per month: copies acquired, their market value today, and what was paid (null when costs are hidden) |
| GET | `/api/v1/collection/history` | daily market value and cost, paged (`since`); `imported` marks the days a file was imported |
| GET | `/api/v1/collection/stats` | most valuable, biggest gains and losses (only copies with a recorded price paid count), duplicates (`most_copies`, each with `printings` and `market_value`: the stockpiles). `limit` items per list, 1–50 (default 8) |
| GET | `/api/v1/collection/breakdowns` | copies, distinct printings and market value by colour identity, main type, mana value (`0`–`7`, `8+`), rarity, and colour × type (`matrix`). Fixed buckets, zeros included; printings not in the card table yet count as `unknown` (see Analytics below) |
| GET | `/api/v1/collection/valuation` | per purchase month (the timeline's months): copies, their market value today, spend, and running `copies_cum`, `market_cum`, `cost_cum`, `gain_cum` (market − cost), `known_gain_cum` (copies with a known cost only); `totals`, `peak` (the month whose copies are worth the most), `trailing_12m` (the 12 calendar months up to the latest month bought), `undated` copies. Costs null when hidden |
| GET | `/api/v1/collection/names` | one row per card name (case-insensitive), paged: copies, market value, unit price, printings, sets, colour (`W`…`G`, `M`, `C`, `unknown`), colour identity, main type, type line, mana value, rarity, and the most valuable printing's image with its artist. `sort`: `-value` (default), `value`, `-quantity`, `quantity`, `name`, `-name`; `colors` (comma-separated; a multicolour card matches any of its colours, `M` every multicolour card), `type`, `min_value`; `limit` for the top N |
| POST | `/api/v1/collection/refresh` | refresh your printings' card data and today's prices from Scryfall, up to 300 per call (batches of 75 through the server's rate-limited client), then recompute today's value, so the collection's `version` changes. Body (optional): `{"cursor"?, "force"?}`. Answers `{done, total, remaining, processed, not_found, unmatched_rows, cursor, unavailable, prices_as_of, version}`: call again with `cursor` until `remaining` is 0. Printings that already have today's price are skipped unless `force`. 503 with `Retry-After` when Scryfall doesn't answer; limited per user (`REFRESH_RATE_LIMIT`); write scope |
| GET | `/api/v1/collection/exports` | export formats (Dragon Shield, Moxfield, Archidekt, generic CSV, text list), each with a download link |
| GET | `/api/v1/collection/export/{format}` | the collection in that format. Other apps get Scryfall set codes and numbers for matched printings |
| GET | `/api/v1/collection/export.csv` | Dragon Shield CSV, byte-identical to a Dragon Shield import |
| POST / GET | `/api/v1/imports` | upload a Dragon Shield, Moxfield or generic CSV, detected from the header (multipart `file`, 201; the import's `source` says which; 409 if another import of the collection finished while this one ran). It applies only what changed in the person's app since the last import and keeps edits made in the Vault; `merge` in the answer says what was applied, kept and asked. Query: `replace_everything` (the old behaviour), `conflicts` (`vault` default or `app`), `use_app_value` (conflict ids to take the app's value for; repeatable) / list imports with changes (an entry made through an assistant has `kind` `assistant`, its `app` and `lines`, and `undoable`: true on the one that can be undone now, `undone` once it was; `POST /api/v1/collection/changes/undo` previews, then applies with the `confirmation`) |
| POST | `/api/v1/imports/preview` | the same, changing nothing: `changes` (what happens to the collection) and `merge` (what comes from the app, the Vault edits kept, the conflicts with their ids and the answer each gets) |
| GET | `/api/v1/imports/{id}` | one import |
| POST | `/api/v1/decks/parse` | parse a pasted decklist |
| POST | `/api/v1/decks/coverage` | owned / partial / missing per card, with `unit_price` (the cheapest known USD price of the line's printing when it names one; otherwise the card's cheapest priced paper printing from `oracle_prices`, the same figure `shopping_list` and `get_card_oracle` give, so a deck never has two prices for one card (#328); with no such price loaded, the cheapest of the printings the Vault holds, in any finish), `price_date` (the day of the snapshot that price came from, null with no price), `missing_cost`, `owned_printings`, and `maybe_owned` (on a line you own none of: collection cards whose name differs only in accents, punctuation, case or an Alchemy "A-", each `{name, quantity}`; counting stays by exact name); the deck's `missing_cost` and `missing_unpriced` (lines with missing copies and no known price). `GET /decks/{id}` and `GET /shared/{id}/deck` carry the same |
| GET | `/api/v1/decks/overlap`, `/decks/overlap/decks`, `/decks/overlap/contested`, `/decks/overlap/purchases` | can the saved decks all be built at the same time from the copies you own (#165, `docs/deck-independence.md`; by card name, any printing owned counts, basic lands left out). The root keeps `decks_checked`, `shared_cards`, `short_cards` and `cards` (the first 200 cards in more than one deck, each with a preview of at most 10 decks and `decks_total`) and adds `summary` (`decks_analysed`, `decks_needing_purchase`, `finish_all_cost` with each card counted once, `unpriced`, `contested_cards`, `cards_to_buy`), `allocation` (the rule used), `decks_analysed`, `decks_skipped_count`, `prices_date` and the first page of `decks` (`limit`, default 25; `_links.next` pages the rest). Each deck record has its allocation `order`, `need`, `free`, `holds` (contested cards it is given, `also_wanted_by`), `lacking` (each copy `not_owned`, to buy, or `held_by_other_deck`, available by moving, with `unit_price`, `price_date`, `cost`), `stands_alone`, `independent`, `independence` and `cost_to_complete`; a deck whose saved list cannot be read is a `status: "skipped"` record with a `reason`. A card is contested when two or more decks use it and the collection holds some copies, but fewer than they need together; a card owned zero times is not contested. Contested copies go to decks in order: the deck closest to complete first, or `priority` (deck ids, comma-separated or repeated; an id that is not yours answers 404). The three lists that grow are subresources with their own `limit`, `cursor` and `next`, computed across every deck before a page is cut: `decks` (allocation order), `contested` (each card with its `move` and `buy` options; `move` only when a deck lacks a copy another deck holds) and `purchases` (cheapest first, unpriced last; `format=text` returns one page of the paste-ready list as `text`, join the pages). Prices are Scryfall's cheapest, dated. Limited per person (60 a minute) |
| GET / POST | `/api/v1/decks` | saved decks (`?summary=true` adds each deck's `summary` against your collection: `need`, `have`, `missing`, `missing_cost`, `missing_unpriced`) / save one |
| GET / PUT / DELETE | `/api/v1/decks/{id}` | a deck with coverage / update (an omitted `source_url` or `source_author` is kept; a new `source_url` without `source_author` clears it) / delete |
| GET | `/api/v1/decks/{id}/versions`, `/api/v1/decks/{id}/versions/{version_id}` | a saved deck's versions, newest first, each with its date, where it came from (`saved`, `edited`, `imported`, `refreshed`) and the cards that changed from the version before (`changes`, `summary`; null for the oldest kept), at most 20 kept (`keep`); and one older list as it was (`text`). A version is recorded when the deck's cards change, never for a rename, a reordering or another spelling of the same cards. `GET /decks/{id}` carries `last_change`, the latest version against the one before it (absent with one version; at most 40 changes are listed, with `more_changes` saying how many more and `summary` the true totals, #327), also in the `get_deck` tool (#93) |
| POST | `/api/v1/decks/{id}/seen` | the person opened the deck page: body `{"text": ...}` (optional) is the list the page showed (for a deck from a link, the source's current list); if its cards differ from the latest version it is recorded (`source` "opened"); answers `recorded` and `since_last_looked` (what changed since they last opened it: `changes`, `summary`, `since`; null the first time and when nothing changed), then marks the latest version as seen. The saved copy is never replaced by looking (#93) |
| POST | `/api/v1/decks/{id}/source-author` | record `source_author` (not blank) on a deck saved before authors were kept: only while its link is still `source_url` and it has no author; answers the deck with `recorded` (false otherwise); not an edit. Agents set the author with `save_deck`/`update_deck` |
| GET | `/api/v1/archidekt/decks/{id}` | a public Archidekt deck, fetched server-side on a person's request, read-only (see `docs/compliance.md`). Answers are cached 10 minutes: a `vault_cache` block says `fetched_at`, `age_seconds` and whether it was a hit; `?refresh=true` asks Archidekt again (and a copy under about a minute old is reused). Returns the deck, an `overview`, counts, the text, the credit (source, link, author, `fetched_at`, notice) and a note, no shop prices. A saved deck from Archidekt carries the same credit with `fetched_at` = when the Vault last took its list from the link, in `GET /decks/{id}` and in the `deck` block of every deck analysis answer |
| POST | `/api/v1/cards/lookup` | any card, owned or not: `{"identifiers": [{"id"} \| {"set", "collector_number"} \| {"name", "set"?}], "refresh"?}`, 1–75 of them. Answers in Scryfall's card shape (`data`, `not_found`) from the Vault's own card table, fetching misses from Scryfall once. `unavailable: true` means Scryfall was needed but didn't answer: retry later. Read scope |
| GET | `/api/v1/catalog/sets` | every Magic set with its icon, paged by set code, plus `aliases` mapping Dragon Shield set codes (e.g. `gk2_orzhov`) to Scryfall ones (needs sign-in: no anonymous catalog, #62; `Cache-Control: private`) |
| POST / GET | `/api/v1/shares` | create a one-time invite link (web or app sign-in only, not personal access tokens) / list what you share |
| DELETE | `/api/v1/shares/{id}` | revoke (owner) or leave (recipient) |
| POST | `/api/v1/shares/accept` | `{"token"}` from an invite link |
| GET | `/api/v1/shared` | what others share with you |
| GET | `/api/v1/shared/{id}/collection[/…]` | a shared collection, read-only. Same sub-resources as `/collection` except the exports (`exports`, `export/{format}`, `export.csv`). Prices paid are hidden unless the owner allowed them |
| GET | `/api/v1/shared/{id}/deck` | a shared deck, checked against your collection |

Outside v1: `POST /api/mcp` (the MCP server for agents, [`agents.md`](agents.md)), plus the web and provider callbacks: `/api/auth/*` (browser sign-in),
`/api/facebook/data-deletion` (Meta's callback) and `/api/health`.

## Analytics

The owner's decision: analytics are computed by the server, in Postgres, and clients only render them. `vault/analytics.py` prices every row in SQL exactly as the collection view does (the latest Scryfall price for the row's finish, else the file's own market price), joins it to the card table, and aggregates. Rows whose printing isn't in the card table yet are counted as `unknown`. These endpoints carry an ETag from the same version as the rest of the collection.

The web app follows this: every number it shows comes from these endpoints (and `/collection`,
`/cards`, `/sets`, `/stats`, `/history`, `/decks/coverage`); it only lays them out. It starts
`POST /collection/refresh` itself, chunk by chunk until `remaining` is 0 and waiting out `503` and
`429` as `Retry-After` says: after an import, and once per visit when `prices_as_of` is older than
today or some printings have no card data yet. "Update now" runs the same loop.

- **Known cost**: a copy's cost is known when a non-zero price paid was recorded for it. P&L compares what was paid against today's value of those same copies; the other copies are counted (`unknown_cost_copies`), never treated as free. Hidden costs (shared collections) make every cost field null.
- **Colour**: the colour identity: one colour is that colour, two or more `M`, none `C`.
- **Main type**: the front face's card types (the type line before ` // `, then before ` — `), and the first of Creature, Planeswalker, Battle, Land, Instant, Sorcery, Artifact, Enchantment found there; otherwise `Other`. So Legendary, Artifact and Enchantment Creatures are Creatures, an Artifact Land is a Land, and double-faced and split cards are typed by their front face (left half).
- **Mana value**: whole numbers 0–7, then `8+` (half points round down).

## Attribution in clients

Card data carries what a client needs to credit its sources: `image.artist`, `image.credit`,
and a `scryfall` link on each card. Native apps must show these the way the web app does:
artist and copyright next to card images, and a Credits screen. See README → "Attribution".

## Server notes

Clients never call Scryfall's API: card data and set lists come from the Vault (`/cards/lookup`,
`/catalog/sets`), which uses one rate-limited Scryfall client per process and stores what it
fetches. Images are hotlinked from Scryfall's image CDN, as Scryfall asks.

Collection resources are built from one per-user view. It is cached in-process, keyed by a
version: user, latest import, latest price day, latest card update, and whether costs are hidden.
Paging through a big collection therefore costs one build, not one per page. Each request
checks the version, so stale data is never served. Each serverless instance keeps its own
cache of 16 views.
