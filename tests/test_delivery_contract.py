"""The delivery contract: what a player installs, in one gesture, and what the plugin must carry.

These tests exist because the delivery path broke repeatedly (a pasted token in the Claude plugin, a connector AND a
plugin to install, a marketplace that git ignored, two entries with the same name). A change that makes any of them
fail is a regression and is not merged. docs/plugin-delivery-and-testing.md is the plan these tests protect.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_plugin as bp  # noqa: E402

CLAUDE_PLUGIN_DIR = ROOT / "plugins" / "the-vault"
CODEX_PLUGIN_DIR = ROOT / "plugins" / "the-vault-openai"
SERVER_URL = "https://mtgvault.cards/api/mcp"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_claude_plugin_is_one_install_with_its_own_server_and_no_credential_step():
    plugin = load(CLAUDE_PLUGIN_DIR / ".claude-plugin" / "plugin.json")
    assert "userConfig" not in plugin, "a player must not be asked to paste a token"
    server = load(CLAUDE_PLUGIN_DIR / ".mcp.json")["mcpServers"]["the-vault"]
    assert server == {"type": "http", "url": SERVER_URL}, "the plugin carries the server; the host signs the person in"


def test_the_chatgpt_and_codex_plugin_is_one_install_with_its_own_server():
    plugin = load(CODEX_PLUGIN_DIR / "plugin.json")  # OpenAI's portable layout
    assert plugin["name"] == "the-vault" and plugin["$schema"].endswith("/plugin.schema.json")
    assert "interface" in plugin["extensions"]["com.openai"] and not (CODEX_PLUGIN_DIR / ".codex-plugin").exists()
    server = next(iter(load(CODEX_PLUGIN_DIR / "mcp.json")["mcpServers"].values()))
    assert server["url"] == SERVER_URL and "headers" not in server
    skills = sorted(p.name for p in (CODEX_PLUGIN_DIR / "skills").iterdir() if p.is_dir())
    assert "expert-council" in skills and "rules-judge" in skills, skills


def test_the_server_in_the_plugin_files_has_only_fields_the_chatgpt_desktop_loader_accepts():
    # The desktop app's loader failed with "unknown field `extensions`, expected `url` or `headers`" and registered no
    # server, so the plugin had no Vault tools at all (its log, 2026-10-11; vault-wxy.5). The server answers 401 with the
    # OAuth challenge and the app signs the person in; a field here that the loader does not know costs every tool.
    for path in (CODEX_PLUGIN_DIR / "mcp.json", CLAUDE_PLUGIN_DIR / "mcp.json"):
        server = next(iter(load(path)["mcpServers"].values()))
        assert set(server) <= {"type", "url", "headers"}, f"{path}: a field the desktop app's loader rejects: {set(server) - {'type', 'url', 'headers'}}"
        assert server["url"] == SERVER_URL and "authorization" not in json.dumps(server).lower(), path


def test_the_directory_zip_declares_the_servers_own_sign_in_and_carries_no_secret(tmp_path):
    # OpenAI "Configure MCP authentication" (the directory's upload): the sign-in is declared in the ZIP's mcp.json only.
    import zipfile

    with zipfile.ZipFile(bp.write_zip(tmp_path / "the-vault-openai.zip")) as z:
        server = next(iter(json.loads(z.read("mcp.json"))["mcpServers"].values()))
        auth = server["extensions"]["com.openai"]["auth"]
        assert auth["type"] == "oauth" and auth["client"]["mode"] in ("cimd", "dcr")
        assert "clientId" not in auth.get("client", {}) and "clientSecret" not in json.dumps(auth)
        assert json.loads(z.read("plugin.json"))["name"] == "the-vault" and ".mcp.json" in z.namelist()


def test_both_git_marketplaces_exist_and_point_at_real_plugins():
    claude = load(ROOT / ".claude-plugin" / "marketplace.json")
    for entry in claude["plugins"]:
        target = ROOT / entry["source"]
        assert (target / ".claude-plugin" / "plugin.json").is_file(), f"Claude marketplace entry {entry['name']} is broken"
    codex = load(ROOT / ".agents" / "plugins" / "marketplace.json")
    assert [p["name"] for p in codex["plugins"]] == ["the-vault"]
    for entry in codex["plugins"]:
        assert entry["source"]["source"] == "local" and entry["policy"]["installation"] == "AVAILABLE"
        target = ROOT / entry["source"]["path"]
        assert (target / "plugin.json").is_file() and (target / "mcp.json").is_file(), f"Codex marketplace entry {entry['name']} is broken"


def test_every_marketplace_offers_the_one_same_package():
    # vault-wxy.3.2: ChatGPT reads both marketplace files; two folders meant two "The Vault" entries, and the app
    # installed the 8-skill one without the sign-in declaration.
    claude = {e["source"] for e in load(ROOT / ".claude-plugin" / "marketplace.json")["plugins"]}
    codex = {e["source"]["path"] for e in load(ROOT / ".agents" / "plugins" / "marketplace.json")["plugins"]}
    assert claude == codex == {"./plugins/the-vault-openai"}, (claude, codex)
    package = CODEX_PLUGIN_DIR
    assert (package / "plugin.json").is_file() and (package / ".claude-plugin" / "plugin.json").is_file()
    assert (package / "mcp.json").is_file() and (package / ".mcp.json").is_file()
    assert (package / "skills" / "expert-council" / "SKILL.md").is_file() and any((package / "agents").glob("*.md"))
    assert load(package / "plugin.json")["name"] == load(package / ".claude-plugin" / "plugin.json")["name"] == "the-vault"


def test_the_codex_marketplace_is_not_ignored_by_git():
    # .agents/ was ignored once, so the marketplace never reached GitHub and the app could not install from it.
    result = subprocess.run(["git", "check-ignore", "-q", ".agents/plugins/marketplace.json"], cwd=ROOT)
    assert result.returncode == 1, ".agents/plugins/marketplace.json is git-ignored: the Git marketplace would be empty"


def test_name_and_version_agree_in_every_manifest():
    claude = load(CLAUDE_PLUGIN_DIR / ".claude-plugin" / "plugin.json")
    portable = load(CLAUDE_PLUGIN_DIR / "plugin.json")
    codex = load(CODEX_PLUGIN_DIR / "plugin.json")
    market = load(ROOT / ".claude-plugin" / "marketplace.json")["plugins"][0]
    names = {claude["name"], portable["name"], codex["name"], market["name"]}
    versions = {claude["version"], portable["version"], codex["version"], market["version"]}
    assert names == {"the-vault"}, names
    assert len(versions) == 1, f"the manifests disagree on the version: {versions}"


def test_the_skills_in_both_plugins_are_the_source_skills():
    for plugin_dir in (CLAUDE_PLUGIN_DIR, CODEX_PLUGIN_DIR):
        for source in sorted((ROOT / "skills").rglob("*")):
            if source.is_file():
                copy = plugin_dir / "skills" / source.relative_to(ROOT / "skills")
                assert copy.is_file(), f"{copy} is missing"
                assert copy.read_text(encoding="utf-8") == source.read_text(encoding="utf-8"), f"{copy} drifted from skills/"


def test_the_council_skill_keeps_its_answer_gate():
    text = (ROOT / "skills" / "expert-council" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Before you answer: the gate" in text
    for needed in ("get_rule", "get_card_oracle", "devil's advocate", "validate_deck_changes", "do not add this"):
        assert needed in text, f"the gate lost '{needed}'"
    # the gate comes before the members, so it is read first
    assert text.index("## Before you answer: the gate") < text.index("## Members")


def test_install_pages_never_tell_a_player_to_paste_a_token_for_the_plugin():
    connect = (ROOT / "public" / "connect.html").read_text(encoding="utf-8")
    plugin_lines = [line for line in connect.splitlines() if re.search(r"plugin (marketplace )?(add|install)", line)]
    assert plugin_lines, "the connect page no longer shows the plugin install"
    for line in plugin_lines:
        assert not re.search(r"token|vault_pat_|user_config", line, re.I), line
