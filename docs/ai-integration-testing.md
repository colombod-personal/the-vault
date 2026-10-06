# Testing the AI integration

The unit tests use small synthetic data. Running the tools against **real data** found four bugs they missed on
2026-10-04 (price dropped from the card answer, rules search too strict, a token shadowing a card, a plan rejected for
a problem the deck already had; PR #70). `scripts/ai_smoke.py` keeps that check: it drives a running Vault's MCP server
the way an agent would, on real cards and rules, and exits non-zero if an answer a skill depends on is wrong.

## Run it

```bash
VAULT_URL=http://localhost:8010 VAULT_TOKEN=vault_pat_... python scripts/ai_smoke.py
python scripts/ai_smoke.py --url https://mtgvault.cards --token vault_pat_... --only rules
```

A read-only personal access token is enough (Account → Agents & API); nothing is written. Groups: `connection`, `cards`,
`rules`, `decks`, `prompts` (prompts and MCP Apps views). It checks, among others: the card is the playable card and not a
token; the answer has a dated price; a plain-language rules search finds rules; a verbatim quote verifies and an invented
one fails with the true text; `present_steps` flags an invented rule number; a 100-card deck is legal; every upgrade
candidate is within budget; a bad plan is caught; provenance and the Fan Content notice are present; the view pages
insert text only.

## A local Vault with real data

```bash
# 1. Postgres 14+ with the pg_trgm extension (Docker's postgres:16 has it)
# 2. real data: download Scryfall's oracle-cards, rulings, oracle-tags bulk files and the Comprehensive Rules .txt, then
DATABASE_URL=postgresql://... python -m jobs.sync_catalog --sources oracle_cards,rulings,oracle_tags,rules \
  --file oracle_cards=oracle-cards.jsonl.gz --file rulings=rulings.jsonl.gz --file oracle_tags=oracle-tags.jsonl.gz --file rules=cr.txt
CATALOG_SOURCES=oracle_prices DATABASE_URL=... python -m jobs.sync_prices      # downloads default-cards (about 75 MB)
# 3. run it and sign in (DEV_LOGIN=1 gives a "Local dev sign-in" button), then create a token in the account panel
DEV_LOGIN=1 DATABASE_URL=... uvicorn --factory vault.app:create_app --port 8010
```

Whether the sources may be loaded in production is a separate decision (`docs/compliance.md`); loading them on a local
machine for testing is what this page describes. The first load takes about 40 seconds, a repeat load 6 seconds.

## What this does not test

A model. The smoke test checks that the tools give a model what it needs; it does not run one. Running a real assistant
against the Vault (for example `claude -p` with the plugin, or ChatGPT with the connector) still needs a signed-in
assistant, so it is a manual check: connect it (`public/connect.html`), then ask the questions in the skills' descriptions
and read what it cites. On 2026-10-04 the Claude CLI connected to a local Vault, listed all the tools and loaded the six
skills, but its own sign-in had expired, so no model call could be made.

Also not covered: the OAuth connect flow with a real host (`docs/mcp-oauth-host-checklist.md`), and the MCP Apps views in
real hosts (`docs/mcp-apps.md`).

## Gotchas found while setting it up

- The embedded Postgres in the Python package `pgserver` has no `pg_trgm`, so the catalog migration fails and a
  misspelled card name returns a 500. Use a full Postgres (Docker, or a normal install).
- Docker Desktop on Windows can fail to start after an OS update; the smoke test needs only a Postgres, not Docker.
- Name-only collection imports price an arbitrary printing (see `docs/usability-review.md`); use files with set and number.

## Expert council: a real run (2026-10-05)

Deck: Sliver Swarm tuned with rage (Archidekt 6803907, deck data Archidekt's), commander Sliver Overlord; goal "tune it".
Run against production (card data and the live rules loaded that day) by six agents using only the Vault's tools:
facts first (one agent), then five members in parallel (Commander expert, casual table, synergy analyst, collection
analyst, judge), then the devil's advocate with the plan validated by `validate_deck_changes` (valid, 2 adds, 2 cuts,
100 cards). A cold run cost about 100 seconds for the facts, 5 to 7 minutes for the members in parallel, and 5 minutes for
the challenge.

**What the council said (summary)**
- **Plan:** cut Harmonic Sliver and Tempered Sliver; add Intruder Alarm (best add) and a protection or interaction spell
  at instant speed; Heart Sliver is the weakest add (gives opponents' Slivers haste; Blur Sliver is tighter).
- **Agreed:** legal in Commander; about Bracket 3 by play (repeatable tutor and steal, stacking lords), opinion; the steal
  is permanent (rule 611.2a) but ends if the Sliver changes zones (400.7); Crystalline Sliver's shroud stops the commander's
  steal (702.18a); Harmonic Sliver destroys the deck's own artifacts and enchantments (mandatory targeted trigger).
- **Disputed:** how strong the token engine is. The synergy analyst called it unbounded; the devil's advocate showed one
  multiplier only breaks even, net mana needs two, and haste (302.6) is the real gate.
- **Not checked:** the Bracket 3 placement (no Game Changers tool), simulation numbers (`simulate_draws` was not deployed
  to the connector yet), shop prices.

**What the run found about the product** (each filed)
- Production answered 500 / "server isn't responding" to parallel calls, most succeeded on retry (#169).
- The synergy engine is real but `find_combos` cannot see it, so "no infinite combos" is unsafe to tell a pod: the tool
  said 0 combos. The council now says so.
- No Game Changers count in deck stats although Scryfall's data has the flag.
- `get_deck_overlap` was not in members' tool lists (the connector's list predates it), so one member counted by hand.
