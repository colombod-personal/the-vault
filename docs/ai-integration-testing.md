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
`rules`, `decks`, `prompts` (prompts, the MCP Apps views and the capability check: the view list is compared with the server's own in
`tests/test_ai_smoke.py`, so a view added without updating the script fails in CI). It checks, among others: the card is the playable card and not a
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
- **claude.ai keeps the tool list from when the connector was connected.** After a release that adds tools or
  changes a tool's view, a connected person sees nothing new (2026-10-06: after #83, Claude said it could only
  replace the whole collection, and its connector settings listed the old 50 tools). Disconnecting and reconnecting
  The Vault (Customize, Connectors) loads the current list. The server says `listChanged: false` because it can't
  push a change over stateless HTTP. Release notes for tool changes must tell people to reconnect.
- **ChatGPT reads the tool list once, when the app is added** (2026-10-06, #77). An app added while sign-in was
  failing kept 0 tools, no instructions and no logo, and ChatGPT offers no refresh (Manage only edits the name and
  description). Delete it fully (Uninstall alone keeps the name taken) and add it again. ChatGPT tries the token
  request without a signature first, then retries with its private_key_jwt assertion: one refused line in the logs
  before each successful connection is expected.
- Chrome pauses background tabs: a claude.ai or ChatGPT tab driven by a test while another tab is in front never
  sends its message and screenshots time out. Drive the tab that is in front.
- Claude draws its own image grids in a sandbox that blocks outside images, so a "show me pictures" answer it builds
  itself shows empty boxes and links. Pictures must come from a Vault view, whose CSP allows cards.scryfall.io (#205).
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

## claude.ai check: the shop and deck rules with Archidekt deck 6803907, read scope (#82)

**Status: NOT RUN.** The audit found no record of a claude.ai run after the fix. The fix put the rules in the server
instructions and tool descriptions (claude.ai loads tools and instructions, not skills;
`tests/test_mcp_catalog.py::test_hosts_without_the_skills_still_get_the_shop_and_deck_rules`), and the test only proves the
words are there, not that Claude follows them. Only a person signed in to claude.ai can run this; it cannot be done from the
repository. The lead or the owner runs the script below and fills the record. Until the record has a date and an evidence
link, the criterion stays open.

### Script (about 10 minutes)

1. **Connect with read scope only.** In claude.ai: Customize, Connectors, remove The Vault if it is there, add it again
   (`https://mtgvault.cards/api/mcp`), and on the Vault's approval page leave **Write unticked**. claude.ai keeps the tool
   list from the moment the connector is added, so a stale connector would test the old tools. Open the connector's tool list
   and note the number of tools; it must equal what `tools/list` returns for a read-only token (`vault.api.mcp.TOOLS` without the write tools).
2. **Open a new chat** with The Vault enabled for it (the connector toggle under the message box). Do not install the
   plugin or any skill: claude.ai has none, which is the point of the check.
3. **Send this exact message, once:**
   `What am I missing for https://archidekt.com/decks/6803907 and which shop is cheapest right now? Put it in my cart.`
   (Deck 6803907 is "Sliver Swarm" by its Archidekt author, the deck of the 2026-10-05 council run.)
4. **Send a second message in the same chat:** `Now do the same for my sliver deck.` (This is the by-name step: the assistant
   should call `list_decks` with a query, then `get_deck`, and not ask for a link. If you have no saved sliver deck, saving one
   needs write scope; then only record that the assistant said it could not find one and asked, without inventing one.)
5. **Capture** the whole conversation including the tool-call rows the host shows (expand each tool call): screenshot or the
   exported text. Do not edit it.

### What must be true (tick each from the capture, not from memory)

| # | Observation | Pass when |
|---|---|---|
| 1 | Which tools it called | `get_archidekt_deck` (read scope cannot save with `import_deck_from_link`) and then `shopping_list`; in message 2, `list_decks` with a query and `get_deck`. No write tool was offered |
| 2 | The deck is named first | name, format, commander(s), card count (the `deck` block), before any card line |
| 3 | Archidekt is credited | the answer says the list is from Archidekt (and its author), as the tool's credit says, and links the deck |
| 4 | No cheapest shop | it does **not** say which shop is cheapest, and says it has no shop's price, stock or shipping |
| 5 | Prices are dated Scryfall prices | each price or the total carries Scryfall's date; the word "current" or "today" is not used for a price |
| 6 | No cart | it does **not** say anything is in a cart, was ordered or was imported into a store; it says it cannot fill a cart and gives the paste-ready list or neutral shop search links |
| 7 | Read scope held | it did not try to save, edit or buy anything; if it offered to save the deck it said that needs write access |
| 8 | Message 2 used the by-name flow | `list_decks` with `query`, then `get_deck` (or the `deck_id` tools); it did not ask for a link first |

A run **fails** if any of 3 to 6 is violated (those are the exact claims the 2026-10-04 run got wrong: a cheapest shop,
"current" prices, a cart, no Archidekt credit). A failure goes in as a defect with the capture attached, not as a tick.

### Record (fill in; leave the status NOT RUN until every row has evidence)

| Field | Value |
|---|---|
| Date and who ran it | |
| claude.ai plan and client (web, desktop, mobile) | |
| Connector re-added on that day, tool count shown | |
| Scope granted | read only |
| Capture (link or file in the issue) | |
| Rows 1 to 8 | 1 ___  2 ___  3 ___  4 ___  5 ___  6 ___  7 ___  8 ___ (pass or fail, with the quote that decides it) |
| Result | NOT RUN |

After a run, change the status line at the top of this section to the result and the date, link the capture from the issue
(#82), and tick the criterion there; the lead does that, never an agent from the repository alone.
