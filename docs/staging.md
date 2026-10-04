# Staging: test the real path before it goes live

Every merge to `main` deploys to `https://mtgvault.cards`. To test what real users hit (OAuth
consent in a browser, the MCP server behind HTTPS, claude.ai connecting) before that, push the
change to the `staging` branch first.

- **Branch:** `staging` deploys as a Vercel Preview (`ignoreCommand` allows `main` and `staging`).
  Its URL is the branch alias, e.g. `the-vault-git-staging-wintermute-team.vercel.app`.
  `BASE_URL` is left unset there, so the app uses that URL (`vault/config.py`).
- **Database:** `DATABASE_URL` is set for the `staging` branch only (Vercel → Environment
  Variables → Preview → `staging`) and points at a schema-only Neon branch named `staging`: no
  collections or accounts are copied from production. Production's `DATABASE_URL` is untouched.
- **Flow:** merge the feature PR into `staging`, run `scripts/ai_smoke.py` and the claude.ai
  connector check against the staging URL, then open a PR from `staging` to `main`.
- **Why it exists:** the consent form bug (Origin `null` after `Referrer-Policy: no-referrer`)
  passed every in-process test and only showed with a real browser on the real domain.
