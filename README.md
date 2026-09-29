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
# open http://localhost:8000, sign in, import your Dragon Shield or Moxfield CSV
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
vault/migrations/     schema migrations (Alembic), applied at startup; alembic.ini for writing new ones
vault/importer.py     Dragon Shield CSV -> entries (records what changed since the last import)
vault/collection_view.py  one user's collection as items, sets, timeline, stats (cached per version)
vault/tokens.py       bearer access/refresh tokens and PKCE app codes for native apps
vault/native.py       verifies Apple / Google ID tokens from the iOS SDKs
vault/sync.py         daily Scryfall sync (offline matching, prices, per-user value)
vault/api/v1.py       the API (/api/v1): paged, linked (HAL), ETags, problem+json
vault/api/hal.py      links, cursors, pages, ETags, problem details
vault/api/schemas.py  response models (OpenAPI for Swift codegen)
vault/api/mcp.py      MCP server for people's own agents (tools wrap /api/v1)
vault/api/idempotency.py  Idempotency-Key replay for retried POSTs
vault/api/meta.py     Facebook data deletion callback
vault/sharing.py      invite links, access checks for shared collections and decks
vault/privacy.py      GDPR data export (ZIP) and account erasure (purge_user)
vault/outbound.py     sends outbound calls to the twin universe in local development (VAULT_TWINS_URL)
jobs/sync_prices.py   CLI run by .github/workflows/sync-prices.yml
twins/                digital twins of every outside service, for tests and offline development
public/               front end (React, JSX compiled into public/app.bundle.js by web/build.mjs)
web/                  the front-end build (esbuild): npm --prefix web ci && npm --prefix web run build
```

## API

The web app and native apps share one API under `/api/v1`: paged with cursors (at most 500
items per response), linked (`_links`, start at `GET /api/v1`), ETag/304, errors as
`application/problem+json`. Native apps sign in with Apple or Google ID tokens, or through the
browser with PKCE, and use rotating bearer tokens. Full reference: [`docs/api.md`](docs/api.md).
Interactive docs at `/api/docs`; OpenAPI at `/api/openapi.json`.

**Moving collections.** Imports accept Dragon Shield, Moxfield and generic CSV; the format is
detected from the header. Exports cover Dragon Shield (byte-identical to a Dragon Shield
import), Moxfield, Archidekt, generic CSV (lossless, with Scryfall ids) and a text list. They
are in Account → Move your collection and at `/api/v1/collection/exports`. Matched printings go
out with Scryfall's set codes, so other apps recognise them.

**Agents.** People can connect their own AI agents: personal access tokens (read, or read and
write), an MCP server at `/api/mcp`, and `/llms.txt`. See [`docs/agents.md`](docs/agents.md).
The web app keeps collections in IndexedDB, keyed by version, and retries failed pages. It
reopens with one small request and works offline.

## Deploying on Vercel

The app runs on Vercel's free Hobby plan (fine while the Vault is free and non-commercial),
with a Neon Postgres database in Frankfurt (`fra1`) for GDPR. HTTPS is automatic.

**How deploys work**
- **Only merges to `main` deploy.** Vercel's GitHub integration runs `vercel.json` →
  `ignoreCommand` before each deployment, which skips every branch but `main`. Branches get
  no preview; test them locally with the twins (`docs/twins.md`).
- The **vercel-check** workflow runs when a deployment succeeds (`deployment_status`):
  - for production deployments of `main`, configures the project with `jobs/vercel_setup.py`,
    setting `SESSION_SECRET` and `BASE_URL` and writing a checklist of what's missing, with the
    OAuth redirect URIs;
  - smoke-tests that exact deployment from outside with `jobs/smoke_test.py`, without secrets.
- **deploy (manual fallback)** is a Vercel CLI deploy of `main`, for when the integration isn't
  connected.

**One-time setup:**

1. **Vercel:** sign up at [vercel.com](https://vercel.com) with GitHub (Hobby). Import or connect
   `colombod-personal/the-vault` (project `the-vault`). Vercel detects FastAPI from
   `pyproject.toml` (`[tool.vercel] entrypoint = "api.index:app"`).
2. **Token for Actions, in a protected environment** (see *Security* below): Vercel → Account
   Settings → Tokens → *Create*, scoped to the team, with an expiry. In GitHub → `the-vault` →
   Settings → Environments → **New environment** `vercel-production`:
   - *Deployment branches and tags* → **Selected branches** → add `main`;
   - *Environment secrets* → add `VERCEL_TOKEN` (and `DATABASE_URL` if you want the price sync to
     skip Vercel).
   For a Vercel team, also set the repository *variable* `VERCEL_SCOPE` (e.g. `wintermute2`).
3. **Database:** Vercel → project → Storage → *Create Database* → **Neon**, region
   **Frankfurt**, connected to Production and Preview. This sets `DATABASE_URL`.
4. **Sign-in:** passkeys work as soon as the site is up. Google, Microsoft, Apple and
   Facebook are optional (next section); the vercel-check summary lists the redirect URIs to
   register.
5. **Go live:** merge to `main`. Environment changes apply from the next deployment.
6. **Prices:** the *sync-prices* workflow runs daily from `main`; run it once by hand after the
   first deploy. It reads the database address from Vercel through `VERCEL_TOKEN`. A
   `DATABASE_URL` secret in the `vercel-production` environment overrides that.
7. **Optional:** your own domain, under project → Settings → Domains. Update `BASE_URL` and each
   provider's redirect URI afterwards.

### Security

- **Secrets reach `main` only.** `VERCEL_TOKEN` and `DATABASE_URL` live in the
  `vercel-production` GitHub environment, which only `main` may use. Repository-level secrets
  are readable by a workflow on any branch, so keep none there: delete `VERCEL_TOKEN` and
  `DATABASE_URL` under Settings → Secrets and variables → Actions → *Repository secrets* once
  the environment has them.
- **Jobs that hold a secret** run only for `main`, never on `push` or `pull_request`, and don't
  install or run branch code next to the token (vercel-check's configure step runs the default
  branch's code with only `httpx` installed).
- **Least privilege:** every workflow's `GITHUB_TOKEN` is read-only, checkouts don't keep the
  token, the Vercel CLI version is pinned, and values reach scripts through `env`, never
  `${{ }}` inside the script.
- **Protect `main`:** Settings → Branches → add a rule (or ruleset) for `main`: require a pull
  request and the `test` check, and block force pushes and deletion.
- **Vercel:** keep Deployment Protection on, give tokens an expiry, and never set `DEV_LOGIN`.
- **The "Vercel Preview Comments" check** on a pull request goes red while a comment left with
  the Vercel toolbar on a preview is unresolved. It isn't a code failure. Resolve the comment in
  Vercel (the check's *Go to feedback* link). Vercel re-posts the check only for a new commit, so
  re-running it does nothing. Branches have no previews any more, so turn the toolbar's comments
  off for Preview (Vercel → the-vault → Settings → General → Vercel Toolbar) to keep it from
  coming back.
- CDN scripts carry integrity hashes, so a tampered copy won't run.
- `tests/test_workflows.py` fails if a workflow breaks one of these rules.

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

**Passkeys work without any setup**: Face ID, Touch ID, Windows Hello, a phone or a security
key. People can create an account with a passkey alone, or add one to an account from another
provider. The OAuth providers below are optional extras.

Each redirect URI is `{BASE_URL}/api/auth/callback/{provider}`. A provider appears
on the sign-in screen only when its client id is set.

Store a provider's credentials in Vercel (Production) from your terminal. It asks for each value
at a hidden prompt and prints only names, then redeploy (or merge) for them to apply:

```bash
read -rs VERCEL_TOKEN && export VERCEL_TOKEN
python -m jobs.vercel_setup --scope wintermute2 --provider google   # or microsoft, facebook, apple
unset VERCEL_TOKEN
```

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

- TypeScript for the front end (the JSX is already compiled ahead of time by `web/build.mjs`).
- Graph features: Postgres link tables and recursive queries first; Apache AGE (Azure Postgres)
  or pgvector for "similar cards" if needed.
- Before opening to other people, work through the operational checklist in `docs/gdpr.md`
  (privacy notice details, processor agreements, backup and log retention).

## Licence

Our code is [MIT](LICENSE). Dependencies keep their own licences; all are permissive except
Psycopg (LGPL-3.0, used unmodified as a library, which is compatible with MIT) and certifi
(MPL-2.0, unmodified). Magic: The Gathering content belongs to Wizards of the Coast (Fan
Content Policy), and Scryfall data follows Scryfall's terms. Details are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); `tests/test_licenses.py` keeps GPL out.
