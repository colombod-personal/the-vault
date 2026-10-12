# Developing and operating the Vault

For people who run, change or deploy the code. The short introduction is in the [README](../README.md).

## What it is made of

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
- **Built on other people's work:** card data, rulings, images and prices are Scryfall's, the
  Comprehensive Rules are Wizards of the Coast's (read live, never stored), role tags come from the
  Scryfall Tagger community, combos from Commander Spellbook, decks from Archidekt and their
  authors, and collections from Dragon Shield and Moxfield files you upload. The Vault shows each
  source with its material, says what it sends to each service (never anything about you), and
  credits them in every screen's footer, on `/credits.html`, in the plugins and below
  ("Who this is built on, and how the Vault uses them").
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
vault/models.py       users, identities, imports, entries, collection_baselines, cards, price_snapshots, collection_values
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
jobs/sync_limited.py  17Lands per-card statistics (streams their files, keeps counts only), run by .github/workflows/sync-limited.yml
jobs/limited_window.py  which sets are in the rolling window of 8 (pure rules);  jobs/limited_terms.py  the 120-day terms re-read the job checks
                      and the text of the monthly reminder issue (.github/workflows/limited-terms-monthly.yml)
vault/limited_stats.py, vault/limited_data.py  the sample-size rules, the counters, and the Limited statistics answer (get_limited_card_stats)
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
After connecting an assistant, check it: ask it to call the Vault's `whoami` tool. It names the account, the scopes
(read, or also write) and the data versions the Vault holds (the Comprehensive Rules edition, card data and price dates).
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
   For a Vercel team, also set the repository *variable* `VERCEL_SCOPE` (e.g. `wintermute-team`).
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
   The **public-site** workflow checks this: after every production deployment (Git or the manual
   *deploy* fallback) and once a day it runs the
   smoke test against the public domain (`https://mtgvault.cards`, or the `PUBLIC_URL` repository variable)
   signed out, with `SMOKE_EXPECT_PUBLIC=1`, and fails if the site is behind a Vercel login or broken.
   After a Git deployment it first waits for `/api/health` to report that deployment (`VERCEL_URL`,
   its own `*.vercel.app` address: a redeploy of the same commit is told apart; the commit,
   `VERCEL_GIT_COMMIT_SHA`, is reported too). Vercel gives functions these variables only with project → Settings →
   Environment Variables → *Automatically expose System Environment Variables* on; *sync-prices*
   turns it on and redeploys.

### Security

- **Secrets reach `main` only.** `VERCEL_TOKEN` and `DATABASE_URL` live in the
  `vercel-production` GitHub environment, which only `main` may use. Repository-level secrets
  are readable by a workflow on any branch, so keep none there: delete `VERCEL_TOKEN` and
  `DATABASE_URL` under Settings → Secrets and variables → Actions → *Repository secrets* once
  the environment has them.
- **Jobs that hold a secret** run only for `main`, never on `push` or `pull_request`, and don't
  install or run branch code next to the token (sync-prices runs only from `main`, with
  hash-checked dependencies installed before any secret is in the environment).
- **Least privilege:** every workflow's `GITHUB_TOKEN` is read-only (the budget guard's alert jobs add `issues: write`; the guide's
  deploy job adds `pages: write` and `id-token: write`, only for main and only once the owner sets `GUIDE_PAGES_ENABLED`), checkouts don't keep the
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

**A recent sign-in for serious actions (#347):** deleting the account, downloading all data, creating an access token, connecting an AI app, adding a passkey,
linking a provider and removing an older passkey need a sign-in from the last 10 minutes (`RECENT_SIGNIN_SECONDS`). The person
confirms with a passkey, a linked provider, or a one-time code and link e-mailed to a verified address on the account. The e-mail needs
[Resend](https://resend.com) (`RESEND_API_KEY`, `EMAIL_FROM` on a domain verified there, `EMAIL_DAILY_CAP`, `APPLE_RELAY_REGISTERED`); **unset, nothing is sent** and
only the passkey and provider paths are offered. Locally, run the twins and set `RESEND_API_KEY=re_twin_dev_key` (the code is at
`/_twins/api/resend/sent`). Design, attacks and limits: `docs/mcp-oauth-threat-model.md`; routes: `docs/api.md`.

**Store reviewers:** the Claude and ChatGPT directories need a demo account that works without a second factor. Set
`REVIEWER_PASSPHRASE` (16+ characters) in the Vercel production environment and the sign-in page gains a "Reviewer sign-in"
box that opens one fixed account of made-up data (`vault/reviewer.py`, guide at `/reviewers`); unset, the way in does not exist.
`python -m jobs.seed_reviewer --reset` puts the demo data back.

**Costs:**
- Vercel Hobby: $0.
- Neon: free for 1 GB of storage per project and 100 compute-hours a month (checked 2026-10-04;
  see `docs/catalog-design.md` → Neon budget). Every job logs the database size (`jobs/db_budget.py`); at 70% the daily
  jobs **fail** (after their work) and a GitHub issue is opened or updated; at 85% catalog loading stops.
  **Compute hours (limit 100 CU-hours) and network transfer (5 GB) are not visible to the database:** if you create a Neon
  API key and store it as the `NEON_API_KEY` secret (and `NEON_PROJECT_ID` as a variable) in the `vercel-production`
  environment, the daily job checks them too; without the key, the `neon-monthly-check` workflow opens a reminder issue on the
  1st of each month and **you read them in the Neon console**. The daily price history grew about
  0.86 GB a year for a ~10k-printing collection before it was thinned and compacted; now it is capped
  at a year, thinned and stored in integer cents (`docs/catalog-design.md`), which keeps 10,000 printings near 130 MB. Past the
  free tier Neon's pay-as-you-go plan costs about $0.35 per GB-month plus compute while in use (a few dollars a month).
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
python -m jobs.vercel_setup --scope wintermute-team --provider google   # or microsoft, facebook, apple
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
4. `python -m jobs.vercel_setup --scope wintermute-team --provider google` and paste the client id and secret.

**Microsoft** (about 5 minutes; personal and work accounts)
1. [Entra admin center](https://entra.microsoft.com/) → Applications → App registrations → New registration.
2. Name "The Vault"; supported account types: *Accounts in any organizational directory and personal
   Microsoft accounts*; redirect URI: platform *Web*, `https://YOUR-DOMAIN/api/auth/callback/microsoft`.
3. Certificates & secrets → New client secret; copy its **Value** (shown once). The client id is the
   *Application (client) ID* on the Overview page.
4. `python -m jobs.vercel_setup --scope wintermute-team --provider microsoft` and paste both.
   Client secrets expire (24 months at most): put a reminder in your calendar.

**Facebook** (about 10 minutes; `email` and `public_profile` need no app review)
1. [Meta for Developers](https://developers.facebook.com/apps/) → Create app → *Authenticate and request
   data from users with Facebook Login*.
2. Facebook Login → Settings → Valid OAuth Redirect URIs: `https://YOUR-DOMAIN/api/auth/callback/facebook`.
3. App settings → Basic: privacy policy URL `https://YOUR-DOMAIN/privacy.html`; user data deletion →
   *Data deletion callback URL* `https://YOUR-DOMAIN/api/facebook/data-deletion`; app domain `YOUR-DOMAIN`.
4. Switch the app to **Live** (in Development mode only people with a role on the app can sign in).
5. `python -m jobs.vercel_setup --scope wintermute-team --provider facebook` and paste the App ID and App Secret.

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


## The guide on GitHub Pages

The player guide (how to install the Vault in each assistant app, sign in and use it) lives in `docs/site/` and is published with
GitHub Pages (bead vault-30s.3.2). **It is not live yet: merging the workflow publishes nothing** (see "Switching it on").

**How it is made.** Plain HTML, no framework: each page in `docs/site/` is a fragment with a few comment lines at the top
(`title`, `summary`, `nav`, `order`, `status`), and `scripts/build_site.py` (standard library) wraps it in the shared header,
navigation and footer (`docs/site/_layout.html`, which links back to https://mtgvault.cards) and writes plain files to `_site/`.
Chosen over Jekyll or a Node generator because the guide is a handful of pages, nothing has to be installed or pinned, and the same
script builds it locally, in CI and in the publishing job. Screenshots go in `docs/site/assets/`; every image needs alt text.

**What is published is an allow-list**, because everything built becomes public: pages (`.html`) and `.css`, `.png`, `.jpg`,
`.jpeg`, `.webp`, `.ico`, `.txt`. Any other file in `docs/site/` fails the build (not a silent skip): no SVG (it can carry script), no
`.md`, `.py`, `.htm`; a name that starts with `.` or contains `.local.` is refused; a symbolic link is refused. Put working notes
in a file or folder whose name starts with `_`: it is never copied. The build also refuses to write into a folder it did not make
(`build_site.py docs/site public` is an error, not a deletion).

**Nothing unverified ships (the owner asked for correct guidance).** A page's `status` is `being-written` (a banner, no steps, and
the page is marked `noindex`) or `checked` (steps were run on the real app; the page must carry
`<!-- checked: <date>, <app and version> -->`, shown to the reader). `status: home` is the start page. "No steps" is a heuristic:
the builder refuses a numbered list, `<pre>`, "Step 1" and a line or bullet that starts "1."; it cannot prove a page holds no
instruction, so whoever reviews a change to the guide reads the pages. The content is vault-30s.3.1's job and only rows that passed
on the real app (vault-wxy.7) may be written as working. Until then the pages may point at the Vault's Connect page but do not
vouch for it.

**The link check** (`python scripts/check_site_links.py`, standard library, run first in `tests.yml` and again in the publishing
workflow; `tests/test_guide_site.py` holds its cases) fails on: a broken internal link or `#anchor`; a missing screenshot; an image
with no alt text; anything loaded from another site (image, `srcset`, video, CSS `url(...)`); a link that starts with `/` (the project
address is a sub-path, so it would leave the guide); `http://`; `mailto:` and `tel:` (the guide carries no e-mail address: reports go
through the support page); `<base>`, `<iframe>`, `<object>`, `<embed>`, a meta refresh, an inline `<script>`, an `on...=` handler and
`@import`; and a link to https://mtgvault.cards that is not written exactly as a file in `public/` is named (or `/`, `/api/mcp`), or
whose `#anchor` is not on that page. Links to other sites are not fetched.

**The workflow** (`.github/workflows/guide-pages.yml`): on a push to `main` that changes `docs/site/**`, the build script, the link
check, `public/**` (the link check reads it) or the workflow, or by hand from `main`, the `build` job builds the guide, checks the
links and uploads the Pages artifact (kept one day). A manual run from another branch does nothing. The `deploy` job (official
`actions/configure-pages` and `actions/deploy-pages`, pinned by commit, in the `github-pages` environment) runs **only on `main` and
only when the repository variable `GUIDE_PAGES_ENABLED` is `true`**, so by default it is skipped and the run is green. Only that job
holds `pages: write` and `id-token: write`; the workflow has no secret and never asks configure-pages to enable Pages. One publish
runs at a time: only the deploy job is in the concurrency group `guide-pages`, so a build can never take the place of a waiting
publish. If the variable is `true` but Pages is not set to the "GitHub Actions" source, `configure-pages` fails with "Get Pages
site failed. Please verify that the repository has Pages enabled and configured to build using GitHub Actions", and the job adds a
line saying where to look.

**The boundary is the environment, not the `if` in the file.** Anyone who can push a branch can push a copy of the workflow without
the `if`, and a branch dispatch of it would then try to deploy. What stops that is the `github-pages` environment's deployment
branch policy (main only): GitHub refuses the deploy from any other ref. So the environment is created, with that policy, before
anything else is switched on (below), and the verification line is part of the steps.

**llms.txt stays at https://mtgvault.cards/llms.txt** (a file at the root of the domain by convention; `public/llms.txt`, generated
by `scripts/build_plugin.py`). The guide does not carry a copy; add one only if the owner asks.

### The address (decision recorded 2026-10-12)

| | A. Project address | B. Custom domain |
|---|---|---|
| Address | `https://colombod-personal.github.io/the-vault/` | `https://docs.mtgvault.cards/` |
| Owner steps | The steps below. No DNS | The same, plus: verify the domain for the organisation, one DNS record at Vercel (the domain's DNS is `ns1/ns2.vercel-dns.com`), the custom-domain setting, wait for the certificate (up to a day), then "Enforce HTTPS" |
| Reversible | Unset the variable and switch Pages off | Remove the DNS record **before** switching Pages off: a record left pointing at GitHub lets someone else host a page on the subdomain (GitHub's own warning); verifying the domain prevents it |
| Stability | Changes if the organisation or repository is renamed | Independent of GitHub names, and the guide can move host later |
| Looks like | A GitHub page | Part of mtgvault.cards |
| Risk to the app | None | A sibling subdomain of the app's domain on third-party hosting. The app's cookies are host-only (no `Domain=`), so the guide cannot read them; it could set a cookie for the parent domain, which is harmless while the guide runs no script |

**Recommendation: A, the project address, now.** It needs no DNS and nothing that can dangle, it can be switched off in one
setting, and every link in the guide is relative, so moving to B later is one setting and one record, plus the few links from the
app (vault-30s.3.3, which should keep the address in one place). B is better for the brand but it is a permanent public record in
the domain's DNS and a second thing for the owner to keep up to date; there is no player-facing reason to pay that before the guide
has any content. I could not confirm in GitHub's documentation whether the old project address redirects to the custom domain
once one is set, so do not rely on a redirect: choose before the links in the app (vault-30s.3.3) are published, or plan to update
them.

### Switching it on (only with the owner's yes: it makes the guide public)

For address A (no DNS). Do the steps in this order; step 1 is not optional.

1. **Required: create the `github-pages` environment limited to `main`, before anything else.** It does not exist today
   (`gh api repos/colombod-personal/the-vault/environments` lists `copilot`, `Preview`, `Production` and `vercel-production`, not
   `github-pages`). Settings → Environments → New environment → `github-pages` → Deployment branches and tags → **Selected
   branches and tags** → add `main`. Check it:
   `gh api repos/colombod-personal/the-vault/environments/github-pages --jq .deployment_branch_policy` must print
   `{"custom_branch_policies":true,"protected_branches":false}`, and
   `gh api repos/colombod-personal/the-vault/environments/github-pages/deployment-branch-policies --jq '.branch_policies[].name'`
   must print only `main`. If either differs, stop: the guide must not be switched on.
2. Settings → Pages → Build and deployment → Source: **GitHub Actions** (it uses the environment from step 1).
3. Settings → Secrets and variables → Actions → Variables → New repository variable `GUIDE_PAGES_ENABLED` = `true`.
4. Actions → guide-pages → Run workflow on `main`. The first publish does not wait for a push to `docs/site`.
5. Check it signed out: `curl -sSI https://colombod-personal.github.io/the-vault/` returns 200, and the page shows the guide.

For address B, after A works: verify `mtgvault.cards` for the organisation (organisation Settings → Pages → Add a domain; GitHub shows
the TXT record to add), add the `CNAME` record `docs` → `colombod-personal.github.io` in Vercel (Domains → mtgvault.cards → DNS
records), set Settings → Pages → Custom domain to `docs.mtgvault.cards`, wait for the DNS check, tick Enforce HTTPS.

To take the guide down: unset `GUIDE_PAGES_ENABLED` (stops new publishes), then Settings → Pages → Unpublish site (after removing
the DNS record, if B).

## Attribution rules for contributors


The Vault depends on other people's work, and says so visibly:

| Where | What is credited |
|---|---|
| Credits page, "Limited statistics" | 17Lands: CC BY 4.0, what the Vault changed, no endorsement; every Limited answer repeats the credit with the set, format and dates |
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


## Archidekt: read only

**The Vault only reads from Archidekt.** It keeps to [Archidekt's terms](https://archidekt.com/terms) and to the
open, read-only API its developers describe on their forum.

- It reads **one public deck at a time, only when you ask** (you paste a deck link, open a saved deck, or an
  AI assistant calls `get_archidekt_deck` for you). There are no background jobs, no crawling and no deck
  search. A deck is kept only if you press Save: then the Vault stores your copy of its list, its link and its
  author's public username (for the credit), in your account, and you can remove it any time.
- It **never writes to Archidekt**: it does not sign in to your Archidekt account, change your decks or sync
  anything. When the Vault suggests changes, it gives you a change list and a buying list, and **you apply
  them on Archidekt yourself**.
- Private decks are not read (Archidekt answers "not found" without your sign-in, and the Vault never asks
  for it).
- Every deck is credited to Archidekt and its author, with a link back.
- It is gentle with Archidekt's rate limit (they start limiting at about 40 requests a minute): one request per
  deck a person asks for, and a public deck read is kept for 10 minutes so opening it again does not reach
  Archidekt (unless the person presses Refresh; #133).
- A guard test (`tests/test_archidekt_readonly.py`, #132) fails if anything in the Vault could write to Archidekt.

The details and sources are in [`docs/compliance.md`](docs/compliance.md#archidekt) (#79).

## Next steps

- TypeScript for the front end (the JSX is already compiled ahead of time by `web/build.mjs`).
- Graph features: Postgres link tables and recursive queries first; Apache AGE (Azure Postgres)
  or pgvector for "similar cards" if needed.
- Before opening to other people, work through the operational checklist in `docs/gdpr.md`
  (privacy notice details, processor agreements, backup and log retention).
