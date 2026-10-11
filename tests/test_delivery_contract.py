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
    plugin = load(CODEX_PLUGIN_DIR / ".codex-plugin" / "plugin.json")
    assert plugin["name"] == "the-vault" and plugin["skills"] == "./skills/" and plugin["mcpServers"] == "./mcp.json"
    server = next(iter(load(CODEX_PLUGIN_DIR / "mcp.json")["mcpServers"].values()))
    assert server["url"] == SERVER_URL and "headers" not in server
    skills = sorted(p.name for p in (CODEX_PLUGIN_DIR / "skills").iterdir() if p.is_dir())
    assert "expert-council" in skills and "rules-judge" in skills, skills


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
        assert (target / ".codex-plugin" / "plugin.json").is_file(), f"Codex marketplace entry {entry['name']} is broken"


def test_the_codex_marketplace_is_not_ignored_by_git():
    # .agents/ was ignored once, so the marketplace never reached GitHub and the app could not install from it.
    result = subprocess.run(["git", "check-ignore", "-q", ".agents/plugins/marketplace.json"], cwd=ROOT)
    assert result.returncode == 1, ".agents/plugins/marketplace.json is git-ignored: the Git marketplace would be empty"


def test_name_and_version_agree_in_every_manifest():
    claude = load(CLAUDE_PLUGIN_DIR / ".claude-plugin" / "plugin.json")
    portable = load(CLAUDE_PLUGIN_DIR / "plugin.json")
    codex = load(CODEX_PLUGIN_DIR / ".codex-plugin" / "plugin.json")
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
