# The Vault

MTG collection manager: FastAPI server (`vault/`) on top of the `mtg-toolkits` library, with the
React prototype as the front end (`public/`). Read `README.md` first.

- Library logic (CSV formats, Scryfall matching, deltas, decklists) belongs in
  `colombod-personal/mtg-toolkits`, not here. Bump the pinned commit in both `pyproject.toml`
  and `requirements.txt` together.
- Never commit collection data (CSV exports, collection.json). `.gitignore` blocks them.
- The front end reads `/api/collection` in the prototype's `collection.json` shape; keep that shape
  stable (add fields, don't rename).
- Keep `public/styles.css` and its design tokens unchanged.
- Scryfall: the browser may call `/cards/collection` at most every 500 ms; bulk prices come from
  the daily `jobs/sync_prices.py` run.
- Multi-tenant and private by default (see `docs/gdpr.md`): scope every query by the signed-in
  user, reach other users' data only through `vault.sharing`, and answer 404 (never 403) for ids
  that aren't the caller's. Add a `tests/test_tenancy.py` case for every new endpoint taking an id.
- Every new per-user table must be added to `vault.privacy.personal_data` (erasure; a test enforces
  this) and to `export_archive` (data export), and documented in `docs/gdpr.md` and `public/privacy.html`.
- Run `pytest` before pushing.
