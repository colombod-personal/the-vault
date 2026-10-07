@AGENTS.md

# The Vault

MTG collection manager: FastAPI server (`vault/`) on top of the `mtg-toolkits` library, with the
React prototype as the front end (`public/`). Read `README.md` first.

- Library logic (CSV formats, Scryfall matching, deltas, decklists) belongs in
  `colombod-personal/mtg-toolkits`, not here. Bump the pinned commit in `pyproject.toml`,
  `requirements.txt` and `requirements-vcs.txt` together.
- CI and the jobs install Python packages only at hash-checked versions: `requirements-lock.txt`
  (the app, its tests, the build backend) and `jobs/requirements-ops.txt` (httpx for the Vercel
  jobs). After changing a dependency, regenerate them (`tests/test_workflows.py` checks):
  `uv pip compile <inputs> --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes`,
  where the inputs are requirements.txt without mtg-toolkits, plus the dev extras, httpx and hatchling.
- Never commit collection data (CSV exports, collection.json). `.gitignore` blocks them.
- One API for web and native apps: `/api/v1` (`docs/api.md`). Every list is paged with a cursor
  (`vault.api.hal.paginate`, max 500); never add an endpoint whose response grows with the
  collection. Add `_links`, a response model (OpenAPI drives the Swift client) and a test.
  Analytics are computed server-side from Postgres (`vault/analytics.py`); the front end only
  renders API answers (`public/lib/api.js`) and never computes values or fetches prices itself.
- Agents are first-class users (`docs/agents.md`): an endpoint an agent could use also gets an
  MCP tool in `vault/api/mcp.py` and a line in `public/llms.txt`. POSTs that create things take
  `Idempotency-Key` (`vault.api.idempotency`). Personal access tokens never get account-level
  powers (`account_user`).
- After changing any `public/*.jsx`, rebuild the bundle: `npm --prefix web run build` (run
  `npm --prefix web ci` once). Commit `public/app.bundle.js` with the change;
  `tests/test_frontend_build.py` fails when it is stale. A new view file goes in `SOURCES` in
  `web/build.mjs`.
- Keep `public/styles.css` and its design tokens unchanged. Layout additions (navigation, small
  screens) go in `public/layout.css`, using those tokens. Views live in the URL hash
  (`#/browse`, `#/sets/MKM`): change views with `setRoute`, and open overlays (card drawer,
  account panel) with `openCard`/`openAccount` so the browser's Back button closes them.
- Scryfall: the browser never calls Scryfall's API. It asks the Vault (`/api/v1/cards/lookup`,
  `/api/v1/catalog/sets`, served by `vault/catalog.py` through one rate-limited client per
  process). Only images are hotlinked from cards.scryfall.io. Bulk prices come from the daily
  `jobs/sync_prices.py` run.
- Multi-tenant and private by default (see `docs/gdpr.md`): scope every query by the signed-in
  user, reach other users' data only through `vault.sharing`, and answer 404 (never 403) for ids
  that aren't the caller's. Add a `tests/test_tenancy.py` case for every new endpoint taking an id.
- Every new per-user table must be added to `vault.privacy.personal_data` (erasure; a test enforces
  this) and to `export_archive` (data export), and documented in `docs/gdpr.md` and `public/privacy.html`.
- Attribution is a requirement, not decoration (README → "Attribution"): keep the footer, the
  Fan Content notice, artist credits and source links; never crop card images; keep the app free.
  New services or libraries go on `public/credits.html`.
- Tests never reach real services: use the twin universe (`twins/`, `docs/twins.md`). When the
  Vault starts using a new endpoint or field of an outside service, teach the twin and add a
  conformance check in `tests/conformance`. A new outside service gets a new twin.
- Run `pytest` before pushing, with `VAULT_TEST_DATABASE_URL` set to a Postgres database the tests
  may wipe (README → Run it locally). The Vault runs on Postgres only, in the tests too.
- CI and deployment security (README → Security, enforced by `tests/test_workflows.py`): only
  merges to `main` deploy; secrets live in the `vercel-production` environment and reach only
  jobs that run for `main`, never on push/PR; read-only `permissions`, `persist-credentials: false`,
  pinned tools, values through `env` (no `${{ }}` in scripts), integrity hashes on CDN scripts.
- Schema changes need a migration (Alembic, `vault/migrations`): change the model, then
  `DATABASE_URL=postgresql://vault:vault@localhost:5432/vault alembic revision --autogenerate -m "what changed"`,
  review the file, and commit it. The app applies migrations at
  startup (`Database.migrate`); `tests/test_schema_migrations.py` fails if models and migrations
  disagree. Never edit a migration that has shipped: add a new one.
- Skills and the plugin: the skills live in `skills/` (Agent Skills format; `description: >-`, `metadata.vault-tools`
  listing the MCP tools they use). `plugins/the-vault/`, `.claude-plugin/marketplace.json` and `public/connect.html`
  are generated: after changing a skill or `scripts/build_plugin.py`, run `python scripts/build_plugin.py` and commit
  (`tests/test_plugin.py` fails when stale; `tests/test_skills.py` checks the skills against the real tool list).
  See `docs/skills.md`.
- Provenance and compliance: every MCP tool answer that carries Scryfall, Wizards or other third-party data carries
  `provenance` (`vault/provenance.py`, `docs/compliance.md`); never present source material as the Vault's own.
  A new tool goes in `SCRYFALL_DATA` or `OWN_DATA_ONLY` (`vault/api/mcp.py`) or has its own `provenance=`.
  New data sources stay off until their terms are checked (`CATALOG_SOURCES`, `docs/data-sources.md`).
- Picking up work (`docs/triage.md`): check issues and PRs updated since you last looked for overlap; claim an issue
  (`in-progress`, assign yourself, comment) before working; pick only `status:ready` issues, highest priority first;
  never implement `status:needs-refinement` (help refine it with research or a design PR instead) and skip
  `status:blocked`; close only after every acceptance criterion has evidence posted in the issue (`AGENTS.md`: merged is
  not deployed is not verified; PRs say `Refs #n`, never `Closes #n`); file defects you find as issues.
- The Comprehensive Rules are not stored: they are read live from Wizards (`vault/rules_live.py`, `docs/rules-index.md`).
  Do not add a copy of the rules text to the database, the repository or a fixture that is not invented.

## Before any push (AGENTS.md section 7)

Run `python scripts/prepush.py` (add `--full` in the background when you changed shared behaviour) before pushing a branch or
opening a pull request. A pull request that CI turns red wastes the owner's GitHub Actions minutes: fix it locally first.
