# Skills, the plugin and how to install them

The Vault's MCP tools (docs/agents.md) give an assistant grounded data. **Skills** tell it how to use
that data well: look things up, verify quotes, repeat sources, never answer rules from memory. They are
plain `SKILL.md` files in the [Agent Skills](https://agentskills.io) format, which Claude Code, Codex,
Cursor, GitHub Copilot, OpenCode and many other agents read.

| Skill | For |
|---|---|
| `rules-judge` | Rules questions: look up cards and rulings, find rules, verify every quote, answer step by step |
| `interaction-explainer` | How cards interact: the stack and timing walked through with rule citations |
| `deck-upgrader` | Swaps within a budget: how the curve plays (`simulate_draws`), what the collection already gives (`get_deck_ideas`, `get_card_alternatives`), candidates from the Vault, then `validate_deck_changes` before presenting a plan; saving, changing and deleting a deck |
| `shopping-assistant` | What a deck still needs from your collection, with dated prices and a list to paste into a store |
| `expert-council` | A council of on-topic experts reviews a deck or a rules question: independent views, a devil's advocate, then a checked plan with the discussion on request (Commander first) |
| `archidekt-deck-helper` | A public Archidekt deck checked against your collection: legality, budget upgrades, a change list to apply on Archidekt, what to buy, and shop search links |
| `collection-analyst` | Your own collection: value, gains and losses, spare copies, what you own, imports, small edits, buckets and tags, sharing, reset and undo |
| `vault-attribution` | How to show Vault data: pass on provenance, never present Scryfall's or Wizards' material as the Vault's own |

## Flows: the common jobs, step by step

A **flow** is a multi-step procedure for one job, written as a `## Flow: ...` section of the skill that owns it, not as a skill of its
own. Every step names the tools it calls (`Calls:`), what to show the person (`Show:`) and when to stop (`Stop:`); a step that calls a
write tool always has a `Stop:` (the person's yes to a preview). Each flow has an MCP prompt that carries the same steps for hosts
without skills (the Vault's own `prompts/list`), and `tests/test_capabilities.py` checks that the prompt and the flow name the same tools.

| Job | Flow (skill) | MCP prompt |
|---|---|---|
| Import a collection | Import a collection (`collection-analyst`) | `import_collection` |
| Evaluate a deck from a link | Evaluate a deck from a link (`archidekt-deck-helper`) | `evaluate_deck` |
| Buy what a deck is missing | Buy what a deck is missing (`shopping-assistant`) | `shopping_help` |
| A rules question, with citations | Answer a rules question with citations (`rules-judge`) | `rules_judge` |
| Tune a deck with the council | Tune a deck with the council (`expert-council`) | `council_review` |
| Sort with buckets and tags | Organise with buckets and tags (`collection-analyst`) | `organise_collection` |
| Reset or undo | Reset or undo (`collection-analyst`) | `reset_or_undo` |
| Upgrade a deck within a budget | Upgrade a deck within a budget (`deck-upgrader`) | `upgrade_deck` |

## Every capability is reachable

`docs/ai-parity.md` lists what a person can do in the web app and the tool for it. `tests/test_capabilities.py` reads that file and
the skills and agents and fails, naming the row and the tool, when a tool is in no skill (`metadata.vault-tools`) or in no agent.
An agent is read-only, so a write tool counts for an agent when the agent lists a skill that declares it: the generated agent text
says the change is left to the main assistant, which previews and asks first. `council_brief` and `expert_brief` are exempt for
agents (they are how a connector gets the agents). When you add a tool, add it to the skill that owns the job (extend a skill, do not
add a parallel one), to an agent that lists that skill (its allow list, for a read tool), to the flow's steps and to the prompt.
`tests/test_tool_names.py` checks that every tool named by an agent, a skill, a generated host file, a prompt or the server's
instructions exists, so a renamed tool fails with the file that still names it.

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

After connecting, ask the assistant to call `whoami`: it names the account, the scopes and the data versions the Vault
holds. Every skill tells the assistant to call it when a tool fails or before offering to change anything.

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

The ready-made agents (the judge, the deckbuilder, the buyer, the curator for your own collection, and the expert council's members) live in `agents/` as one neutral source. `scripts/build_plugin.py` makes the Claude Code versions (inside the plugin, limited to the Vault's own tools by a `tools:` allow list), the GitHub Copilot versions (a `tools:` allow list of `vault/<tool>`), and Codex and Cursor versions, in `agent-definitions/` (copy instructions in its README). Edit `agents/`, never the generated files; `tests/test_agent_definitions.py` checks them against the real tools and skills.

What each assistant enforces differs, and `agent-definitions/README.md` (generated) says exactly that: Claude Code and Copilot have an allow list; Codex limits the Vault server's tools (`enabled_tools`) and runs read-only but cannot take its other tools away; Cursor has only a read-only mode (its subagent format has no tool list). Sources read 2026-10-07: GitHub Docs and VS Code docs (custom agents, `tools` and `<server>/<tool>`), Codex docs (custom agent TOML, `mcp_servers`, `enabled_tools`), Cursor docs (subagents).

**Agent Plugins and agents.** The Agent Plugins 1.0 standard defines only skills and MCP servers; agents are "too client-specific" to be portable (its design notes). Client-specific files go in a top-level directory named for the client's reverse-domain namespace (spec section 8), and a client reads only its own. VS Code documents `com.github.copilot/` for GitHub Copilot's agents, so the portable plugin `plugins/the-vault/` carries the Copilot agents in `com.github.copilot/agents/` and other clients ignore that directory. No documentation was found for a Codex, Cursor or Claude Code namespace, so none is made up: the Claude Code agents stay in the plugin's `agents/` folder, Claude Code's own convention, and Codex and ChatGPT get the experts as skills in `plugins/the-vault-openai/` (a separate package, #225). If a client documents a namespace, add its directory in `scripts/build_plugin.py` (`COPILOT_NAMESPACE` is the pattern) and a case to `tests/test_plugin.py`. Listings and directories: `docs/listings.md`.
