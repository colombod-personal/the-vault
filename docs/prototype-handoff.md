# The Vault — Handoff & Deployment Guide (historical: the prototype)

> **Historical document.** This describes the original client-only prototype as it was handed
> off. It is no longer the current architecture: the Vault now has a FastAPI server
> (`vault/`), a Postgres database with migrations, a precompiled front end
> (`public/app.bundle.js`, built by esbuild), and the browser no longer calls Scryfall's API
> (the server does). "Path B" below is what was then built. For the current system see
> `README.md`, `docs/api.md` and `CLAUDE.md`.

An MTG (Magic: The Gathering) collection manager + valuation app. This is a **working client-side
application**, not just a visual mockup: it loads a real collection, computes portfolio value, pulls
live prices from Scryfall, and renders an interactive value-over-time chart.

This document covers two paths:
- **Path A — Deploy the prototype as-is** (you can have it online today, incl. on your Linux server).
- **Path B — Rebuild as a production app** (hand off to Claude Code / a developer).

---

## 1. What the prototype was (architecture at handoff)

It's a **static, client-side site**. No server, no build step, no database.

```
The Vault.html          Entry point. Loads React + Babel from CDN, then the scripts below.
styles.css              All styling. CSS custom properties (oklch) define the theme.
app.jsx                 Root component: router, data loading, drawer, the price-refresh logic.
views/
  dashboard.jsx         Overview: totals, top sets, Market Value tile (→ Valuation).
  valuation.jsx         Value-over-time chart + monthly ledger + "Update now" / bulk sync.
  browse.jsx            Card browser / search.
  sets.jsx, setIcon.jsx Per-set breakdowns.
  graph.jsx             Color/type graphs.
  deck.jsx, lab.jsx     Deck tools.
lib/
  scryfall.js           Scryfall API client: per-card batch lookup, bulk file sync, localStorage cache.
  setIcons.js, deck.js  Helpers.
data/
  collection.json       THE DATA. Pre-computed collection snapshot (cards, sets, meta, timeline).
  raw_collection.csv    Original CSV export the JSON was built from.
```

### How it works at runtime
- **JSX is transpiled in the browser** by Babel Standalone (`<script type="text/babel">`). Fine for a
  prototype; for production you'd precompile (see Path B).
- **Data is a static file.** `data/collection.json` is the whole collection, baked in. Editing your
  collection means regenerating that file from `raw_collection.csv`.
- **Live prices come straight from the browser** → `api.scryfall.com`. The app caches results in
  `localStorage` and recomputes market value from them.
- **Valuation history is derived, not recorded.** The chart plots your *current* holdings valued at the
  *latest* prices, bucketed by each card's acquisition date. It is NOT a record of historical market
  prices (Scryfall doesn't give history, and nothing here stores daily snapshots yet). This is the #1
  thing to upgrade in Path B.

### Known constraint you already hit
Your network blocks `api.scryfall.com`, so the per-card refresh fails. The **bulk file sync** uses a
different host and may work; but the real fix is **Path B**, where a server fetches Scryfall (your
browser never touches it) — see §3.

---

## 2. Path A — Deploy the prototype as-is

Because it's static files, any static host works. Pick one.

### Option A1 — Your Linux server with a static file server
Copy the project folder to the server, then serve it. Two common ways:

**Caddy (auto-HTTPS, simplest):**
```bash
# /etc/caddy/Caddyfile
vault.yourdomain.com {
    root * /var/www/the-vault
    file_server
    encode gzip
}
```
```bash
sudo cp -r ./the-vault /var/www/the-vault
sudo systemctl reload caddy
```

**nginx:**
```nginx
server {
    listen 80;
    server_name vault.yourdomain.com;
    root /var/www/the-vault;
    index "The Vault.html";
    location / { try_files $uri $uri/ /"The Vault.html"; }
    gzip on;
    gzip_types text/css application/javascript application/json;
}
```
> Note the space in `The Vault.html`. Either keep it and quote it as above, or rename the entry file to
> `index.html` (recommended) so the server picks it up automatically.

**Quick test, no web server (just to see it on the LAN):**
```bash
cd the-vault && python3 -m http.server 8080
# then visit http://<server-ip>:8080/The%20Vault.html
```

### Option A2 — Zero-server static hosting
Drag the folder into Netlify, Cloudflare Pages, GitHub Pages, or Vercel (static). Rename the entry to
`index.html` first. Done in minutes, free, HTTPS included.

### Caveats of deploying as-is
- **Slow first paint** — Babel transpiles ~10 JSX files on every load. Acceptable for personal use.
- **Anyone with the URL sees it** — no auth. Put it behind Caddy/nginx basic-auth if you want privacy.
- **Still hits the Scryfall block** — the browser is still the one calling Scryfall. Bulk sync may dodge
  it; otherwise you need Path B.
- **Collection edits = regenerate `collection.json`** by hand.

This is the right choice if you just want *your* dashboard online and you're OK regenerating the JSON
when your collection changes.

---

## 3. Path B — Rebuild as a real production app (Claude Code handoff)

This is the meaningful upgrade. Hand this whole folder to Claude Code (or any developer) and point them
at this README. The HTML/JSX here is the **reference implementation** — same UI and logic, just moved
into a proper build with a small backend.

### Recommended target stack
- **Frontend:** Vite + React + TypeScript. The existing `app.jsx` / `views/*.jsx` port almost 1:1 —
  it's already idiomatic React (hooks, function components). Drop in-browser Babel; Vite precompiles.
- **Styling:** keep `styles.css` as-is (plain CSS + custom properties). No rewrite needed.
- **Charting:** the valuation chart is hand-rolled SVG in `valuation.jsx` and works well — keep it, or
  swap for Recharts/visx if preferred.
- **Backend:** a thin Node/Express (or Fastify) service, OR Python/FastAPI on your Linux box. Its jobs:
  1. **Proxy Scryfall server-side.** The server runs the daily **bulk-data sync** on a cron, stores
     prices in a DB. *This solves your network block entirely* — your browser only talks to your own
     server, never to `api.scryfall.com`.
  2. **Record price history.** Snapshot total value daily → the valuation chart becomes a *real*
     historical curve instead of a derived one. Biggest functional win.
  3. **Own the collection.** Move `collection.json` into a DB (Postgres) with CSV import, so you
     can add/edit cards in-app instead of regenerating a file.
- **DB:** Postgres (what the Vault uses, everywhere).
- **Deploy:** `vite build` → static frontend served by Caddy/nginx; backend as a systemd service behind
  the same reverse proxy. Or Docker Compose (frontend + backend + db) for one-command deploys.

### Suggested data model (server)
```
cards            id, name, set, collector_number, qty, condition, finish, paid, acquired_date
price_snapshots  card_key, date, usd, usd_foil          -- written daily by the bulk-sync cron
collection_value date, total_market, total_cost          -- daily rollup → powers the real chart
```

### Migration order (what to tell Claude Code)
1. Scaffold Vite + React + TS; move `styles.css` over unchanged.
2. Port `app.jsx` + each `views/*.jsx` to `.tsx` components (mechanical — they're already React).
3. Stand up the backend; move `collection.json` → Postgres; add CSV import using `raw_collection.csv`.
4. Implement the server-side Scryfall bulk-sync cron (logic mirrors `lib/scryfall.js` `bulkSync`).
5. Add the `price_snapshots` / `collection_value` tables; rewrite the valuation chart to read real
   recorded history.
6. (Optional) Auth, multi-user, mobile layout.

---

## 4. Design tokens (so the rebuild matches exactly)

All defined as CSS custom properties at the top of `styles.css`. Theme is a dark "vault" palette in
**oklch**. Key tokens (read the `:root` block for the full set):
- Backgrounds: `--bg`, `--bg-2`, `--surface`, `--surface-2`
- Accents: `--gold` (primary), `--copper` (cost basis), `--good` (gains), `--danger` (losses)
- Text: `--text`, `--text-2`, `--muted`
- Borders/radius: `--border`, `--border-2`, `--radius`
- Fonts: `--display`, `--mono` (used heavily for numbers/labels)

The valuation chart uses: **gold** = market value line, **copper** = cost basis line, gold gradient
band between them = unrealised gain.

---

## 5. Files included in this bundle
- `The Vault.html`, `app.jsx`, `styles.css`
- `views/` (all view components)
- `lib/` (scryfall client + helpers)
- `data/collection.json` (the data) and `data/raw_collection.csv` (original source)

The README is self-contained — a developer who wasn't in this conversation can implement from it alone.
