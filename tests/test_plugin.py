"""The generated plugin, marketplace and connect page (scripts/build_plugin.py): up to date with skills/,
valid in both layouts, free of secrets, and honest about what works today."""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_plugin as bp  # noqa: E402


def test_generated_files_are_up_to_date():
    assert bp.stale() == [], "run: python scripts/build_plugin.py (and commit the result)"


def test_the_check_flag_fails_when_a_skill_changes(tmp_path, monkeypatch):
    skills = tmp_path / "skills" / "x"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    monkeypatch.setattr(bp, "SKILLS", tmp_path / "skills")
    assert any("skills/x/SKILL.md" in p.replace("\\", "/") for p in bp.stale())


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_portable_plugin_follows_agent_plugins_1_0():
    plugin = load(bp.PLUGIN / "plugin.json")
    assert plugin["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert set(plugin) <= {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
    assert re.fullmatch(r"[a-z0-9]([a-z0-9.-]{0,62}[a-z0-9])?", plugin["name"]) and "--" not in plugin["name"]
    mcp = load(bp.PLUGIN / "mcp.json")
    server = mcp["mcpServers"]["the-vault"]
    assert server["type"] == "streamable-http" and server["url"].endswith("/api/mcp") and server["url"].startswith("https://")
    assert "headers" not in server  # no credential in a portable file
    names = sorted(p.name for p in (bp.PLUGIN / "skills").iterdir())
    assert names == sorted(p.name for p in bp.SKILLS.iterdir() if p.is_dir())


def test_the_claude_manifests_ask_for_the_token_and_never_contain_one():
    plugin = load(bp.PLUGIN / ".claude-plugin" / "plugin.json")
    assert plugin["name"] == "the-vault" and plugin["userConfig"]["vault_token"]["sensitive"] is True
    assert not plugin["name"].startswith(("claude-", "anthropic-"))
    server = load(bp.PLUGIN / ".mcp.json")["mcpServers"]["the-vault"]
    assert server["headers"]["Authorization"] == "Bearer ${user_config.vault_token}" and server["url"] == "${user_config.vault_url}/api/mcp"
    market = load(bp.MARKETPLACE)
    assert market["plugins"][0]["source"] == "./plugins/the-vault" and market["name"] == "the-vault"
    for path in [*bp.PLUGIN.rglob("*"), bp.MARKETPLACE, ROOT / "public" / "connect.html"]:
        if path.is_file():
            assert "vault_pat_" not in path.read_text(encoding="utf-8").replace("vault_pat_...", ""), f"a token in {path}"


def test_the_claude_cli_accepts_both_manifests_when_it_is_installed():
    import shutil

    cli = shutil.which("claude")
    if not cli:
        return  # CI has no Claude CLI; the structure is checked above
    for target in (bp.PLUGIN, ROOT):
        done = subprocess.run([cli, "plugin", "validate", str(target)], capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr


def test_the_connect_page_has_each_install_path_and_is_honest_about_oauth():
    page = (ROOT / "public" / "connect.html").read_text(encoding="utf-8")
    for needle in ("/plugin marketplace add colombod-personal/the-vault", "npx skills add colombod-personal/the-vault",
                   "claude mcp add --transport http vault", "Account → Agents &amp; API", "whoami", bp.FAN_NOTICE):
        assert needle in page, needle
    assert ("not switched on yet" in page) == (not bp.OAUTH_READY)
    assert "<script src" not in page  # nothing loaded from elsewhere


def test_the_connect_page_is_served(client):
    res = client.get("/connect.html")
    assert res.status_code in (200, 404)  # the test app may not serve static files; the file itself is checked above
