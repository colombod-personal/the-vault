# The Vault

A Magic: The Gathering collection manager and valuation app. Import your
Dragon Shield export, see what it's worth, track value over time, and check
decks against what you own.

- **Server:** Python / FastAPI, built on [mtg-toolkits](https://github.com/colombod-personal/mtg-toolkits)
  (CSV parsing, Scryfall matching, deltas, decklists).
- **Front end:** the React prototype from the design handoff, in `public/`,
  now loading data from the server (see `docs/prototype-handoff.md`).
- **Sign-in:** Google, Microsoft, Apple and Facebook (OAuth 2 / OpenID Connect via Authlib).
- **Storage:** Postgres, and only Postgres: Neon (via the Vercel Marketplace) in production, a
  local Postgres for development and the tests, so what is tested is what runs.
- **Prices:** a daily GitHub Actions job downloads Scryfall's bulk file, matches every
  collection offline and stores that day's prices, so the value chart is real history.
- **Credits:** every service the Vault relies on is credited where it's used, in the footer of
  every screen, and on `/credits.html` (see "Attribution" below).
- **Privacy:** multi-tenant and private by default. Users share their collection or a deck
  with someone through a one-time invite link, and can revoke it. Everyone can download all
  their data and delete their account, which removes every row (see `docs/gdpr.md`).

## Run it locally

The Vault runs on Postgres only. Start one (any Postgres 14+ works; this is Docker's):

```bash
docker run -d --name vault-pg -p 5432:5432 -e POSTGRES_USER=vault -e POSTGRES_PASSWORD=vault \
  -e POSTGRES_DB=vault postgres:16
docker exec vault-pg createdb -U vault vault_test   # for the tests, which wipe it
```

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # DATABASE_URL points at the database above; DEV_LOGIN=1 gives you a "Local dev sign-in" button
set -a && . ./.env && set +a
uvicorn --factory vault.app:create_app --reload --port 8000
# open http://localhost:8000, sign in, import your Dragon Shield or Moxfield CSV
```

Get prices without waiting for the daily job:

```bash
python -m jobs.sync_prices      # downloads Scryfall's default_cards file (~75 MB)
```

Tests: `VAULT_TEST_DATABASE_URL=postgresql://vault:vault@localhost:5432/vault_test pytest`
(each test starts from an empty schema in that database, so never point it at real data)

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
vault/importer.py     collection files (Dragon Shield, Moxfield, generic CSV) -> entries, and exports in every format
vault/collection_view.py  one user's collection as items, sets, timeline, stats (cached per version)
vault/analytics.py    analytics in Postgres: P&L, breakdowns, valuation, per-name rollup, deck prices, refresh
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

**Agents.** People can connect their own AI agents: OAuth for ChatGPT, Claude and other MCP clients
(add the URL `/api/mcp`; the Vault is the authorization server, see `docs/mcp-oauth-threat-model.md`),
personal access tokens (read, or read and write), an MCP server at `/api/mcp`, and `/llms.txt`. See [`docs/agents.md`](docs/agents.md).
The web app computes nothing about a collection: each view asks the server for what it shows
(totals, P&L, breakdowns, the value over time, pages of printings; see `docs/api.md` →
Analytics). It keeps the server's answers in IndexedDB, keyed by the collection's version, so it
reopens with one small request and works offline. It has the server refresh prices and card data
by itself (`POST /api/v1/collection/refresh`) after an import and when prices are older than today.

## Deploying on Vercel

The app runs on Vercel's free Hobby plan (fine while the Vault is free and non-commercial),
with a Neon Postgres database in Frankfurt (`fra1`) for GDPR. HTTPS is automatic.

**How deploys work**
- **Only merges to `main` deploy.** Vercel's GitHub integration runs `vercel.json` →
  `ignoreCommand` before each deployment, which skips every branch but `main`. Branches get
  no preview; test them locally with the twins (`docs/twins.md`).
- The **vercel-check** workflow runs when a deployment succeeds (`deployment_status`) and
  smoke-tests that exact deployment from outside with `jobs/smoke_test.py`, without secrets.
- The **sync-prices** workflow (daily, or Actions → sync-prices → *Run workflow*) first
  configures the project with `jobs/vercel_setup.py --redeploy`: it sets `SESSION_SECRET` and
  `BASE_URL`, redeploys production unless it was built from the current production variables
  (variables only reach new deployments, so a merge's deployment is redeployed once; the rebuild
  takes main's latest commit and steps aside if another release just went live), and writes a checklist of what's missing with the OAuth redirect URIs. Then it
  syncs prices.
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
   Facebook are optional (next section); the sync-prices summary lists the redirect URIs to
   register.
5. **Go live:** merge to `main`, then Actions → *sync-prices* → *Run workflow* once: it
   configures the project, redeploys and loads prices. Until then the site answers 503.
6. **Prices:** the *sync-prices* workflow runs daily from `main`. It reads the database address from Vercel through `VERCEL_TOKEN`. A
   `DATABASE_URL` secret in the `vercel-production` environment overrides that.
7. **Optional:** your own domain, under project → Settings → Domains. Then delete `BASE_URL` in
   Vercel and run *sync-prices* again: it sets the custom domain and redeploys. Update each
   provider's redirect URI.
8. **Public access:** project → Settings → Deployment Protection → Vercel Authentication →
   *Standard Protection* (protects previews only) so visitors aren't sent to a Vercel login.

### Security

- **Secrets reach `main` only.** `VERCEL_TOKEN` and `DATABASE_URL` live in the
  `vercel-production` GitHub environment, which only `main` may use. Repository-level secrets
  are readable by a workflow on any branch, so keep none there: delete `VERCEL_TOKEN` and
  `DATABASE_URL` under Settings → Secrets and variables → Actions → *Repository secrets* once
  the environment has them.
- **Jobs that hold a secret** run only for `main`, never on `push` or `pull_request`, and don't
  install or run branch code next to the token (sync-prices runs only from `main`, with
  hash-checked dependencies installed before any secret is in the environment).
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
on the sign-in screen only when its client id is set. To keep a configured provider off the
sign-in screen (say, a Meta app still in Development mode), list it in `AUTH_HIDDEN_PROVIDERS`
(comma-separated, e.g. `facebook`); its sign-in URL and account linking keep working.

Store a provider's credentials in Vercel (Production) from your terminal. It asks for each value
at a hidden prompt and prints only names, then redeploy (or merge) for them to apply:

```bash
read -rs VERCEL_TOKEN && export VERCEL_TOKEN
python -m jobs.vercel_setup --scope wintermute2 --provider google   # or microsoft, facebook, apple
unset VERCEL_TOKEN
```

In every step below, `https://YOUR-DOMAIN` is the production address (`python -m jobs.vercel_setup`
prints it, and the exact redirect URIs, under "Redirect URIs to register with each provider").

**Google** (about 5 minutes; no review needed for these scopes)
1. [Google Cloud Console](https://console.cloud.google.com/) → create or pick a project.
2. APIs & Services → OAuth consent screen: *External*, app name "The Vault", your support e-mail;
   scopes `openid`, `email`, `profile`; add `https://YOUR-DOMAIN/privacy.html` as the privacy policy.
   Publish the app (until then only listed test users can sign in).
3. APIs & Services → Credentials → Create credentials → OAuth client ID → *Web application*.
   Authorized redirect URI: `https://YOUR-DOMAIN/api/auth/callback/google`.
4. `python -m jobs.vercel_setup --scope wintermute2 --provider google` and paste the client id and secret.

**Microsoft** (about 5 minutes; personal and work accounts)
1. [Entra admin center](https://entra.microsoft.com/) → Applications → App registrations → New registration.
2. Name "The Vault"; supported account types: *Accounts in any organizational directory and personal
   Microsoft accounts*; redirect URI: platform *Web*, `https://YOUR-DOMAIN/api/auth/callback/microsoft`.
3. Certificates & secrets → New client secret; copy its **Value** (shown once). The client id is the
   *Application (client) ID* on the Overview page.
4. `python -m jobs.vercel_setup --scope wintermute2 --provider microsoft` and paste both.
   Client secrets expire (24 months at most): put a reminder in your calendar.

**Facebook** (about 10 minutes; `email` and `public_profile` need no app review)
1. [Meta for Developers](https://developers.facebook.com/apps/) → Create app → *Authenticate and request
   data from users with Facebook Login*.
2. Facebook Login → Settings → Valid OAuth Redirect URIs: `https://YOUR-DOMAIN/api/auth/callback/facebook`.
3. App settings → Basic: privacy policy URL `https://YOUR-DOMAIN/privacy.html`; user data deletion →
   *Data deletion callback URL* `https://YOUR-DOMAIN/api/facebook/data-deletion`; app domain `YOUR-DOMAIN`.
4. Switch the app to **Live** (in Development mode only people with a role on the app can sign in).
5. `python -m jobs.vercel_setup --scope wintermute2 --provider facebook` and paste the App ID and App Secret.

**Apple** (paid Apple Developer program): Identifiers → an App ID with Sign in with Apple, then a
**Services ID** (= `APPLE_CLIENT_ID`) with your domain and the return URL
`https://YOUR-DOMAIN/api/auth/callback/apple`; Keys → a Sign in with Apple key (`.p8` →
`APPLE_PRIVATE_KEY`, its id → `APPLE_KEY_ID`). The server signs a fresh short-lived client secret
itself, so there's no 6-month rotation. Apple sends the user's name only on first sign-in and may hide
the e-mail behind a relay address.

After storing credentials, redeploy (or merge) for them to apply; the provider's button then appears
on the sign-in screen.

Accounts are **not** merged by e-mail. Signing in with a second provider while signed in
links it to the same account; otherwise each provider identity is its own account.

**Linking a sign-in that already has its own account** (say Microsoft, used once by mistake and
so holding a second, empty vault) works only when that other account is *empty*
(`vault.auth.account_is_empty`): no collection rows, imports, decks, shares given or received
(pending invites too), unexpired personal access tokens, or value history with any value. Its
profile, sessions, one-time codes and expired tokens don't count. Then, in one transaction:
- only the identity being linked moves to the signed-in account;
- the other account is signed out everywhere; if it has no sign-in or passkey left, it is deleted
  with everything `vault.privacy.personal_data` lists (its app sessions end too);
- the web app shows "Microsoft is now linked to this vault (its empty test account was removed)"
  (`/?linked=microsoft&empty_account=removed`, or `kept`).

If the other account holds data, nothing changes and the link is refused
(`/?link_error=identity_in_use&provider=…`; apps get `error=identity_in_use` or `409`): sign in
with that provider, download that account's data if you want it, delete it (Account → Delete my
account), then link the provider again. Two accounts with data are never merged. The web
callback, the app hand-off (`app_redirect_uri`) and native ID-token sign-in follow the same rule.
A passkey can't be moved: it carries its account's WebAuthn user handle, so add a new passkey to
the account you keep (a passkey sign-in switches accounts, it never links).

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
- linking a sign-in from an empty account (moved, that account removed) or one with data (refused)
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
