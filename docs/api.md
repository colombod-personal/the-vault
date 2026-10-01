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
  same link (derived again from the server's secret; the stored answer never keeps it), and
  accepting an invite (`/shares/accept`) takes a key too.
  An access token's secret is shown once and never stored, so a retried token creation answers
  `409` with the token's id (in `Location`) instead of making a second one: revoke it and create
  a new one.
- **A version to cache by.** `GET /api/v1/collection` has a `version` that changes whenever the
  collection or its prices change. The web app keeps a copy in IndexedDB and refetches the
  pages only when the version moves.
- **For agents.** There are personal access tokens (read, or read and write) and an MCP server at
  `/api/mcp`; `/llms.txt` explains both to an agent. See [`agents.md`](agents.md).
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

The web app uses a session cookie (set by `/api/auth/login/{provider}`). Native apps use
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
   account. One already used by another account answers `409`. Personal access tokens can't
   link sign-ins.

Audiences: `APPLE_APP_BUNDLE_ID` for Apple and `GOOGLE_IOS_CLIENT_ID` for Google. Tokens from the
web sign-in are refused. Google Sign-In for iOS configured with the server (web) client id is
accepted: its tokens carry the web client id as `aud` and the iOS client as `azp`.

### Option B: browser sign-in handed to the app (any provider, including Microsoft and Facebook)

1. The app makes a `code_verifier` and opens this in `ASWebAuthenticationSession`:
   `/api/auth/login/{provider}?app_redirect_uri=vault://auth&code_challenge=<BASE64URL(SHA256(verifier))>&code_challenge_method=S256`
2. After sign-in the browser is sent to `vault://auth?code=...`, or to `?error=...` if it failed
   (`identity_in_use` when a signed-in person tries to link a sign-in owned by another account).
   `app_redirect_uri` must be listed in `APP_REDIRECT_URIS`.
3. `POST /api/v1/auth/token`:

   ```json
   {"grant_type": "authorization_code", "code": "...", "code_verifier": "...", "redirect_uri": "vault://auth"}
   ```

### Passkeys (WebAuthn, web)

Passkeys need no third party: Face ID, Touch ID, Windows Hello, a phone or a security key.
Each ceremony is two POSTs. The challenge is kept on the server (`passkey_challenges`); the
signed session cookie holds only the ceremony's id. A challenge can be claimed once, within 5 minutes.

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
| POST / GET / DELETE | `/api/v1/me/tokens[/{id}]` | personal access tokens for agents and scripts (shown once) |
| GET | `/api/v1/collection` | summary: copies, printings, value, cost, dates, links |
| GET | `/api/v1/collection/cards` | printings, paged. Filters: `q`, `set`, `name`, `finish`, `condition`. `sort`: `name`, `-value`, `value`, `-quantity`, `set`, `-acquired` |
| GET | `/api/v1/collection/cards/{id}` | one printing: copies, card data, image with artist credit, 90-day price history |
| GET | `/api/v1/collection/sets` | value by set, paged |
| GET | `/api/v1/collection/timeline` | copies acquired per month |
| GET | `/api/v1/collection/history` | daily market value and cost, paged (`since`); `imported` marks the days a file was imported |
| GET | `/api/v1/collection/stats` | most valuable, biggest gains and losses, duplicates |
| GET | `/api/v1/collection/exports` | export formats (Dragon Shield, Moxfield, Archidekt, generic CSV, text list), each with a download link |
| GET | `/api/v1/collection/export/{format}` | the collection in that format. Other apps get Scryfall set codes and numbers for matched printings |
| GET | `/api/v1/collection/export.csv` | Dragon Shield CSV, byte-identical to a Dragon Shield import |
| POST / GET | `/api/v1/imports` | upload a Dragon Shield, Moxfield or generic CSV, detected from the header (multipart `file`, 201; the import's `source` says which) / list imports with changes |
| GET | `/api/v1/imports/{id}` | one import |
| POST | `/api/v1/decks/parse` | parse a pasted decklist |
| POST | `/api/v1/decks/coverage` | owned / partial / missing per card |
| GET / POST | `/api/v1/decks` | saved decks / save one |
| GET / PUT / DELETE | `/api/v1/decks/{id}` | a deck with coverage / update (an omitted `source_url` is kept) / delete |
| GET | `/api/v1/archidekt/decks/{id}` | a public Archidekt deck, fetched server-side |
| POST | `/api/v1/cards/lookup` | any card, owned or not: `{"identifiers": [{"id"} \| {"set", "collector_number"} \| {"name", "set"?}], "refresh"?}`, 1–75 of them. Answers in Scryfall's card shape (`data`, `not_found`) from the Vault's own card table, fetching misses from Scryfall once. `unavailable: true` means Scryfall was needed but didn't answer: retry later. Read scope |
| GET | `/api/v1/catalog/sets` | every Magic set with its icon, paged by set code, plus `aliases` mapping Dragon Shield set codes (e.g. `gk2_orzhov`) to Scryfall ones (public, cached for a day) |
| POST / GET | `/api/v1/shares` | create a one-time invite link / list what you share |
| DELETE | `/api/v1/shares/{id}` | revoke (owner) or leave (recipient) |
| POST | `/api/v1/shares/accept` | `{"token"}` from an invite link |
| GET | `/api/v1/shared` | what others share with you |
| GET | `/api/v1/shared/{id}/collection[/…]` | a shared collection, read-only. Same sub-resources as `/collection` except `export.csv`. Prices paid are hidden unless the owner allowed them |
| GET | `/api/v1/shared/{id}/deck` | a shared deck, checked against your collection |

Outside v1: `POST /api/mcp` (the MCP server for agents, [`agents.md`](agents.md)), plus the web and provider callbacks: `/api/auth/*` (browser sign-in),
`/api/facebook/data-deletion` (Meta's callback) and `/api/health`.

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
