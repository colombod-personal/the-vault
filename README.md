# The Vault

A Magic: The Gathering collection manager and valuation app. Import your
Dragon Shield export, see what it's worth, track value over time, and check
decks against what you own.

- **Server:** Python / FastAPI, built on [mtg-toolkits](https://github.com/colombod-personal/mtg-toolkits)
  (CSV parsing, Scryfall matching, deltas, decklists).
- **Front end:** the React prototype from the design handoff, in `public/`,
  now loading data from the server (see `docs/prototype-handoff.md`).
- **Sign-in:** Google, Microsoft, Apple and Facebook (OAuth 2 / OpenID Connect via Authlib).
- **Storage:** Postgres in production (Neon via the Vercel Marketplace), SQLite locally.
- **Prices:** a daily GitHub Actions job downloads Scryfall's bulk file, matches every
  collection offline and stores that day's prices, so the value chart is real history.
- **Privacy:** multi-tenant and private by default. Users share their collection or a deck
  with someone through a one-time invite link, and can revoke it. Everyone can download all
  their data and delete their account, which removes every row (see `docs/gdpr.md`).

## Run it locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # DEV_LOGIN=1 gives you a "Local dev sign-in" button
set -a && . ./.env && set +a
uvicorn --factory vault.app:create_app --reload --port 8000
# open http://localhost:8000, sign in, import your Dragon Shield CSV
```

Get prices without waiting for the daily job:

```bash
python -m jobs.sync_prices      # downloads Scryfall's default_cards file (~75 MB)
```

Tests: `pytest`

## Layout

```
api/index.py          Vercel entry point (all /api/* routes)
vault/app.py          FastAPI app factory, sessions, static files for local dev
vault/auth.py         Google / Microsoft / Apple / Facebook sign-in, account linking
vault/models.py       users, identities, imports, entries, cards, price_snapshots, collection_values
vault/importer.py     Dragon Shield CSV -> entries (records what changed since the last import)
vault/vault_json.py   builds the collection.json the front end reads
vault/sync.py         daily Scryfall sync (offline matching, prices, per-user value)
vault/routes/api.py   collection, imports, export, decks, sharing, account, Facebook data deletion
vault/sharing.py      invite links, access checks for shared collections and decks
vault/privacy.py      GDPR data export (ZIP) and account erasure (purge_user)
jobs/sync_prices.py   CLI run by .github/workflows/sync-prices.yml
public/               front end (React prototype, no build step yet)
```

## API

| Method | Path | |
|---|---|---|
| GET | `/api/auth/providers` | enabled sign-in providers |
| GET | `/api/auth/login/{provider}` | start sign-in (`google`, `microsoft`, `apple`, `facebook`) |
| GET/POST | `/api/auth/callback/{provider}` | provider redirect target |
| POST | `/api/auth/logout` | |
| GET / PATCH | `/api/me` | current user / change display name |
| GET | `/api/me/export` | everything held about you, as a ZIP (CSV and JSON) |
| DELETE | `/api/me` | `{"confirm": "DELETE"}`: delete the account and all of its data |
| GET | `/api/collection` | collection in the front end's `collection.json` shape, plus `history` |
| GET | `/api/collection/export.csv` | Dragon Shield CSV (byte-identical round-trip of your import) |
| POST / GET | `/api/imports` | upload a Dragon Shield CSV / list past imports with their changes |
| GET | `/api/history` | daily market value and cost |
| POST | `/api/decks/coverage` | `{"text": "<decklist>"}` → owned / partial / missing per card |
| GET / POST | `/api/decks` | saved decks / save one (`{"name", "text", "source_url"}`) |
| GET / PUT / DELETE | `/api/decks/{id}` | a saved deck with coverage / update / delete |
| POST / GET | `/api/shares` | create an invite link (`{"kind": "collection" \| "deck", "deck_id", "show_costs"}`) / list what you've shared |
| DELETE | `/api/shares/{id}` | revoke (owner) or leave (recipient) |
| POST | `/api/shares/accept` | `{"token"}` from an invite link |
| GET | `/api/shared` | what others have shared with you |
| GET | `/api/shared/{id}/collection` | a shared collection, read-only (prices paid hidden unless allowed) |
| GET | `/api/shared/{id}/deck` | a shared deck, with coverage against your own collection |
| GET | `/api/archidekt/decks/{id}` | public Archidekt deck (fetched server-side) |
| POST | `/api/facebook/data-deletion` | Meta's required data deletion callback |

Interactive docs at `/api/docs`.

## Deploying on Vercel

1. **Database:** add Neon Postgres from the Vercel Marketplace. It sets `DATABASE_URL`
   (`postgres://…` URLs are handled). Tables are created on first start.
2. **Environment variables** (Project → Settings → Environment Variables): `SESSION_SECRET`
   (long random string), `BASE_URL` (e.g. `https://vault.example.com`), and the provider
   credentials below. Never set `DEV_LOGIN` in production (the app refuses to start).
3. **Price sync:** add `DATABASE_URL` as a GitHub Actions repository secret. The
   `sync-prices` workflow runs daily, or run it by hand from the Actions tab.
4. **Domain:** add it in Vercel and point DNS at Vercel from wherever it's registered.

Vercel's Hobby plan is for non-commercial use. A public app with other users needs Pro.

### Sign-in providers

Each redirect URI is `{BASE_URL}/api/auth/callback/{provider}`. A provider appears
on the sign-in screen only when its client id is set.

| Provider | Where | Notes |
|---|---|---|
| Google | Google Cloud Console → APIs & Services → Credentials → OAuth client (Web) | Consent screen with `openid email profile`; no Google review needed for these scopes. |
| Microsoft | Entra admin center → App registrations | Supported accounts: *any organizational directory and personal Microsoft accounts*. Add a client secret. |
| Apple | Apple Developer (paid program) → Identifiers: an App ID with Sign in with Apple, then a **Services ID** (= `APPLE_CLIENT_ID`) with your domain and return URL; Keys → a Sign in with Apple key (`.p8` → `APPLE_PRIVATE_KEY`, its id → `APPLE_KEY_ID`) | The server signs a fresh short-lived client secret itself, so there's no 6-month rotation. Apple sends the user's name only on first sign-in and may hide the e-mail behind a relay address. Needs HTTPS. |
| Facebook | Meta for Developers → app with Facebook Login | Set the privacy policy URL and the data deletion callback `{BASE_URL}/api/facebook/data-deletion`. `email` and `public_profile` need no app review. |

Accounts are **not** merged by e-mail. Signing in with a second provider while signed in
links it to the same account; otherwise each provider identity is its own account.

## Next steps

- Card images and card details from the server's `cards` table, so the browser never calls Scryfall.
- Real value-over-time chart from `history` (the data is already in `/api/collection`).
- Precompiled front end (Vite + React + TypeScript) instead of in-browser Babel.
- Alembic migrations once the schema settles (tables are currently created with `create_all`).
- Graph features: Postgres link tables and recursive queries first; Apache AGE (Azure Postgres)
  or pgvector for "similar cards" if needed.
- Before opening to other people, work through the operational checklist in `docs/gdpr.md`
  (privacy notice details, processor agreements, backup and log retention).
