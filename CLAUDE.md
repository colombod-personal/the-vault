# The Vault

MTG collection manager: FastAPI server (`vault/`) on top of the `mtg-toolkits` library, with the
React prototype as the front end (`public/`). Read `README.md` first.

- Library logic (CSV formats, Scryfall matching, deltas, decklists) belongs in
  `colombod-personal/mtg-toolkits`, not here. Bump the pinned commit in both `pyproject.toml`
  and `requirements.txt` together.
- Never commit collection data (CSV exports, collection.json). `.gitignore` blocks them.
- One API for web and native apps: `/api/v1` (`docs/api.md`). Every list is paged with a cursor
  (`vault.api.hal.paginate`, max 500); never add an endpoint whose response grows with the
  collection. Add `_links`, a response model (OpenAPI drives the Swift client) and a test.
  `public/lib/api.js` rebuilds the prototype's `collection.json` shape from the pages.
- Agents are first-class users (`docs/agents.md`): an endpoint an agent could use also gets an
  MCP tool in `vault/api/mcp.py` and a line in `public/llms.txt`. POSTs that create things take
  `Idempotency-Key` (`vault.api.idempotency`). Personal access tokens never get account-level
  powers (`account_user`).
- Keep `public/styles.css` and its design tokens unchanged. Layout additions (navigation, small
  screens) go in `public/layout.css`, using those tokens. Views live in the URL hash
  (`#/browse`, `#/sets/MKM`): change views with `setRoute`, and open overlays (card drawer,
  account panel) with `openCard`/`openAccount` so the browser's Back button closes them.
- Scryfall: the browser may call `/cards/collection` at most every 500 ms; bulk prices come from
  the daily `jobs/sync_prices.py` run.
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
- Run `pytest` before pushing.
- CI and deployment security (README → Security, enforced by `tests/test_workflows.py`): only
  merges to `main` deploy; secrets live in the `vercel-production` environment and reach only
  jobs that run for `main`, never on push/PR; read-only `permissions`, `persist-credentials: false`,
  pinned tools, values through `env` (no `${{ }}` in scripts), integrity hashes on CDN scripts.
