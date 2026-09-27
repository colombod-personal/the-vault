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
- Run `pytest` before pushing.
