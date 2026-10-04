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
PLUGIN = ROOT / "plugins" / "the-vault"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

NAME = "the-vault"
VERSION = "0.1.0"
HOST = "https://the-vault-puce-one.vercel.app"
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


# Flip to True when the OAuth server (milestone M4) is live and checked against the real hosts: the
# connect page then offers "connect by URL" for ChatGPT and Claude.ai instead of saying it is coming.
OAUTH_READY = False

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
    web_status = ("Open the connectors settings, add a custom connector with the address below, and approve the permissions "
                  "when the Vault asks you to sign in." if OAUTH_READY else
                  "These connect by signing in with OAuth, which is not switched on yet (it is being built and checked). "
                  "Until then, use the options above, or check back here.")
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

  <h2>1. Create a token</h2>
  <p>Sign in to the Vault, open <strong>Account → Agents &amp; API</strong>, and create a personal access token.
    Read-only is enough for rules, cards, deck analysis and your collection. The token is shown once; keep it private.</p>

  <h2>2. Pick your assistant</h2>

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
    <h3>Claude Code: the tools only</h3>
    {_block(claude_mcp)}
  </div>

  <div class="card">
    <h3>Cursor, VS Code, Codex and other editors: the tools</h3>
    <p>Add this to the editor's MCP settings (the file is often <code>.cursor/mcp.json</code> or <code>.vscode/mcp.json</code>;
      some editors call the key <code>servers</code> instead of <code>mcpServers</code>):</p>
    {_block(editor_json)}
  </div>

  <div class="card">
    <h3>Claude.ai, Claude Desktop and ChatGPT (connect by address)</h3>
    <p>{web_status}</p>
    {_block(mcp_url)}
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
    return files


def _read(value: str | Path) -> bytes:
    data = value.read_bytes() if isinstance(value, Path) else value.encode("utf-8")
    return data.replace(b"\r\n", b"\n")  # checkouts differ in line endings; the content is what matters


def stale() -> list[str]:
    want = expected()
    problems = []
    for path, value in want.items():
        if not path.exists() or path.read_bytes().replace(b"\r\n", b"\n") != _read(value):
            problems.append(f"out of date: {path.relative_to(ROOT)}")
    if PLUGIN.exists():
        for path in PLUGIN.rglob("*"):
            if path.is_file() and path not in want:
                problems.append(f"unexpected: {path.relative_to(ROOT)}")
    return problems


def build() -> None:
    if PLUGIN.exists():
        shutil.rmtree(PLUGIN)
    for path, value in expected().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_read(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the generated files are out of date")
    args = parser.parse_args(argv)
    if args.check:
        problems = stale()
        for p in problems:
            print(p)
        if problems:
            print("run: python scripts/build_plugin.py")
        return 1 if problems else 0
    build()
    print(f"wrote {PLUGIN.relative_to(ROOT)}, {MARKETPLACE.relative_to(ROOT)} and public/connect.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
