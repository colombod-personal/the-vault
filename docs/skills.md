# Skills, the plugin and how to install them

The Vault's MCP tools (docs/agents.md) give an assistant grounded data. **Skills** tell it how to use
that data well: look things up, verify quotes, repeat sources, never answer rules from memory. They are
plain `SKILL.md` files in the [Agent Skills](https://agentskills.io) format, which Claude Code, Codex,
Cursor, GitHub Copilot, OpenCode and many other agents read.

| Skill | For |
|---|---|
| `rules-judge` | Rules questions: look up cards and rulings, find rules, verify every quote, answer step by step |
| `interaction-explainer` | How cards interact: the stack and timing walked through with rule citations |
| `deck-upgrader` | Swaps within a budget: how the curve plays (`simulate_draws`), candidates from the Vault, then `validate_deck_changes` before presenting a plan |
| `shopping-assistant` | What a deck still needs from your collection, with dated prices and a list to paste into a store |
| `expert-council` | A council of on-topic experts reviews a deck or a rules question: independent views, a devil's advocate, then a checked plan with the discussion on request (Commander first) |
| `archidekt-deck-helper` | A public Archidekt deck checked against your collection: legality, budget upgrades, a change list to apply on Archidekt, what to buy, and shop search links |
| `collection-analyst` | Questions about your own collection: value, gains, what you own |
| `vault-attribution` | How to show Vault data: pass on provenance, never present Scryfall's or Wizards' material as the Vault's own |

## Install

**Everything at once in Claude Code** (skills and tools, asks for your token once):

```
/plugin marketplace add colombod-personal/the-vault
/plugin install the-vault@the-vault
```

**Skills only, in any agent** that reads skills:

```
npx skills add colombod-personal/the-vault
npx skills add colombod-personal/the-vault --skill rules-judge      # one skill
npx skills add colombod-personal/the-vault -g                       # for all your projects
```

(`npx skills` is [vercel-labs/skills](https://github.com/vercel-labs/skills); it copies or links the skills
into each agent's skills folder.)

**Tools** (the MCP server) in an agent without the plugin: see `public/connect.html` (also served at
`/connect.html`), or `docs/agents.md`. For editors that read an Agent Plugins folder, use
`plugins/the-vault/` (it holds `plugin.json`, `skills/` and `mcp.json`).

Sign in with your Vault account where the tool offers it (OAuth: ChatGPT, Claude.ai, and the tools on
`public/connect.html`), or with a personal access token (Account → Agents & API) kept in an environment variable
or the tool's own prompt.

## Where things live (one source of truth)

- `skills/<name>/SKILL.md`: **the skills. Edit these.**
- `plugins/the-vault/`, `.claude-plugin/marketplace.json` and `public/connect.html`: **generated** by
  `python scripts/build_plugin.py` from `skills/` and the constants in that script (version, address,
  keywords). `tests/test_plugin.py` fails when they are out of date; run the script and commit.
- `claude plugin validate plugins/the-vault` and `claude plugin validate .` check the Claude Code manifests;
  `npx skills add . --list` shows what the skills CLI finds (it parses the YAML: an unquoted colon in a
  description breaks it, which `tests/test_skills.py` also catches).

## Writing a skill

- A folder under `skills/` with `SKILL.md`: YAML frontmatter (`name` equal to the folder name, `description`
  written as when to use it, `license`, and `metadata.vault-tools`: the tools it uses, space separated),
  then the instructions.
- Use `description: >-` so colons and quotes are safe in YAML.
- Name tools in backticks. `tests/test_skills.py` checks that every declared tool exists, that the body
  names no real tool it did not declare, and that it invents no tool-like names.
- Keep skills short and procedural. Say what to call, in what order, what to verify, what to say when the
  sources do not settle a question, and what never to do. Never put rules text in a skill: skills point at
  the tools, which return the current text with its source.
- Every skill that shows Vault data points at `vault-attribution` and must keep the rules there: provenance
  always, nothing source-made presented as the Vault's own.

## Verified installs

Checked on 2026-10-04 with `npx skills add <this repo> --skill rules-judge --skill vault-attribution -a claude-code -a codex -a cursor`
from a local checkout (the GitHub path works once these files are on `main`): it found all six skills (YAML
parsed), and wrote them to `.claude/skills/` (Claude Code) and `.agents/skills/` (Codex and Cursor read the
shared folder) in about a second. `claude plugin validate` passes for `plugins/the-vault` and for the
marketplace. Not yet checked on real installs of Codex, Cursor or Copilot beyond the files the CLI wrote; a
person should run the first-question check on `connect.html` in each.

## Agents

The ready-made agents (the judge, the deckbuilder, the buyer, and the expert council's members) live in `agents/` as one neutral source. `scripts/build_plugin.py` makes the Claude Code versions (inside the plugin, limited to the Vault's own tools by a `tools:` allow list), the GitHub Copilot versions (a `tools:` allow list of `vault/<tool>`), and Codex and Cursor versions, in `agent-definitions/` (copy instructions in its README). Edit `agents/`, never the generated files; `tests/test_agent_definitions.py` checks them against the real tools and skills.

What each assistant enforces differs, and `agent-definitions/README.md` (generated) says exactly that: Claude Code and Copilot have an allow list; Codex limits the Vault server's tools (`enabled_tools`) and runs read-only but cannot take its other tools away; Cursor has only a read-only mode (its subagent format has no tool list). Sources read 2026-10-07: GitHub Docs and VS Code docs (custom agents, `tools` and `<server>/<tool>`), Codex docs (custom agent TOML, `mcp_servers`, `enabled_tools`), Cursor docs (subagents).

**Agent Plugins and agents.** The Agent Plugins 1.0 standard defines only skills and MCP servers; agents are "too client-specific" to be portable (its design notes). Client-specific files go in a top-level directory named for the client's reverse-domain namespace (spec section 8), and a client reads only its own. VS Code documents `com.github.copilot/` for GitHub Copilot's agents, so the portable plugin `plugins/the-vault/` carries the Copilot agents in `com.github.copilot/agents/` and other clients ignore that directory. No documentation was found for a Codex, Cursor or Claude Code namespace, so none is made up: the Claude Code agents stay in the plugin's `agents/` folder, Claude Code's own convention, and Codex and ChatGPT get the experts as skills in `plugins/the-vault-openai/` (a separate package, #225). If a client documents a namespace, add its directory in `scripts/build_plugin.py` (`COPILOT_NAMESPACE` is the pattern) and a case to `tests/test_plugin.py`. Listings and directories: `docs/listings.md`.
