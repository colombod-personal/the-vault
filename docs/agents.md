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
- **read** covers everything under `GET`, plus the two POSTs that only compute:
  `decks/parse` and `decks/coverage`. **write** adds imports, saving decks and sharing.
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
and 2024-11-05 are supported. It was tested with the official MCP Python SDK client.

```bash
claude mcp add --transport http vault https://<host>/api/mcp --header "Authorization: Bearer vault_pat_..."
```

Tools (the `share_id` argument reads a collection someone shared with you):

| Tool | API |
|---|---|
| `get_collection_summary` | `GET /collection` |
| `search_cards` (query, set, name, finish, condition, sort, limit ≤ 100, cursor) | `GET /collection/cards` |
| `get_card` | `GET /collection/cards/{id}` |
| `list_sets`, `get_collection_stats`, `get_value_history`, `get_acquisition_timeline` | `/collection/...` |
| `check_decklist`, `parse_decklist` | `POST /decks/coverage`, `/decks/parse` |
| `list_decks`, `get_deck`, `save_deck`*, `update_deck`* | `/decks` |
| `get_archidekt_deck` | `/archidekt/decks/{id}` |
| `list_imports`, `import_collection_csv`* (Dragon Shield, Moxfield or generic CSV) | `/imports` |
| `list_export_formats` (download links for Dragon Shield, Moxfield, Archidekt, generic CSV, text) | `/collection/exports` |
| `list_shared_with_me`, `get_shared_deck` | `/shared` |

\* write tools, listed only for tokens with the write scope.

Each tool calls the API in-process with the caller's credentials, so the API enforces every
rule: tenancy, sharing, scopes, validation and paging. Pages include a `next_cursor`, and
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
  A retried invite gets a fresh link: stored answers never keep one.
- **Errors say what to do.** Every error is problem+json with a `detail`. A 503 carries
  `Retry-After`.

## Adding to it

A new API endpoint that an agent could use also gets an MCP tool in `vault/api/mcp.py`
(with a description written for a model), a line in `/llms.txt`, and a test in
`tests/test_agents.py`.
