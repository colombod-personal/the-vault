# Directory listings and discovery

Status: **prepared, not submitted.** Every submission is a person's decision and needs the OAuth server
(M4) to be live and checked on the real hosts first. This page lists what each place asks for (from the
public descriptions I could read on 2026-10-04, so check each against the current form) and what the Vault
already has.

## What the Vault already has

- Tool annotations on every tool (`readOnlyHint`, `destructiveHint`, `openWorldHint`); all catalog and deck
  tools are read-only; write tools exist only for the person's own data and need a write scope.
- A public privacy notice (`/privacy.html`, with a placeholder section the owner still has to fill: issue #2), credits
  (`/credits.html`), a plain-language connect page (`/connect.html`), `llms.txt`, and docs in the repository.
- Provenance on every answer, the Fan Content notice, and "not endorsed by" lines.
- A free service: nothing is sold; sign-in only protects each person's own collection.

## Places

| Place | What it needs | Vault status |
|---|---|---|
| ChatGPT plugin / app directory | Organisation verification, an OAuth-capable remote MCP server, privacy policy and support URLs, submission through OpenAI's dashboard. Plus and Pro users can add any server in developer mode without listing. | The OAuth server is live and a ChatGPT connection through it was seen on 2026-10-06 (#77). Ready: the plugin ZIP (`python scripts/build_plugin.py --zip`), the reviewer account (`/reviewers`, #239) and eight test cases. Left, and only the owner can do them: organisation verification, the upload and the submission (#240) |
| Claude connector directory | A remote MCP server with tool annotations, OAuth sign-in, documentation and a test account; submission through Anthropic's form | Ready: tool annotations on every tool, the OAuth server (checked on Claude.ai on 2026-10-08), the reviewer account and eight test cases (five positive, three negative; run on the demo account, see #239). Left, and only the owner can do them: the submission under the owner's Anthropic account and the directory terms (#241) |
| Claude Code marketplace | `.claude-plugin/marketplace.json` in the repository (`/plugin marketplace add colombod-personal/the-vault`) | Done; validates with `claude plugin validate` |
| Skills (`npx skills add colombod-personal/the-vault`) | `SKILL.md` files in `skills/` | Done; works once these files are on `main` |
| Official MCP Registry | A `server.json` describing the remote server and a verified namespace (a domain or GitHub identity) | `server.json` at the repository root is ready (`tests/test_server_json.py` checks its shape, the address and the version). Left, and only the owner can do it: sign in to the registry as the GitHub account `colombod-personal` (`mcp-publisher login github`, which is what verifies the `io.github.colombod-personal` namespace) and run `mcp-publisher publish` (#60) |
| MCP Server Card (`/.well-known/mcp/server-card.json`) | A proposal (SEP-2127) whose location and format had not settled in September 2026 | Deliberately not published, to avoid a format that may change |

## Before submitting anything

1. The OAuth work is merged and the host checklist (`docs/mcp-oauth-host-checklist.md`) has been run by a person on
   ChatGPT and Claude.ai, and the results recorded (Claude.ai: 2026-10-08, #239 and #48; ChatGPT: connected 2026-10-06, a run on the
   demo account is still to do).
2. The compliance gate (`docs/compliance.md`, issue #62) is closed: terms read, permissions asked, the registration
   question decided. A directory listing shows the Vault to many more people than a repository does.
3. The privacy notice is complete (issue #2).
4. `docs/mcp-apps.md` has real-host results, so the listing does not promise views that were never seen working.
