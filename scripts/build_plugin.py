"""Build the Vault's installable plugin from ``skills/`` (the one source of the skills).

    python scripts/build_plugin.py            # write plugins/the-vault/ and .claude-plugin/marketplace.json
    python scripts/build_plugin.py --check    # exit 1 if they are out of date (tests/test_plugin.py does this)

What it makes:

- ``plugins/the-vault/`` follows the Agent Plugins 1.0 layout (``plugin.json``, ``skills/``,
  ``mcp.json``), which ChatGPT, Codex, Cursor, GitHub Copilot, VS Code and others read. The skills
  there are copies of ``skills/``; never edit them by hand.
- Claude Code extras next to them: ``.claude-plugin/plugin.json`` and ``.mcp.json`` (it asks for the
  person's token once, stores it securely, and sends it as the bearer header).
- ``.claude-plugin/marketplace.json`` at the repository root, so
  ``/plugin marketplace add colombod-personal/the-vault`` then ``/plugin install the-vault@the-vault`` works.

Skills are installed on their own with ``npx skills add colombod-personal/the-vault`` (docs/skills.md).
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vault import reviewer  # noqa: E402  (the test cases live with the demo account they run on)

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
AGENTS = ROOT / "agents"
DEFS = ROOT / "agent-definitions"
PLUGIN = ROOT / "plugins" / "the-vault"
# ChatGPT and Codex plugin (#225): .codex-plugin/plugin.json, the skills, the experts as skills (no subagents there),
# and the Vault's MCP server. Uploaded as a ZIP: python scripts/build_plugin.py --zip
OPENAI = ROOT / "plugins" / "the-vault-openai"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

NAME = "the-vault"
# Agent Plugins 1.0 has no agent concept ("commands, hooks, agents ... outside the v1 format", spec section on why only
# skills and MCP), so agents are client extensions: spec section 8 puts client files in a top-level directory named for
# the client's reverse-domain namespace. VS Code documents that it reads GitHub Copilot's agents from
# ``com.github.copilot/agents/`` in a plugin (docs/skills.md); no other client's namespace is documented, so none is made.
COPILOT_NAMESPACE = "com.github.copilot"
VERSION = "0.1.0"
HOST = "https://mtgvault.cards"
REPO = "https://github.com/colombod-personal/the-vault"
DESCRIPTION = ("Magic: The Gathering rules, cards, decks and collection tools for your AI assistant, grounded in "
               "Scryfall and the Comprehensive Rules, with sources shown. Free and unofficial.")
KEYWORDS = ["magic-the-gathering", "mtg", "rules", "decks", "scryfall", "collection"]
AUTHOR = {"name": "The Vault", "url": REPO}

PORTABLE_PLUGIN = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": NAME, "version": VERSION, "description": DESCRIPTION, "author": AUTHOR, "homepage": REPO,
    "repository": REPO, "license": "MIT", "keywords": KEYWORDS,
}
# No credentials in the portable file: the client discovers sign-in from the server (OAuth), or you add a
# personal access token header by hand (docs/skills.md).
PORTABLE_MCP = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
    "mcpServers": {NAME: {"type": "streamable-http", "url": f"{HOST}/api/mcp"}},
}
CLAUDE_PLUGIN = {
    "name": NAME, "displayName": "The Vault", "version": VERSION, "description": DESCRIPTION, "author": AUTHOR,
    "homepage": REPO, "repository": REPO, "license": "MIT", "keywords": KEYWORDS,
    "userConfig": {
        "vault_url": {"type": "string", "title": "Vault address", "default": HOST,
                      "description": "Where The Vault runs (the default is the public one)"},
        "vault_token": {"type": "string", "title": "Personal access token", "sensitive": True, "required": True,
                        "description": "Create one in The Vault: Account, Agents & API. Read-only is enough for rules, cards and deck analysis."},
    },
}
CLAUDE_MCP = {"mcpServers": {NAME: {"type": "http", "url": "${user_config.vault_url}/api/mcp",
                                    "headers": {"Authorization": "Bearer ${user_config.vault_token}"}}}}
MARKET = {
    "name": NAME, "owner": {"name": "The Vault", "url": REPO},
    "description": "Tools and skills for Magic: The Gathering with your AI assistant",
    "plugins": [{"name": NAME, "source": "./plugins/the-vault", "description": DESCRIPTION, "version": VERSION,
                 "keywords": KEYWORDS}],
}

README = f"""# The Vault plugin

Magic: The Gathering rules, cards, decks and collection tools for your AI assistant. Free, unofficial,
and every answer shows where it came from. This folder is **generated** by `scripts/build_plugin.py`
from `skills/` in the repository; do not edit it by hand.

Contents: `skills/` (rules judge, interaction explainer, deck upgrader, shopping assistant, collection
analyst, attribution), `mcp.json` (the Vault's MCP server), Claude Code extras in `.claude-plugin/` and
`.mcp.json`, the Claude Code agents in `agents/`, and the GitHub Copilot agents in `com.github.copilot/agents/`
(Agent Plugins has no agent component, so agents live under the client's reverse-domain namespace).

Install and connect: see docs/skills.md in the repository, or {HOST}/connect.html.

The Vault is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards.
Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.
"""


def dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# The OAuth server is live and was checked on the real hosts (claude.ai 2026-10-04, ChatGPT 2026-10-06, #77): the
# connect page offers "connect by URL" for ChatGPT and Claude.ai.
OAUTH_READY = True

# The Vault's listings in each app's directory (#227): empty until published (#225). Setting one turns the connect
# page's manual steps for that app into an "Add to ..." button, everywhere at once.
LISTINGS = {"claude": None, "chatgpt": None}

MCP_URL = f"{HOST}/api/mcp"
TOKEN_ENV = "VAULT_TOKEN"
# When the docs below were read for the blocks (docs/onboarding.md, "The connect page's blocks"). Nothing here has been
# run in the harness itself, except Claude Code and the plugin validators: the page says what the docs say.
HARNESSES_CHECKED = "2026-10-07"

TOKEN_HOW = (f"Without browser sign-in: create a personal access token (Account → Agents & API; read-only is enough), "
             f"keep it in an environment variable named {TOKEN_ENV}")


def _json(data: dict) -> str:
    return json.dumps(data, indent=2)


# Every harness's connection instructions, in its own format, read from its own documentation. connect.html and
# llms.txt are both generated from this list (connect_page(), llms_txt()), so they cannot disagree with each other or
# with HOST and OAUTH_READY (tests/test_plugin.py). A step is {kind, label, lang, code}: "oauth" steps show only when
# OAUTH_READY, a token never appears in a command line (it would land in shell history and the chat): it is read from
# an environment variable or typed into the harness's own prompt.
HARNESSES = [
    {"id": "claude-code", "title": "Claude Code",
     "docs": [("Claude Code: MCP", "https://code.claude.com/docs/en/mcp")],
     "steps": [
         {"kind": "oauth", "label": "Add the server for all your projects, then sign in with your Vault account:", "lang": "bash",
          "code": f"claude mcp add --transport http vault --scope user {MCP_URL}\nclaude mcp login vault"},
         {"kind": "token", "label": f"{TOKEN_HOW}, and put this in the project's .mcp.json (Claude Code fills in the variable):", "lang": "json",
          "code": _json({"mcpServers": {"vault": {"type": "http", "url": MCP_URL,
                                                 "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"}}}})},
     ],
     "note": "Inside a session, /mcp does the sign-in too. The plugin above asks for the token once and stores it securely."},
    {"id": "codex", "title": "Codex (CLI and IDE extension)",
     "docs": [("Codex: MCP", "https://learn.chatgpt.com/docs/extend/mcp?surface=cli")],
     "steps": [
         {"kind": "oauth", "label": "Add the server, then sign in with your Vault account:", "lang": "bash",
          "code": f"codex mcp add vault --url {MCP_URL}\ncodex mcp login vault"},
         {"kind": "config", "label": "Or by hand in ~/.codex/config.toml (Codex uses TOML here, not JSON):", "lang": "toml",
          "code": f'[mcp_servers.vault]\nurl = "{MCP_URL}"'},
         {"kind": "token", "label": f"{TOKEN_HOW}, and name it in the same table:", "lang": "toml",
          "code": f'[mcp_servers.vault]\nurl = "{MCP_URL}"\nbearer_token_env_var = "{TOKEN_ENV}"'},
     ],
     "note": "The CLI and the IDE extension share this configuration."},
    {"id": "cursor", "title": "Cursor",
     "docs": [("Cursor: MCP", "https://cursor.com/docs/context/mcp")],
     "steps": [
         {"kind": "oauth", "label": "In .cursor/mcp.json (this project) or ~/.cursor/mcp.json (all projects); Cursor documents OAuth for servers that need it; if it shows no sign-in, use the token block below:", "lang": "json",
          "code": _json({"mcpServers": {"vault": {"url": MCP_URL}}})},
         {"kind": "token", "label": f"{TOKEN_HOW}; Cursor fills in the variable:", "lang": "json",
          "code": _json({"mcpServers": {"vault": {"url": MCP_URL, "headers": {"Authorization": f"Bearer ${{env:{TOKEN_ENV}}}"}}}})},
     ],
     "note": "The root key is mcpServers."},
    {"id": "vscode", "title": "VS Code (GitHub Copilot)",
     "docs": [("VS Code: MCP servers", "https://code.visualstudio.com/docs/copilot/customization/mcp-servers"),
              ("VS Code: MCP configuration reference", "https://code.visualstudio.com/docs/agents/reference/mcp-configuration")],
     "steps": [
         {"kind": "oauth", "label": "In .vscode/mcp.json (or run “MCP: Open User Configuration” for all workspaces); VS Code documents OAuth for servers that need it; if it shows no sign-in, use the token block below:", "lang": "json",
          "code": _json({"servers": {"vault": {"type": "http", "url": MCP_URL}}})},
         {"kind": "token", "label": "Without browser sign-in: create a personal access token (Account → Agents & API); VS Code asks for it once, hides it and stores it:", "lang": "json",
          "code": _json({"inputs": [{"type": "promptString", "id": "vault-token", "description": "The Vault personal access token", "password": True}],
                         "servers": {"vault": {"type": "http", "url": MCP_URL,
                                               "headers": {"Authorization": "Bearer ${input:vault-token}"}}}})},
     ],
     "note": "The root key is servers here, not mcpServers."},
    {"id": "copilot-cli", "title": "GitHub Copilot CLI",
     "docs": [("GitHub Docs: add MCP servers to Copilot CLI",
               "https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers")],
     "steps": [
         {"kind": "config", "label": "Add the server:", "lang": "bash", "code": f"copilot mcp add --transport http vault {MCP_URL}"},
         {"kind": "config", "label": "Or by hand in ~/.copilot/mcp-config.json:", "lang": "json",
          "code": _json({"mcpServers": {"vault": {"type": "http", "url": MCP_URL, "tools": ["*"]}}})},
         {"kind": "token", "label": ("Without browser sign-in: create a personal access token (Account → Agents & API). In a Copilot CLI session run "
                                     "/mcp add, choose HTTP, enter the address above and type this into the HTTP Headers prompt (not into a command):"),
          "lang": "json", "code": _json({"Authorization": "Bearer vault_pat_..."})},
     ],
     "note": "The Copilot CLI documentation we read describes headers for remote servers and does not describe a browser sign-in, so this block uses the token."},
]


def harness_steps(harness: dict) -> list[dict]:
    """The steps to show: the OAuth ones only when sign-in is switched on (OAUTH_READY)."""
    return [s for s in harness["steps"] if OAUTH_READY or s["kind"] != "oauth"]


def llms_connection() -> str:
    """The connection part of public/llms.txt, from the same HARNESSES, HOST and OAUTH_READY as connect.html."""
    host = HOST
    lines = [
        f"- MCP server: `{MCP_URL}` (Streamable HTTP, stateless JSON).",
    ]
    if OAUTH_READY:
        lines += [
            f"- OAuth (ChatGPT, Claude.ai, any MCP client): add the connector URL `{MCP_URL}`. The",
            "  401 from `/api/mcp` points at `/.well-known/oauth-protected-resource/api/mcp`; the server",
            "  metadata is at `/.well-known/oauth-authorization-server`. PKCE S256, the `resource` parameter",
            f"  (`{MCP_URL}`), a Client ID Metadata Document URL or `POST /oauth/register` as",
            "  `client_id`, scopes `read` (default) and `write` (the person must tick it). Access tokens last",
            "  an hour; refresh tokens rotate, and a reused one revokes the connection. OAuth tokens work on",
            "  `/api/mcp` only, and can never manage the account.",
        ]
    else:
        lines.append("- OAuth sign-in is not switched on yet: use a personal access token.")
    lines += [
        "- Token: header `Authorization: Bearer vault_pat_...` (a personal access token from Account → Agents & API). Keep it in an",
        f"  environment variable or the client's own secret prompt, never in a command line (shell history, the chat).",
        f"- Each client has its own format, taken from its documentation (read {HARNESSES_CHECKED}); the host is {host}:",
    ]
    for h in HARNESSES:
        docs = "; ".join(f"{t}: {u}" for t, u in h["docs"])
        lines.append(f"  - {h['title']} ({docs})")
        for s in harness_steps(h):
            lines.append(f"    {s['label']}")
            lines += ["", f"    ```{s['lang']}"] + [f"    {c}" if c else "" for c in s["code"].split("\n")] + ["    ```", ""]
        lines.append(f"    {h['note']}")
    return "\n".join(lines).rstrip() + "\n"


LLMS_BEGIN = "<!-- connect:begin generated by scripts/build_plugin.py from HARNESSES, HOST and OAUTH_READY: edit them there -->"
LLMS_END = "<!-- connect:end -->"


def llms_txt() -> str:
    """public/llms.txt with its generated connection section filled in (the rest is hand-written)."""
    path = ROOT / "public" / "llms.txt"
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    head, rest = text.split(LLMS_BEGIN + "\n", 1)
    _, tail = rest.split(LLMS_END + "\n", 1)
    return head + LLMS_BEGIN + "\n" + llms_connection() + LLMS_END + "\n" + tail

FAN_NOTICE = ("The Vault is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. "
              "Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.")


def _block(code: str) -> str:
    code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<div class="code"><button class="copy" type="button">Copy</button><pre><code>{code}</code></pre></div>'


def connect_page() -> str:
    """public/connect.html, generated from the same constants as the plugin so it cannot go stale."""
    mcp_url = MCP_URL
    def app_card(app: str, title: str, steps: str, after_update: str) -> str:
        listing = LISTINGS.get(app)
        if listing:
            how = (f'<p><a class="btn primary" href="{listing}" target="_blank" rel="noopener">Add The Vault to {title}</a></p>'
                   f'<p class="note">It opens The Vault in {title}\'s directory. Approve on the Vault\'s page when it asks '
                   'you to sign in: tick <strong>Write</strong> if the assistant may edit your collection and decks.</p>')
        elif OAUTH_READY:
            how = (f"<p>{steps}</p>" + _block(mcp_url) +
                   '<p class="note">Then approve on the Vault\'s page: tick <strong>Write</strong> if the assistant may edit '
                   f"your collection and decks. A listing in {title}'s directory is coming; this page will show the button.</p>")
        else:
            how = "<p>Connecting by sign-in is not switched on yet. Use the developer options below, or check back here.</p>"
        return (f'<div class="card" id="{app}"><h3>{title}</h3>{how}'
                f'<p class="note">After a Vault update that adds tools: {after_update}</p></div>')

    claude_card = app_card("claude", "Claude",
                           "In Claude (web, desktop or mobile): <strong>Settings → Connectors → Add custom connector</strong>, "
                           "name it The Vault and paste this address:",
                           "disconnect and connect The Vault again, so Claude reads the new tools.")
    chatgpt_card = app_card("chatgpt", "ChatGPT",
                            "In ChatGPT: <strong>Plugins → Add → Add custom MCP server</strong>, name it The Vault, paste this "
                            "address and keep OAuth:",
                            "ChatGPT reads the tools only when the app is added: delete it (not only uninstall) and add it again.")
    def harness_card(h: dict) -> str:
        steps = "".join(f"<p>{html.escape(s['label'])}</p>" + _block(s["code"]) for s in harness_steps(h))
        docs = ", ".join(f'<a href="{u}" target="_blank" rel="noopener">{html.escape(t)}</a>' for t, u in h["docs"])
        return (f'<div class="card" id="{h["id"]}"><h3>{html.escape(h["title"])}</h3>{steps}'
                f'<p class="note">{html.escape(h["note"])} Format from the tool\'s own documentation ({docs}), read {HARNESSES_CHECKED}.</p></div>')

    harness_cards = "\n  ".join(harness_card(h) for h in HARNESSES)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Connect your AI assistant · The Vault</title>
  <meta name="description" content="Connect Claude, ChatGPT, Codex, Cursor or VS Code to The Vault: Magic rules, cards, decks and your collection, with sources shown." />
  <link rel="icon" href="/favicon.ico" sizes="32x32" />
  <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
  <link rel="stylesheet" href="styles.css" />
  <style>
    body {{ padding: 32px 16px; }}
    main {{ max-width: 780px; margin: 0 auto; line-height: 1.6; }}
    h2 {{ font-family: var(--display); margin-top: 36px; }}
    .card {{ border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; margin: 12px 0; background: var(--surface); }}
    .card h3 {{ margin: 0 0 4px; font-family: var(--display); font-size: 20px; }}
    .code {{ position: relative; margin: 8px 0; }}
    .code pre {{ margin: 0; padding: 12px 70px 12px 12px; overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg, #111); font-family: var(--mono); font-size: 13px; }}
    .copy {{ position: absolute; top: 8px; right: 8px; font-size: 12px; cursor: pointer; }}
    .note {{ color: var(--text-2); font-size: 14px; }}
    .legal {{ font-size: 12px; color: var(--muted); border-top: 1px solid var(--border); margin-top: 40px; padding-top: 16px; }}
  </style>
</head>
<body>
<main>
  <p class="eyebrow">The Vault</p>
  <h1 class="h1">Connect your AI assistant</h1>
  <p>Ask your own assistant about Magic rules, cards and decks, grounded in Scryfall and the official
    Comprehensive Rules, with every answer showing where it came from. The Vault is free; your assistant
    uses your own subscription or key. Nothing here is generated by the Vault itself.</p>

  <h2>1. In Claude or ChatGPT</h2>
  <p>No token needed: you sign in with your Vault account and choose what the assistant may do. You can disconnect it
    any time under <strong>Account → Connected apps</strong>.</p>
  {claude_card}
  {chatgpt_card}

  <h2>2. Developers: Claude Code, Codex, Cursor, VS Code, GitHub Copilot CLI</h2>
  <p>Each tool below has its own format, so each has its own block. Where the tool offers it you sign in with your Vault
    account in the browser and no token is involved. Otherwise open <strong>Account → Agents &amp; API</strong> and create a
    personal access token (read-only is enough for rules, cards, deck analysis and your collection; it is shown once).
    The blocks never put the token in a command: it is read from an environment variable or typed into the tool's own prompt.</p>

  <div class="card">
    <h3>Claude Code (plugin: skills and tools together)</h3>
    {_block("/plugin marketplace add colombod-personal/the-vault" + chr(10) + "/plugin install the-vault@the-vault")}
    <p class="note">It asks for the Vault address (the default is this site) and your token once, and stores the token securely.</p>
  </div>

  <div class="card">
    <h3>Any agent that reads skills (Claude Code, Codex, Cursor, Copilot and more): the skills</h3>
    {_block("npx skills add colombod-personal/the-vault")}
    <p class="note">Add <code>--skill rules-judge</code> for one skill, or <code>-g</code> to install for all your projects.
      Skills tell your assistant how to answer; pair them with the tools below.</p>
  </div>

  <div class="card">
    <h3>Ready-made agents: a rules judge, a budget deck builder, a buyer</h3>
    <p>The Claude Code plugin above includes them (<code>the-vault:vault-judge</code>, <code>vault-deckbuilder</code>,
      <code>vault-buyer</code>); in Claude Code they can only use the Vault's tools. For Codex, Cursor and GitHub Copilot,
      copy the matching files from <a href="{REPO}/tree/main/agent-definitions">agent-definitions</a> into the folder
      named there; what each tool actually restricts is in the README next to them (Copilot: only the Vault's tools;
      Codex: the Vault's read-only tools, read-only sandbox; Cursor: read-only mode and instructions only).</p>
  </div>

  <h3>The tools (MCP server) in each tool</h3>
  {harness_cards}


  <h2>3. Check it works</h2>
  <p>Ask your assistant: <em>“Call the Vault's whoami tool.”</em> It should name you, your scopes and which data
    versions the Vault holds (the Comprehensive Rules edition, card data and price dates). Then try these five:</p>
  <ol id="examples">
    <li><em>“Does Lightning Bolt kill a creature with 3 toughness that has protection from red? Cite the rules.”</em>
      The assistant looks up the card and the rules, quotes them, and names the rules edition.</li>
    <li><em>“Review my sliver deck with the expert council.”</em>
      It finds your saved deck, shows its name, format and commander first, then answers as the format expert, the casual
      table, the judge and a devil's advocate, each citing what the tools returned.</li>
    <li><em>“What am I missing for my sliver deck, and what will it cost?”</em>
      Your collection against the deck: owned, partly owned and missing cards, with dated Scryfall prices.</li>
    <li><em>“Show me the printings of Sol Ring that I own.”</em>
      The printings in your collection, with pictures.</li>
    <li><em>“Add a Sol Ring to my collection.”</em>
      It shows exactly what would change and waits for your yes. You can undo it.</li>
  </ol>
  <p>The Vault will not do these, and a good assistant says so: <em>“Which shop is cheapest right now?”</em> (it has no
    shop prices, only dated Scryfall ones), <em>“Buy me this deck”</em> (it never contacts stores), <em>“Delete my
    account”</em> (account actions stay with you in the Vault).</p>

  <h2>What to expect</h2>
  <ul>
    <li>Answers cite rule numbers and the rules edition, and say when the sources do not settle a question.</li>
    <li>Material from Scryfall and Wizards of the Coast is shown as theirs. Figures the Vault works out (legality, budget, counts) are labelled as computed, with their sources.</li>
    <li>Prices are Scryfall's, dated, not a store's price today. The Vault never contacts stores or fills carts.</li>
  </ul>

  <div class="legal">
    <p>{FAN_NOTICE}</p>
    <p><a class="btn sm" href="/">Back to the Vault</a> <a class="btn sm ghost" href="credits.html">Credits</a>
      <a class="btn sm ghost" href="privacy.html">Privacy notice</a> <a class="btn sm ghost" href="terms.html">Terms</a>
      <a class="btn sm ghost" href="support.html">Support</a></p>
  </div>
</main>
<script>
  // Show the address this page is served from (a preview or a local copy), but never a *.vercel.app address:
  // the production address is the one to give out.
  (function () {{
    var origin = location.origin, shown = {json.dumps(HOST)};
    if (!/^https?:/.test(origin) || /\\.vercel\\.app$/.test(location.hostname)) return;
    document.querySelectorAll('code').forEach(function (c) {{ c.textContent = c.textContent.split(shown).join(origin); }});
  }})();
  document.querySelectorAll('.copy').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var text = b.parentNode.querySelector('code').textContent;
      if (navigator.clipboard) {{ navigator.clipboard.writeText(text).then(function () {{ b.textContent = 'Copied'; setTimeout(function () {{ b.textContent = 'Copy'; }}, 1500); }}); }}
    }});
  }});
</script>
</body>
</html>
"""


def load_agents() -> list[dict]:
    """The agent definitions in ``agents/*.md``: neutral source, one file each (frontmatter, then instructions)."""
    import yaml

    out = []
    for path in sorted(AGENTS.glob("*.md")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        front, body = text[4:].split("\n---\n", 1)
        meta = yaml.safe_load(front)
        out.append({"name": meta["name"], "description": " ".join(str(meta["description"]).split()), "tools": meta["vault-tools"],
                    "skills": meta.get("skills", []), "body": body.strip()})
    return out


def _skills_note(agent: dict) -> str:
    return ("\n\nIf these skills are installed, follow them: " + ", ".join(agent["skills"]) + ". You are read-only: where a skill "
            "says to change the person's collection, decks or shares, say what the change would be and leave it to the main assistant, "
            "which asks the person first.") if agent["skills"] else ""


def claude_agent(agent: dict) -> str:
    """Claude Code plugin agent: only the Vault's own tools (named as plugin MCP tools), no files, shell or web."""
    tools = ", ".join(f"mcp__plugin_{NAME}_{NAME}__{t}" for t in agent["tools"])
    return (f"---\nname: {agent['name']}\ndescription: {json.dumps(agent['description'])}\ntools: {tools}\nmodel: inherit\n---\n\n"
            + agent["body"] + _skills_note(agent) + "\n")


def codex_agent(agent: dict) -> str:
    """Codex custom agent: read-only sandbox, and the Vault server limited to this agent's tools (``enabled_tools`` is
    Codex's allow list for one server). Codex has no documented way to take away its other tools (shell reads, other MCP
    servers) from one agent, so those are limited by the instructions only: see DEFS_README."""
    body = agent["body"] + _skills_note(agent)
    assert "'''" not in body
    tools = ", ".join(json.dumps(t) for t in agent["tools"])
    return (f"name = {json.dumps(agent['name'])}\ndescription = {json.dumps(agent['description'])}\nsandbox_mode = \"read-only\"\n"
            f"developer_instructions = '''\n{body}\n'''\n\n[mcp_servers.vault]\nurl = {json.dumps(MCP_URL)}\n"
            f"enabled_tools = [{tools}]\n")


def cursor_agent(agent: dict) -> str:
    return (f"---\nname: {agent['name']}\ndescription: {json.dumps(agent['description'])}\nmodel: inherit\nreadonly: true\n---\n\n"
            + agent["body"] + _skills_note(agent) + "\n")


def copilot_agent(agent: dict, server: str = "vault") -> str:
    """GitHub Copilot / VS Code custom agent. ``tools`` is the agent's allow list (documented: only the listed tools are
    available; a name that is not available is ignored), so listing only ``<server>/<tool>`` for the Vault's tools leaves
    it no files, shell or web. ``server`` is the name the Vault's MCP server is connected under."""
    tools = "".join(f"  - {server}/{t}\n" for t in agent["tools"])
    return (f"---\nname: {agent['name']}\ndescription: {json.dumps(agent['description'])}\ntools:\n{tools}---\n\n"
            + agent["body"] + _skills_note(agent) + "\n")


DEFS_README = """# Agent definitions for other assistants

Generated by `scripts/build_plugin.py` from `agents/` (edit those, never these). Thirteen agents: `vault-judge`
(rules and interactions), `vault-deckbuilder` (budget upgrades, validated by the Vault), `vault-buyer` (what
to buy, from your collection), `vault-collection-analyst`, and the expert council's voices (one per format:
Commander, Standard, Pioneer, Pauper, Limited, Two-Headed Giant; plus the casual table, the synergy analyst and the
devil's advocate). Each is told to answer only through the Vault's tools and to pass on provenance.

## What each assistant actually enforces

The instructions tell every agent to use only the Vault's tools. Whether the assistant *stops* it from using anything
else differs, and these files claim only what each format documents:

| Assistant | Enforced by the assistant | Not enforced (the instructions only ask) |
|---|---|---|
| Claude Code (the plugin) | An allow list (`tools:`): only the Vault's read-only tools; no files, shell or web | nothing else |
| GitHub Copilot and VS Code | An allow list (`tools:` with `vault/<tool>`): only the Vault's read-only tools; no built-in tools | nothing else, if the server is connected under the name `vault` (a different name leaves the agent with no tools, never more) |
| Codex | The Vault server's tools limited to the agent's read-only ones (`enabled_tools`), and a read-only sandbox (no writes) | Codex's other tools: reading files with the shell, other MCP servers, web search |
| Cursor | A read-only mode (`readonly: true`: no writes) | Which tools it uses: Cursor's subagent format has no tool allow list and subagents inherit the parent's tools, including every MCP server |

The Vault's write tools are not in any list. A person's own assistant, not an agent, makes changes, after asking.

## Install

The Claude Code plugin already includes them (`plugins/the-vault/agents/`; they appear as `the-vault:vault-judge`
and so on). The same plugin carries the Copilot versions in `plugins/the-vault/com.github.copilot/agents/`, the
namespace VS Code documents for GitHub Copilot components in an Agent Plugins package. For the others, copy the files
into the folder your assistant reads:

| Assistant | Copy |
|---|---|
| Codex | `codex/*.toml` to `.codex/agents/` (project) or `~/.codex/agents/` (personal) |
| Cursor | `cursor/*.md` to `.cursor/agents/` (project) or `~/.cursor/agents/` (personal) |
| GitHub Copilot | `copilot/*.agent.md` to `.github/agents/` |

These formats follow the assistants' published documentation as read on 2026-10-07 but have not been run in the
assistants themselves: check that the agent appears and can call the Vault's tools (ask it to call `whoami`), and, for
Copilot, that it has no other tools. The Vault's MCP server must be connected under the name `vault` (the name in
`public/connect.html`) for Copilot's allow list and Codex's `[mcp_servers.vault]` table to match it. Install the skills
the agents name with `npx skills add colombod-personal/the-vault`.
"""


def experts_module() -> str:
    """vault/experts_data.py: every expert's brief and the council chair's procedure, for the connector tools
    (council_brief, expert_brief, #220), so Claude and ChatGPT get the same experts the plugin installs."""
    import yaml

    experts = {a["name"]: {"description": a["description"], "brief": a["body"]} for a in load_agents()}
    text = (ROOT / "skills" / "expert-council" / "SKILL.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    front, body = text[4:].split("\n---\n", 1)
    chair = {"description": " ".join(str(yaml.safe_load(front)["description"]).split()), "brief": body.strip()}
    data = json.dumps({"experts": experts, "chair": chair}, indent=1, ensure_ascii=False)
    return ('"""Generated by scripts/build_plugin.py from agents/*.md and skills/expert-council/SKILL.md: edit those, '
            'never this file."""\n\n# fmt: off\nDATA = ' + data + "\n")


OPENAI_MANIFEST = {
    "name": NAME,
    "version": "0.1.0",
    "description": ("Magic: The Gathering rules, cards, decks and your collection for ChatGPT and Codex, grounded in "
                    "Scryfall and the Comprehensive Rules, with sources shown, and an expert council for deck reviews. "
                    "Free and unofficial."),
    "author": {"name": "The Vault", "url": "https://github.com/colombod-personal/the-vault"},
    "homepage": HOST,
    "repository": "https://github.com/colombod-personal/the-vault",
    "license": "MIT",
    "keywords": ["magic-the-gathering", "mtg", "commander", "decks", "rules", "collection", "scryfall"],
    "skills": "./skills/",
    "mcpServers": "./mcp.json",
    # What the OpenAI directory review runs (five positive and three negative cases) on the demo account (vault/reviewer.py).
    "review": {"test_cases": {
        "positive": [{"description": c["name"], "prompt": c["prompt"], "tools_triggered": ", ".join(c["tools"]),
                      "expected_behavior": c["expect"]} for c in reviewer.POSITIVE],
        "negative": [{"description": c["name"] + ": " + c["expect"], "prompt": c["prompt"]} for c in reviewer.NEGATIVE]}},
    "interface": {
        "displayName": "The Vault",
        "shortDescription": "Your Magic collection, decks and the rules, with sources",
        "longDescription": ("Ask about the cards you own and what they are worth, check a deck against your collection, "
                            "find upgrades on a budget, get rules answers with cited rule numbers, and have an expert "
                            "council (Commander expert, casual table, judge, devil's advocate) review a deck. Card data "
                            "and prices are Scryfall's, rules are Wizards of the Coast's, shown with their sources. "
                            "Free, unofficial Fan Content."),
        "developerName": "The Vault",
        "category": "Lifestyle",
        "capabilities": ["Read", "Write"],
        "websiteURL": HOST,
        "privacyPolicyURL": f"{HOST}/privacy.html",
        "termsOfServiceURL": f"{HOST}/terms.html",
        "defaultPrompt": ["What are my most valuable cards?",
                          "Review my Commander deck with the expert council",
                          "What am I missing for this deck, and what will it cost?"],
        "brandColor": "#C9A227",
        "logo": "./assets/logo.png",
        "composerIcon": "./assets/logo.png",
    },
}
OPENAI_MCP = {"mcpServers": {NAME: {"type": "streamable-http", "url": f"{HOST}/api/mcp"}}}
# Experts that work on a deck or a question: each becomes a skill where hosts have no subagents (the council skill
# calls them; a person can also ask for one directly). The buyer and deckbuilder stay agents for plugin hosts.
EXPERT_SKILLS = ("vault-commander-expert", "vault-casual-table", "vault-limited-expert", "vault-pauper-expert",
                 "vault-standard-expert", "vault-pioneer-expert", "vault-two-headed-giant-expert", "vault-judge",
                 "vault-devils-advocate", "vault-synergy-analyst", "vault-collection-analyst")


def expert_skill(agent: dict) -> str:
    """An expert agent as a skill (SKILL.md): the same brief, for ChatGPT and Codex, which load skills, not subagents."""
    import yaml

    front = {"name": agent["name"],
             "description": agent["description"] + " Use when the expert council seats this member, or when the person "
                            "asks for this expert's view.",
             "license": "MIT", "metadata": {"vault-tools": " ".join(agent["tools"])}}
    return ("---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000) + "---\n\n"
            + agent["body"] + "\n\nAnswer as this one member: at most three points, each tied to a tool result. The "
            "chair (the expert-council skill) gathers the shared facts and runs the challenge.\n")


def openai_files() -> dict[Path, str | Path]:
    files: dict[Path, str | Path] = {
        OPENAI / ".codex-plugin" / "plugin.json": dump(OPENAI_MANIFEST),
        OPENAI / "mcp.json": dump(OPENAI_MCP),
        OPENAI / "assets" / "logo.png": ROOT / "public" / "apple-touch-icon.png",
    }
    for source in sorted(SKILLS.rglob("*")):
        if source.is_file():
            files[OPENAI / "skills" / source.relative_to(SKILLS)] = source
    for agent in load_agents():
        if agent["name"] in EXPERT_SKILLS:
            files[OPENAI / "skills" / agent["name"] / "SKILL.md"] = expert_skill(agent)
    return files


def write_zip(target: Path) -> Path:
    """The ChatGPT/Codex plugin as a ZIP (plugin root at the top), for upload or directory submission."""
    import zipfile

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path, value in sorted(openai_files().items()):
            z.writestr(str(path.relative_to(OPENAI)).replace("\\", "/"), _read(value))
    return target


def expected() -> dict[Path, str | Path]:
    """Every file the plugin should contain: text content, or the skill file it copies."""
    files: dict[Path, str | Path] = {
        PLUGIN / "plugin.json": dump(PORTABLE_PLUGIN),
        PLUGIN / "mcp.json": dump(PORTABLE_MCP),
        PLUGIN / ".claude-plugin" / "plugin.json": dump(CLAUDE_PLUGIN),
        PLUGIN / ".mcp.json": dump(CLAUDE_MCP),
        PLUGIN / "README.md": README,
        MARKETPLACE: dump(MARKET),
        ROOT / "public" / "connect.html": connect_page(),
        ROOT / "public" / "llms.txt": llms_txt(),
    }
    for source in sorted(SKILLS.rglob("*")):
        if source.is_file():
            files[PLUGIN / "skills" / source.relative_to(SKILLS)] = source
    for agent in load_agents():
        files[PLUGIN / "agents" / f"{agent['name']}.md"] = claude_agent(agent)
        files[PLUGIN / COPILOT_NAMESPACE / "agents" / f"{agent['name']}.agent.md"] = copilot_agent(agent, server=NAME)
        files[DEFS / "codex" / f"{agent['name']}.toml"] = codex_agent(agent)
        files[DEFS / "cursor" / f"{agent['name']}.md"] = cursor_agent(agent)
        files[DEFS / "copilot" / f"{agent['name']}.agent.md"] = copilot_agent(agent)
    files[DEFS / "README.md"] = DEFS_README
    files[ROOT / "vault" / "experts_data.py"] = experts_module()
    files.update(openai_files())
    return files


BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}


def _read(value: str | Path) -> bytes:
    data = value.read_bytes() if isinstance(value, Path) else value.encode("utf-8")
    if isinstance(value, Path) and value.suffix.lower() in BINARY:
        return data  # an image is copied byte for byte
    return data.replace(b"\r\n", b"\n")  # checkouts differ in line endings; the content is what matters


def stale() -> list[str]:
    want = expected()
    problems = []
    for path, value in want.items():
        on_disk = path.read_bytes() if path.exists() else None
        if on_disk is not None and path.suffix.lower() not in BINARY:
            on_disk = on_disk.replace(b"\r\n", b"\n")
        if on_disk != _read(value):
            problems.append(f"out of date: {path.relative_to(ROOT)}")
    for folder in (PLUGIN, DEFS, OPENAI):
        if folder.exists():
            for path in folder.rglob("*"):
                if path.is_file() and path not in want:
                    problems.append(f"unexpected: {path.relative_to(ROOT)}")
    return problems


def build() -> None:
    for folder in (PLUGIN, DEFS, OPENAI):
        if folder.exists():
            shutil.rmtree(folder)
    for path, value in expected().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_read(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the generated files are out of date")
    parser.add_argument("--zip", action="store_true", help="also write dist/the-vault-openai.zip (ChatGPT/Codex upload)")
    args = parser.parse_args(argv)
    if args.check:
        problems = stale()
        for p in problems:
            print(p)
        if problems:
            print("run: python scripts/build_plugin.py")
        return 1 if problems else 0
    build()
    print(f"wrote {PLUGIN.relative_to(ROOT)}, {OPENAI.relative_to(ROOT)}, {MARKETPLACE.relative_to(ROOT)} and public/connect.html")
    if args.zip:
        print(f"wrote {write_zip(ROOT / 'dist' / 'the-vault-openai.zip').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
