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

## Parallel use: the load test (#169)

On 2026-10-05 five agents calling the production MCP server at the same time got 500s and "server isn't responding" (`get_card_oracle`
several times, `get_deck` twice); retrying worked. Production logs were not available to the work below (the Vercel connector is not
authorised), so **what the failures were in production is not proven from a log**. What is proven is what the code does under that
shape of load, on a machine where it can be repeated.

### What was wrong (found by reading the code, then reproduced; each has a test in `tests/test_parallel_load.py` that failed before)

1. **A tool call needed up to three database connections at once.** The MCP request held one for its whole life (its session),
   the in-process API call opened a second, and the catalog's rate limit counted in a third. With a pool of 2+2 two calls
   starved each other: each held its connection and waited for another. Measured on the old code with that pool (5 agents, 20
   calls, one instance): **2 answers, 18 failures, after 30 s waits, 90 s in all.** #176 made the pool 6+14 and the wait 8 s, which
   hides it until a few more calls are in flight. Now a request holds one connection at a time (the MCP endpoint closes its own
   session before it runs the tool; the rate limit counts in the request's session), so calls queue for the pool and never wait for
   themselves: the same 2+2 pool now answers all 100 calls in 2.5 s. Test: a pool of **one** connection serves 30 parallel calls.
2. **A refused connection was not retried.** A Neon compute waking from suspend, or any database at its connection limit, refuses
   a connection and accepts the next one a moment later. SQLAlchemy's `pool_pre_ping` does not cover that (it checks connections
   already in the pool), so the request failed. Connections are now retried (4 attempts, 0.5 s doubling, with jitter, 8 s connect
   timeout; not for a wrong password or a missing database), and one that still fails is a **503 with `Retry-After`**, never a 500.
3. **A cold start could crash the instance.** `create_app` migrates at import; a database still waking raised out of the import, so
   the function instance failed. The instance now comes up and the first request that needs the database finishes the migration.
4. **A failing or slow Wizards held the whole server.** The rules lock was held during the download (30 s timeout) and a failed read
   was not remembered, so ten parallel rules calls meant ten timeouts in a row, each holding a thread (the server has 40). A failed
   read with nothing cached is now remembered for 20 s (calls answer 503 at once, with `Retry-After`), a stale edition is served while
   one call refreshes it, and a download that is not text or not rules is the same 503, not a 500.
5. **The Archidekt path held a connection and a thread for minutes.** It held the database session during the call, whose client
   waits 30 s after a 429 and tries three more times (over the function's 60 s limit). The call is now bounded (about 10 s), holds no
   connection, is made once per deck per instance however many agents ask at the same moment, and a 429 is a 503 with `Retry-After`.
6. **Failures were bare.** An exception nobody planned for was a plain "Internal Server Error"; in a tool call a generic message
   with no hint. Now: problem+json with `request_id` and `retry_after_seconds` (and the `X-Request-Id` header); on the MCP endpoint a
   JSON-RPC error (`-32603`, `data.retryAfterSeconds`, `data.requestId`); in a tool result `isError` with `status`,
   `retry_after_seconds` and `request_id`. The per-person limits answer 429 with `Retry-After` **and** "Try again in N seconds." in the
   text, and over MCP the retry time is in the result (it used to be lost with the header).

### Run it

```bash
# a running Vault, production included (a read-only personal access token is enough; nothing is written)
python scripts/load_test.py --url https://mtgvault.cards --token vault_pat_... --deck-id 12 --clients 5 --calls 20 --burst 2

# everything on this machine: Postgres (a disposable database: it is emptied), the twin universe for Wizards and Archidekt,
# and --workers separate server processes, each with its own connection pool
python scripts/load_test.py --local --database-url postgresql://small_role:pw@localhost:5432/load_db --workers 2
```

Five agents make 20 tool calls each, two in flight at once per agent (an assistant running its tool calls in parallel), mixing
`get_card_oracle` (also with a typo, for the fuzzy path), `search_rules`, `get_rule`, `get_collection_summary`, `list_decks` and,
with `--deck-id`, `get_deck`, `deck_stats`, `deck_legality` (`--archidekt-id` adds `get_archidekt_deck`). It prints, per tool, how
many answers were fine, an honest 404, a 429, a 503, another 5xx, no answer at all (timeout, refused connection) or a JSON-RPC error,
with the p50, p95 and maximum time. **It exits 1 on any 5xx (a 503 too), any call with no answer, any JSON-RPC error, and any 429 or
503 that came without a retry hint.** A 429 is not a failure, but it is reported: the catalog allows 60 calls a minute per person and
deck analyses 30, so run production once a minute, not in a loop. `--local` also runs three more cases: Wizards down on cold instances
(the rules tools answer 503 with a hint and nothing else is affected), the catalog tools only, and Archidekt answering in 2 s.

### Measured on this machine (2026-10-07)

Windows 11, Postgres 16 in Docker on the same machine, a catalog of 5,000 invented cards, a collection of 3,000 rows, a 100-card deck,
the twin universe standing in for Wizards and Archidekt. The database role had **`CONNECTION LIMIT 12`** (a small compute's order of
magnitude) and there were two server instances, so the instances' pools together could want more than the database gives.

| Case | Before (main at 784dc45) | After |
|---|---|---|
| 5 agents x 20 calls, mixed tools, 2 instances | 32 ok, **68 failed** (60 server errors, 8 refused or dropped); 260 tracebacks in the log | 100 ok, **0 failed** |
| 5 agents x 20 calls, catalog tools only | 17 ok, **83 failed** | 100 ok, **0 failed** |
| 5 agents x 20 calls, pool of 2+2 and a 30 s wait (the original #169 settings), 1 instance | 2 ok, **18 failed**, 90 s | 100 ok, **0 failed**, 2.5 s |
| 12 agents x 20 calls, 3 at once each (36 in flight), mixed tools, default pools | not run | 201 ok, 14 rate limited (429), **25 clean 503 "could not reach its database"** with a retry hint, 0 other failures |
| the same with `DB_POOL_SIZE=3 DB_MAX_OVERFLOW=2` (2 x 5 stays under the role's 12) | not run | 222 ok, 18 rate limited, **0 failed**, 3.1 s instead of 7.0 s |
| Wizards down, cold instances | not measured on the old code (the twin answers an outage at once; a hanging Wizards, item 4, is read from the code and covered by a test) | 12 rules calls answered 503 at once with a retry hint, 38 other calls ok |
| Archidekt answering in 2 s | | the 5 Archidekt calls took 2 s (p50 1965 ms); the other 45 calls were not delayed (p95 under 200 ms) |

A default run of `--local` (all four cases, 2 instances) ends with `FAILURES 0` in each; the p95 of every tool was under 0.5 s (the 2 s Archidekt calls aside).
On a database with no connection limit the old code also passed 5 x 20 (0 failures): the failures need a pool smaller than the work, a
connection limit, or the nested connections, which is why the original 2+2 pool failed and the 6+14 pool of #176 held in the
production check of 2026-10-06 (about 85 calls, no 5xx).

**Tuning for a database with its own connection limit** (a direct Neon URL, a small role): the server keeps `DB_POOL_SIZE` (6) idle
connections and opens up to `DB_MAX_OVERFLOW` (14) more per instance, and gives up waiting for a free one after `DB_POOL_TIMEOUT` (8 s,
then 503). Set them so that instances x (size + overflow) stays under the limit; calls beyond that queue instead of failing. Neon's
pooled URL (host `...-pooler...`, PgBouncer) takes many client connections, so the defaults are fine there. `NullPool` was considered
and rejected: a new TLS connection per request costs more than it saves, and an instance serves many requests. Also tunable:
`DB_CONNECT_TIMEOUT` (8 s), `DB_CONNECT_ATTEMPTS` (4), `DB_CONNECT_RETRY_DELAY` (0.5 s).

### What this cannot show

- **Neon.** There is no scale-to-zero here: a compute that is asleep, how long it takes to wake, whether its pooler refuses connections
  while it does, are not simulated. The retry (item 2) is tested with a connection that is refused twice, not with Neon.
- **Vercel.** One instance here is one process on a fast machine; Vercel's concurrency per instance, how many instances start at
  once, cold-start time and the 60 s limit are not reproduced. Latencies are those of localhost.
- **The real services.** Wizards and Archidekt are the twins (`docs/twins.md`), the catalog is invented, 5,000 names rather than
  30,000.
- **uvicorn's own `--workers` on Windows.** With it, in 3 of about 20 runs a few first requests were never read until the client
  gave up (the log showed them only after the disconnect). It did not happen in any run with separate processes, which is what
  `--workers` now means and what Vercel does. It was not diagnosed further; it is mentioned so nobody mistakes it for the Vault.

### The production confirmation (a real run, after deploy)

The criterion "a parallel load test on production shows no 5xx" is met on this machine and **not yet on production**. After the deploy:

1. `python scripts/load_test.py --url https://mtgvault.cards --token <a read-only token> --deck-id <a saved deck> --clients 5 --calls 20 --burst 2`
   once, and post its output in the issue. Pass: exit code 0 and `FAILURES 0`. Run it when the person has not used the catalog in the
   last minute (60 a minute; the default mix makes about 45 catalog calls and 22 deck analyses).
2. Look in Vercel's runtime logs for lines with `"event"` (one JSON object per line, logger `vault.access`). Every answer carries
   `X-Request-Id`, which is Vercel's own request id, so a failed call found in a client is found in the log by it.
   - `mcp_tool`: `tool`, `status`, `duration_ms`, `pool` (`checked_out` of `size` + `max_overflow`: close to the sum means the pool is the limit).
   - `slow_request` (3 s or more) and `server_error` (a 5xx that was not an exception, for example a 503 for a busy pool).
   - `unhandled_exception`: `error_class`, `cause_class`, `sqlstate`, `where` (file:function:line), `route`, `duration_ms`, `pool`.
   - `db_connect_retry`: `sqlstate` 57P03 or none = the database was waking or unreachable; 53300 = its connection limit. If these appear
     after quiet periods, that is Neon's cold start, and it is being absorbed; if a call still fails, the `unhandled_exception` says so.
   - `migration_deferred`, `rules_read_failed` (Wizards could not be read; `cached_edition` says whether an old one was served).
   No token, name, query, address or exception message is logged.
3. If a 5xx still shows up, its `request_id` leads to the line, and `error_class` + `sqlstate` + `where` say which of the causes above
   it is (or a new one).

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
