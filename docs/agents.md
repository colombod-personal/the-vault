# Agents on the Vault

People can connect their own AI agents to their own collection: Claude, ChatGPT, scripts,
whatever they build. There are three ways in:

0. **OAuth (connect by URL):** in ChatGPT or Claude, add the connector `https://<host>/api/mcp`.
   The app sends you to the Vault to sign in and choose what it may do. No token to copy.
   See "OAuth" below. Personal access tokens (below) stay for scripts and terminals.

The two doors an agent then uses:

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
  itself: `decks/parse`, `decks/coverage`, `cards/lookup` and `auth/revoke`. **write** adds imports, saving decks and sharing.
  Without write, a change answers `403` with `WWW-Authenticate: Bearer error="insufficient_scope"`.
- Tokens can never manage the account: they can't create tokens, delete the account, export
  it, or manage app sessions. Those need the person, on the web or in the iOS app. A leaked
  token therefore can't lock the owner out or escalate itself.
- A token can revoke itself (`POST /api/v1/auth/revoke`), and the owner can revoke any token.
  Tokens are listed with their last use, erased with the account, and appear, without the
  secret, in the data export.

## OAuth: connect ChatGPT, Claude and other MCP clients by URL

The Vault is an OAuth 2.1 authorization server for its own MCP server, so a host only needs the
URL. The threat model, with the tests that prove each mitigation, is in
[`mcp-oauth-threat-model.md`](mcp-oauth-threat-model.md); the manual checks against the real hosts are in
[`mcp-oauth-host-checklist.md`](mcp-oauth-host-checklist.md).

**What a client does** (all standard, so a spec-following MCP client needs no Vault-specific code):

1. `POST /api/mcp` without a token answers `401` with
   `WWW-Authenticate: Bearer realm="the-vault", resource_metadata="https://<host>/.well-known/oauth-protected-resource/api/mcp"`
   (RFC 9728; also served at `/.well-known/oauth-protected-resource`).
2. The server metadata (RFC 8414) is at `/.well-known/oauth-authorization-server`: S256 only,
   `client_id_metadata_document_supported: true`, a `registration_endpoint`, public clients only.
3. **Identify**: use an https URL as `client_id` (a Client ID Metadata Document with `client_id`,
   `client_name` and `redirect_uris`), or register with `POST /oauth/register` (RFC 7591). Metadata URLs
   are fetched with SSRF protection (https on 443, public addresses only, no redirects, 32 KB, 5 s).
4. Send the person to `GET /oauth/authorize` with `response_type=code`, `client_id`, `redirect_uri`,
   `code_challenge` + `code_challenge_method=S256`, `resource=https://<host>/api/mcp`, `scope`
   (`read` or `read write`) and `state`. The person signs in (provider or passkey), sees who is asking and
   what it may do, and answers. The redirect carries `code`, `state` and `iss`.
5. `POST /oauth/token` (form) with `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`,
   `code_verifier`, `resource` returns `access_token` (1 hour), `refresh_token` (30 days, **rotated on every
   use**) and the granted `scope`. Use `grant_type=refresh_token` to renew. `POST /oauth/revoke` (RFC 7009) revokes.

**Rules that never bend**

- Redirect URIs match exactly: https, or `http://127.0.0.1`, `[::1]` or `localhost` (any port) for apps on the
  person's own computer. No custom schemes. A bad `client_id` or `redirect_uri` shows an error page, never a redirect.
- Codes last 60 seconds, work once, and are bound to client, redirect URI, challenge, resource and person. Using one twice
  revokes what the first use issued.
- A rotated refresh token coming back (it was copied) revokes the whole grant. A refresh can narrow scopes, never widen them.
  A grant lasts at most 90 days from the person's consent, however often it is refreshed; then the app asks again.
- **read** is the default. **write** is offered only if the app asks for it, and it is an unticked box on the consent screen.
- Tokens are for `https://<host>/api/mcp` only (RFC 8707). Used on `/api/v1` directly they are refused (401).
  They never get account powers: no tokens, export, deletion, sign-in or app management (403 even on the tools' own calls).
- Only SHA-256 hashes are stored. Rate limits: `OAUTH_RATE_LIMIT` (120 a minute per IP) for authorize, token and revoke,
  `OAUTH_REGISTER_RATE_LIMIT` (20) for registration, `OAUTH_CLIENT_CAP` (2000) registered clients,
  `OAUTH_CIMD_CAP` (5000) cached metadata documents, `OAUTH_FETCH_LIMIT` (60 a minute, all callers) metadata fetches.

**Connected apps** (Account → Connected apps, or `GET /api/v1/me/apps`, `DELETE /api/v1/me/apps/{id}`): each app with its
name, web address, what was allowed, when it connected and last acted. Disconnecting ends it at once. They are in the data
export (`connected_apps.json`, no tokens) and erased with the account. These endpoints need the person (not a token) and
have no MCP tool.

## MCP

Stateless Streamable HTTP. Each JSON-RPC request gets one `application/json` answer, with no
session id and no SSE, which fits serverless hosting. Protocol versions 2025-06-18, 2025-03-26
and 2024-11-05 are supported. It was tested with the official MCP Python SDK client. A JSON-RPC
batch (older protocol versions) holds 1 to 20 calls; an empty or larger one is refused (`-32600`).

```bash
claude mcp add --transport http vault https://<host>/api/mcp --header "Authorization: Bearer vault_pat_..."
claude mcp add --transport http vault https://<host>/api/mcp   # OAuth: then /mcp in Claude Code to sign in
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

Each tool calls the API in-process with the caller's credentials (an OAuth token is accepted on those calls and on `/api/mcp`, nowhere else), so the API enforces every
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
