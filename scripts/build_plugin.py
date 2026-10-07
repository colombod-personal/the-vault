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
import json
import shutil
import sys
from pathlib import Path

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
analyst, attribution), `mcp.json` (the Vault's MCP server), and Claude Code extras in `.claude-plugin/`
and `.mcp.json`.

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

FAN_NOTICE = ("The Vault is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. "
              "Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.")


def _block(code: str) -> str:
    code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<div class="code"><button class="copy" type="button">Copy</button><pre><code>{code}</code></pre></div>'


def connect_page() -> str:
    """public/connect.html, generated from the same constants as the plugin so it cannot go stale."""
    mcp_url = f"{HOST}/api/mcp"
    claude_mcp = f'claude mcp add --transport http vault {mcp_url} --header "Authorization: Bearer vault_pat_..."'
    editor_json = json.dumps({"mcpServers": {"vault": {"type": "http", "url": mcp_url,
                                                       "headers": {"Authorization": "Bearer vault_pat_..."}}}}, indent=2)
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

  <h2>2. Developers: Claude Code, Codex, Cursor, VS Code</h2>
  <p>Sign in to the Vault, open <strong>Account → Agents &amp; API</strong>, and create a personal access token.
    Read-only is enough for rules, cards, deck analysis and your collection. The token is shown once; keep it private.</p>

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
      <code>vault-buyer</code>); they can only use the Vault's tools. For Codex, Cursor and GitHub Copilot, copy the
      matching files from <a href="{REPO}/tree/main/agent-definitions">agent-definitions</a> into the folder named there.</p>
  </div>

  <div class="card">
    <h3>Claude Code: the tools only</h3>
    {_block(claude_mcp)}
  </div>

  <div class="card">
    <h3>Cursor, VS Code, Codex and other editors: the tools</h3>
    <p>Add this to the editor's MCP settings (the file is often <code>.cursor/mcp.json</code> or <code>.vscode/mcp.json</code>;
      some editors call the key <code>servers</code> instead of <code>mcpServers</code>):</p>
    {_block(editor_json)}
  </div>


  <h2>3. Check it works</h2>
  <p>Ask your assistant: <em>“Call the Vault's whoami tool.”</em> It should name you, your scopes and which data
    versions the Vault holds (the Comprehensive Rules edition, card data and price dates). Then try:</p>
  <ul>
    <li><em>“Does Lightning Bolt kill a creature with 3 toughness that has protection from red? Cite the rules.”</em></li>
    <li><em>“Here is my Commander deck … suggest upgrades under $50 and check them.”</em></li>
    <li><em>“What am I missing for this deck, and what will it cost?”</em></li>
  </ul>

  <h2>What to expect</h2>
  <ul>
    <li>Answers cite rule numbers and the rules edition, and say when the sources do not settle a question.</li>
    <li>Material from Scryfall and Wizards of the Coast is shown as theirs. Figures the Vault works out (legality, budget, counts) are labelled as computed, with their sources.</li>
    <li>Prices are Scryfall's, dated, not a store's price today. The Vault never contacts stores or fills carts.</li>
  </ul>

  <div class="legal">
    <p>{FAN_NOTICE}</p>
    <p><a class="btn sm" href="/">Back to the Vault</a> <a class="btn sm ghost" href="credits.html">Credits</a>
      <a class="btn sm ghost" href="privacy.html">Privacy notice</a></p>
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
    body = agent["body"] + _skills_note(agent)
    assert "'''" not in body
    return (f"name = {json.dumps(agent['name'])}\ndescription = {json.dumps(agent['description'])}\nsandbox_mode = \"read-only\"\n"
            f"developer_instructions = '''\n{body}\n'''\n")


def cursor_agent(agent: dict) -> str:
    return (f"---\nname: {agent['name']}\ndescription: {json.dumps(agent['description'])}\nmodel: inherit\nreadonly: true\n---\n\n"
            + agent["body"] + _skills_note(agent) + "\n")


def copilot_agent(agent: dict) -> str:
    return (f"---\nname: {agent['name']}\ndescription: {json.dumps(agent['description'])}\n---\n\n"
            + agent["body"] + _skills_note(agent) + "\n")


DEFS_README = """# Agent definitions for other assistants

Generated by `scripts/build_plugin.py` from `agents/` (edit those, never these). Thirteen agents: `vault-judge`
(rules and interactions), `vault-deckbuilder` (budget upgrades, validated by the Vault), `vault-buyer` (what
to buy, from your collection), `vault-collection-analyst`, and the expert council's voices (one per format:
Commander, Standard, Pioneer, Pauper, Limited, Two-Headed Giant; plus the casual table, the synergy analyst and the
devil's advocate). Each answers only through the Vault's tools and passes on provenance.

The Claude Code plugin already includes them (`plugins/the-vault/agents/`; they appear as `the-vault:vault-judge`
and so on). For the others, copy the files into the folder your assistant reads:

| Assistant | Copy `{folder}` to |
|---|---|
| Codex | `codex/*.toml` to `.codex/agents/` (project) or `~/.codex/agents/` (personal) |
| Cursor | `cursor/*.md` to `.cursor/agents/` (project) or `~/.cursor/agents/` (personal) |
| GitHub Copilot | `copilot/*.agent.md` to `.github/agents/` |

These formats follow the assistants' published documentation as read on 2026-10-04 but have not been run in the
assistants themselves: check that the agent appears and can call the Vault's tools (ask it to call `whoami`).
The MCP server must be connected under the name `the-vault` or `vault` (see `public/connect.html`). Install the skills
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
    }
    for source in sorted(SKILLS.rglob("*")):
        if source.is_file():
            files[PLUGIN / "skills" / source.relative_to(SKILLS)] = source
    for agent in load_agents():
        files[PLUGIN / "agents" / f"{agent['name']}.md"] = claude_agent(agent)
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
