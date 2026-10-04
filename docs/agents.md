# Agents on the Vault

People can connect their own AI agents to their own collection: Claude, ChatGPT, scripts,
whatever they build. There are two doors:

1. **MCP server:** `POST /api/mcp`. Any MCP client (Claude Desktop and Code, IDEs, agent
   frameworks) gets typed tools with descriptions, and needs no code.
2. **HTTP API:** `/api/v1`. It is hypermedia (follow `_links` from `GET /api/v1`) and has an
   OpenAPI description, for code-writing agents and scripts.

Discovery: `GET /api/v1` links to `mcp` and `llms`, and `/llms.txt` explains the whole
thing to an agent in one page.

## Personal access tokens

Created in Account → Agents & API, or with `POST /api/v1/me/tokens`
(`{"name", "scopes": ["read"] | ["read", "write"], "expires_in_days": 1–365}`).

- The token (`vault_pat_…`) is shown once. Only its SHA-256 is stored.
- **read** covers everything under `GET`, plus the POSTs that only compute or revoke the token
  itself: `decks/parse`, `decks/coverage`, `cards/lookup`, `auth/revoke` and the deck computations (`decks/stats`, `decks/legality`, `decks/upgrades`, `decks/validate-changes`, `decks/combos`, `decks/shopping-list`). **write** adds imports, saving decks and sharing.
  Without write, a change answers `403` with `WWW-Authenticate: Bearer error="insufficient_scope"`.
- Tokens can never manage the account: they can't create tokens, delete the account, export
  it, or manage app sessions. Those need the person, on the web or in the iOS app. A leaked
  token therefore can't lock the owner out or escalate itself.
- A token can revoke itself (`POST /api/v1/auth/revoke`), and the owner can revoke any token.
  Tokens are listed with their last use, erased with the account, and appear, without the
  secret, in the data export.

## MCP

Stateless Streamable HTTP. Each JSON-RPC request gets one `application/json` answer, with no
session id and no SSE, which fits serverless hosting. Protocol versions 2025-06-18, 2025-03-26
and 2024-11-05 are supported. It was tested with the official MCP Python SDK client. A JSON-RPC
batch (older protocol versions) holds 1 to 20 calls; an empty or larger one is refused (`-32600`).

```bash
claude mcp add --transport http vault https://<host>/api/mcp --header "Authorization: Bearer vault_pat_..."
```

Tools (the `share_id` argument reads a collection someone shared with you):

| Tool | API |
|---|---|
| `get_collection_summary` | `GET /collection` |
| `search_cards` (query, set, name, finish, condition, printing, sort, limit ≤ 100, cursor) | `GET /collection/cards` |
| `get_card` | `GET /collection/cards/{id}` |
| `list_sets` (query, sort), `get_collection_stats` (limit), `get_value_history`, `get_acquisition_timeline` | `/collection/...` |
| `get_collection_breakdowns`, `get_valuation` | `GET /collection/breakdowns`, `/collection/valuation` |
| `list_card_names` (sort, colors, type, min_value, limit, cursor) | `GET /collection/names` |
| `refresh_prices`* (cursor, force) | `POST /collection/refresh` |
| `check_decklist`, `parse_decklist` | `POST /decks/coverage`, `/decks/parse` |
| `list_decks`, `get_deck`, `save_deck`*, `update_deck`* | `/decks` |
| `get_archidekt_deck` | `/archidekt/decks/{id}` |
| `lookup_cards` (any card by id, set + number, or name; up to 75) | `POST /cards/lookup` |
| `list_imports`, `import_collection_csv`* (Dragon Shield, Moxfield or generic CSV) | `/imports` |
| `list_export_formats` (download links for Dragon Shield, Moxfield, Archidekt, generic CSV, text) | `/collection/exports` |
| `list_shared_with_me`, `get_shared_deck` | `/shared` |

\* write tools, listed only for tokens with the write scope.

Each tool calls the API in-process with the caller's credentials, so the API enforces every
rule: tenancy, sharing, scopes, validation and paging. Tool arguments are checked against the
tool's schema first, with the API's limits (decklists up to 50,000 characters, ids, lookup
identifiers); a failed check is a JSON-RPC error (`-32602`), and an API error is the tool
result with `isError`. Pages include a `next_cursor`, and
answers come as `structuredContent` plus the same JSON as text. The server's `instructions`
tell the agent how to start, and remind it to credit artists and Scryfall.

## Built for flaky networks and retrying agents

- **Small answers.** Lists are pages of at most 500 items (25 by default for MCP) with cursors,
  so a dropped connection costs one page and a retry repeats one page.
- **Cheap re-checks.** Send `If-None-Match` with the `ETag`, or compare the collection's
  `version`, to skip unchanged data.
- **Safe retries.** Every GET can be repeated. For POSTs, send `Idempotency-Key: <uuid>`: the
  first answer is stored for 24 hours and replayed (`Idempotent-Replayed: true`), so an import
  or a new deck never happens twice. Reusing a key for a different request answers 422; a
  retry that arrives while the first attempt is still running answers 409 with `Retry-After`.
  On `/api/mcp` the header covers the tool call (in a JSON-RPC batch, each call separately).
  A retried invite shows the same link until the invite is accepted (then 409); it is derived again from the server's secret, and stored answers never keep it.
- **Errors say what to do.** Every error is problem+json with a `detail`. A 503 carries
  `Retry-After`.

## Adding to it

A new API endpoint that an agent could use also gets an MCP tool in `vault/api/mcp.py`
(with a description written for a model), a line in `/llms.txt`, and a test in
`tests/test_agents.py`.

## Rules, cards and deck analysis (grounded tools)

These tools answer from the Vault's catalog (docs/catalog-design.md), not from a model's memory, and every
answer carries `provenance` (docs/compliance.md): a block per source (Scryfall, Wizards of the Coast,
Scryfall Tagger, Commander Spellbook), or `computed` with the sources and versions used as `inputs`.
Nothing is dumped: a tool returns the card, rule or deck asked about, capped.

| Tool | API | Notes |
|---|---|---|
| `whoami` | `GET /agent/whoami` | who you are, scopes, data versions (rules edition, card data, rulings, tags, prices) |
| `get_card_oracle` | `GET /catalog/cards` | exact name (either face) or Oracle id; a misspelling gets suggestions, never a guess |
| `get_rulings` | `GET /catalog/cards/{oracle_id}/rulings` | newest first, at most 25 |
| `search_rules`, `get_rule` | `GET /catalog/rules/search`, `/catalog/rules/{number}` | Comprehensive Rules with the edition; at most 10 results; glossary as `glossary:Term` |
| `verify_citation` | `POST /catalog/verify-citation` | is a quote verbatim in the rule, Oracle text or ruling? Whitespace and typographic quotes are forgiven; nothing else; a failure returns the true text |
| `deck_stats`, `deck_legality` | `POST /decks/stats`, `/decks/legality` | counts, curve, color identity, roles (Tagger tags), estimated cost; legality, copies, size, commander identity, and what was not checked |
| `find_upgrades`, `validate_deck_changes` | `POST /decks/upgrades`, `/decks/validate-changes` | candidates are legal, in colors, not in the deck, within budget; the validator checks the final plan (legality, colors, resulting deck, total price) |
| `find_combos` | `POST /decks/combos` | asked of Commander Spellbook on demand; nothing stored |
| `shopping_list` | `POST /decks/shopping-list` | what you do not own, cheapest known price (dated), a list to paste into a store's own tool |

Prompts (`prompts/list`, `prompts/get`): `rules_judge`, `explain_interaction`, `upgrade_deck`,
`shopping_help`. The server's `instructions` and every prompt carry the grounding rules: look things up,
quote only verified text, repeat provenance, never present source material as the Vault's own.

Access: the catalog tools need a token like the rest. `PUBLIC_CATALOG=1` (off by default, see
docs/compliance.md "Registration") would let the REST catalog endpoints answer without an account,
rate-limited per IP; MCP always needs a token for now. `CATALOG_RATE_LIMIT` (default 60 a minute) and
30 deck analyses a minute per person apply.

Tools that return Scryfall or Archidekt data they did not compute (collection and deck tools) get a
`provenance` block added by the MCP layer. A test fails for any tool that is in neither the "Scryfall data"
nor the "own data only" group (`vault/api/mcp.py`).
