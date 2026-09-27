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
- **Credits:** every service the Vault relies on is credited where it's used, in the footer of
  every screen, and on `/credits.html` (see "Attribution" below).
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

Everything offline, without real accounts or Scryfall: run the **digital twin universe**,
behavioural clones of Google, Microsoft, Apple, Facebook, Scryfall and Archidekt. See
[`docs/twins.md`](docs/twins.md).

```bash
python -m twins --port 9000                                          # control panel at /_twins
VAULT_TWINS_URL=http://localhost:9000 uvicorn --factory vault.app:create_app --reload
```

## Layout

```
api/index.py          Vercel entry point (all /api/* routes)
vault/app.py          FastAPI app factory, sessions, static files for local dev
vault/auth.py         Google / Microsoft / Apple / Facebook sign-in, account linking
vault/models.py       users, identities, imports, entries, cards, price_snapshots, collection_values
vault/importer.py     Dragon Shield CSV -> entries (records what changed since the last import)
vault/collection_view.py  one user's collection as items, sets, timeline, stats (cached per version)
vault/tokens.py       bearer access/refresh tokens and PKCE app codes for native apps
vault/native.py       verifies Apple / Google ID tokens from the iOS SDKs
vault/sync.py         daily Scryfall sync (offline matching, prices, per-user value)
vault/api/v1.py       the API (/api/v1): paged, linked (HAL), ETags, problem+json
vault/api/hal.py      links, cursors, pages, ETags, problem details
vault/api/schemas.py  response models (OpenAPI for Swift codegen)
vault/api/meta.py     Facebook data deletion callback
vault/sharing.py      invite links, access checks for shared collections and decks
vault/privacy.py      GDPR data export (ZIP) and account erasure (purge_user)
vault/outbound.py     sends outbound calls to the twin universe in local development (VAULT_TWINS_URL)
jobs/sync_prices.py   CLI run by .github/workflows/sync-prices.yml
twins/                digital twins of every outside service, for tests and offline development
public/               front end (React prototype, no build step yet)
```

## API

The web app and native apps share one API under `/api/v1`: paged with cursors (at most 500
items per response), linked (`_links`, start at `GET /api/v1`), ETag/304, errors as
`application/problem+json`. Native apps sign in with Apple or Google ID tokens, or through the
browser with PKCE, and use rotating bearer tokens. Full reference: [`docs/api.md`](docs/api.md).
Interactive docs at `/api/docs`; OpenAPI at `/api/openapi.json`.

## Deploying on Vercel

The app runs on Vercel's free Hobby plan (fine while the Vault is free and non-commercial)
with a Neon Postgres database, in Frankfurt (`fra1`) for GDPR. HTTPS is automatic.
Deploys go through GitHub Actions (`.github/workflows/deploy.yml`) with the Vercel CLI,
because Vercel's Hobby dashboard can't import a repository owned by a GitHub organization.
Every push to `main` runs the tests and then deploys to production.

**One-time setup** (in this order):

1. **Vercel account:** sign up at [vercel.com](https://vercel.com) with GitHub (Hobby plan).
2. **Token:** Vercel → Account Settings → Tokens → *Create*, scoped to your account. In GitHub:
   `the-vault` → Settings → Secrets and variables → Actions → *New repository secret*
   `VERCEL_TOKEN` with that value.
3. **First deploy:** merge to `main` (or run the *deploy* workflow). It creates the Vercel
   project `the-vault`. Until step 4 is done the site answers
   "not configured yet: DATABASE_URL is not set". That's expected.
4. **Database:** Vercel → project `the-vault` → Storage → *Create Database* → **Neon**,
   region **Frankfurt**, connect it to the project (Production and Preview). This sets
   `DATABASE_URL`. The Neon free plan is enough to start (see "Costs" below).
5. **Environment variables** (project → Settings → Environment Variables, Production):
   - `SESSION_SECRET`: a long random string (`openssl rand -hex 32`)
   - `BASE_URL`: the production address, e.g. `https://the-vault.vercel.app` (shown on the
     project page), or your own domain once added
   - at least one sign-in provider's credentials (next section). Google is the quickest.
   - never `DEV_LOGIN` (the app refuses to start with it on HTTPS)
6. **Redeploy:** Actions → *deploy* → *Run workflow*.
7. **Prices:** GitHub → Settings → Secrets → `DATABASE_URL` with the same Neon connection
   string, then Actions → *sync-prices* → *Run workflow* once. After that it runs daily.
8. **Optional:**
   - Add the repository variable `VAULT_URL` (the site address) so each deploy checks the live site.
   - Add your own domain under project → Settings → Domains. Point its DNS at Vercel and
     the certificate is issued automatically. Then update `BASE_URL` and each provider's
     redirect URI.

**Costs:**
- Vercel Hobby: $0.
- Neon: free for 0.5 GB and 100 compute-hours a month. The daily price history grows about
  0.5 GB a year for a ~10k-printing collection, so after that Neon's pay-as-you-go plan costs
  about $0.35 per GB-month plus compute while in use (a few dollars a month).
- GitHub Actions: $0.
- A domain: about $10/year at Cloudflare or Porkbun.

**Limits to know:**
- Vercel caps a request or response at 4.5 MB. Responses are gzip-compressed (a 21k-card
  collection is 0.5 MB on the wire, and a test guards this). CSV uploads over about 4.5 MB
  (roughly 38k rows) would be refused.
- After a quiet spell the first request takes 1–2 s while the function and database wake up.

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

### Testing sign-in

**Automated, on every push:** `tests/test_signin_flows.py` and `tests/test_native_auth.py`
run the real sign-in flows against the providers' digital twins (`twins/`, see
[`docs/twins.md`](docs/twins.md)). The twins check what the real providers check. The tests cover:
- scopes, registered redirect URIs, and single-use codes
- Microsoft's per-tenant issuer, and its ID tokens without `email`
- Apple's `form_post`, its ES256 client secret (verified by the twin), a name sent only once,
  and Hide My Email
- Facebook users who don't share their e-mail
- linking a second provider, and no merging by e-mail
- cancelled sign-ins, and replayed or forged callbacks (wrong nonce, audience or issuer)
- provider outages
- signing-key rotation
- native ID tokens and the PKCE app handoff

**Nightly:** `tests/conformance` compares each twin with the real service, so the twins stay faithful.

**By hand, offline:** `python -m twins` plus `VAULT_TWINS_URL`. Each sign-in button opens the
twin's sign-in page.

**With real accounts** (needs the credentials above):
- Google, Microsoft and Facebook also work locally with `BASE_URL=http://localhost:8000` and
  redirect URI `http://localhost:8000/api/auth/callback/<provider>`.
- Apple needs the HTTPS deployment.
- Keep Google's consent screen in *Testing* with your accounts as test users, and Facebook's
  app in *Development* with testers added under App roles.

Check each provider:
1. The first sign-in creates your vault, and signing in again reopens it.
2. *Cancel* on the provider's page returns you to the sign-in screen with an error.
3. While signed in, signing in with a second provider links it (Account shows both).
4. Apple: your name appears after the first sign-in. With *Hide my email* you get a relay address.
5. Account → Delete my account, then sign in again: you get a fresh, empty vault.
6. Facebook: Meta's app dashboard → *Data Deletion Request Callback* test.

## Attribution

The Vault depends on other people's work, and says so visibly:

| Where | What is credited |
|---|---|
| Footer on every screen, including sign-in | Scryfall (card data, images, prices), TCGplayer and Cardmarket (price sources), Archidekt, Dragon Shield, the artists, links to Credits and Privacy, and the Wizards of the Coast Fan Content notice |
| Card drawer | "Illustrated by *artist*", image via Scryfall, © Wizards of the Coast; where prices come from |
| Market value tile | how many cards are priced by Scryfall and how many by your Dragon Shield export |
| Decks tab | "Deck list from Archidekt", with the author and a "View this deck on Archidekt" link |
| Import screen | the Dragon Shield Card Manager, and that only the uploaded file is read |
| `/credits.html` | every service and open-source library: what it does, what data (if any) it receives, a link, and a thank-you |
| Data export `README.txt` | Scryfall and the Fan Content notice |

Rules to keep (from [Scryfall's API terms](https://scryfall.com/docs/api) and
[Wizards' Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy)):

- **The Vault must stay free.** Wizards' policy forbids charging for fan content, and
  Scryfall forbids paywalling its data. Sign-in only protects each user's private collection,
  and the credits and privacy pages stay public.
- Keep the Fan Content notice visible. Don't use Scryfall's logo or imply that Scryfall,
  Archidekt, Dragon Shield or Wizards endorse the Vault.
- Card images: never crop, cover, stretch, recolour or watermark them, and keep the
  artist and copyright lines visible. If you ever show `art_crop` images, credit the artist next to them.
- Link back to Archidekt decks, as Archidekt asks.
- When you add a service or library, add it to `public/credits.html` (and to the footer
  if users see its data).

## Next steps

- Card images and card details from the server's `cards` table, so the browser never calls Scryfall.
- Real value-over-time chart from `/api/v1/collection/history`.
- Precompiled front end (Vite + React + TypeScript) instead of in-browser Babel.
- Alembic migrations once the schema settles (tables are currently created with `create_all`).
- Graph features: Postgres link tables and recursive queries first; Apache AGE (Azure Postgres)
  or pgvector for "similar cards" if needed.
- Before opening to other people, work through the operational checklist in `docs/gdpr.md`
  (privacy notice details, processor agreements, backup and log retention).
