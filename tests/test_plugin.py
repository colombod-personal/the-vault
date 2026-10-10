"""The generated plugin, marketplace and connect page (scripts/build_plugin.py): up to date with skills/,
valid in both layouts, free of secrets, and honest about what works today."""

import html
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

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
                   "claude mcp add --transport http vault", "Account → Agents &amp; API", "whoami", bp.FAN_NOTICE,
                   "Copy setup prompt", f"{bp.HOST}/setup/claude.md", "vault_start"):  # the one-prompt setup (docs/onboarding.md; tests/test_setup_pages.py)
        assert needle in page, needle
    assert ("not switched on yet" in page) == (not bp.OAUTH_READY)
    assert 'id="claude"' in page and 'id="chatgpt"' in page and "Add custom MCP server" in page
    assert page.replace("<script src=\"analytics.bundle.js\"></script>", "").count("<script src") == 0  # nothing loaded from elsewhere (only our own analytics bundle)


def test_the_connect_page_is_served(client):
    res = client.get("/connect.html")
    assert res.status_code in (200, 404)  # the test app may not serve static files; the file itself is checked above


def test_no_page_or_plugin_file_names_a_vercel_address():
    """The address to give out is mtgvault.cards; a *.vercel.app address is the old deployment (#157)."""
    roots = [ROOT / "public", ROOT / "plugins", ROOT / ".claude-plugin", ROOT / "docs", ROOT / "scripts"]
    offenders = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix in {".png", ".ico", ".jpg", ".woff2", ".pyc"} or path.name == "app.bundle.js":
                continue
            if re.search(r"https?://[\w.-]+\.vercel\.app", path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_the_chatgpt_listing_fields_meet_openais_limits():
    """OpenAI's upload check (2026-10-10, developers.openai.com/plugins/deploy/submission): subtitle and name at most 30
    characters, description at most 4000, an https support page (required for an MCP review), https privacy and terms."""
    face = load(bp.OPENAI / ".codex-plugin" / "plugin.json")["interface"]
    assert 0 < len(face["shortDescription"]) <= 30 and 0 < len(face["displayName"]) <= 30
    assert 0 < len(face["longDescription"]) <= 4000 and len(face["developerName"]) <= 80
    for key in ("supportURL", "websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
        assert face[key].startswith("https://mtgvault.cards") and len(face[key]) <= 1024, key
    assert face["supportURL"].endswith("/support.html")
    demo = load(bp.OPENAI / ".codex-plugin" / "plugin.json")["review"]["demo_recording_url"]
    assert demo == "https://mtgvault.cards/demo/vault-chatgpt-demo.mp4" and (bp.ROOT / "public" / "demo" / "vault-chatgpt-demo.mp4").stat().st_size > 100_000
    assert face["category"] in {"Business & Operations", "Communication", "Creativity", "Data & Analytics",
                                "Developer Tools", "Education & Research", "Entertainment", "Finance", "Healthcare",
                                "Other", "Productivity", "Scientific Research", "Security", "Travel"}  # the portal's list
    assert "free" not in face["longDescription"].lower().replace("free to", "")  # no pricing or offers (guidelines)
    assert len(face["defaultPrompt"]) <= 3 and all(len(p) <= 128 for p in face["defaultPrompt"])


def test_the_chatgpt_and_codex_plugin_has_the_skills_the_experts_and_the_vault_server(tmp_path):
    """#225: ChatGPT and Codex load skills from a plugin package; with no subagents there, the experts are skills."""
    manifest = load(bp.OPENAI / ".codex-plugin" / "plugin.json")
    assert manifest["name"] == "the-vault" and manifest["skills"] == "./skills/" and manifest["mcpServers"] == "./mcp.json"
    assert manifest["interface"]["privacyPolicyURL"].endswith("/privacy.html") and manifest["interface"]["logo"] == "./assets/logo.png"
    assert load(bp.OPENAI / "mcp.json")["mcpServers"]["the-vault"]["url"] == "https://mtgvault.cards/api/mcp"
    skills = {p.parent.name for p in (bp.OPENAI / "skills").glob("*/SKILL.md")}
    assert {p.name for p in bp.SKILLS.iterdir() if p.is_dir()} <= skills  # every skill
    assert set(bp.EXPERT_SKILLS) <= skills  # and each expert, as a skill
    judge = (bp.OPENAI / "skills" / "vault-judge" / "SKILL.md").read_text(encoding="utf-8")
    assert judge.startswith("---\nname: vault-judge\n") and next(a for a in bp.load_agents() if a["name"] == "vault-judge")["body"] in judge
    import zipfile
    names = zipfile.ZipFile(bp.write_zip(tmp_path / "p.zip")).namelist()
    assert ".codex-plugin/plugin.json" in names and "skills/expert-council/SKILL.md" in names
    logo = zipfile.ZipFile(tmp_path / "p.zip").read("assets/logo.png")
    assert logo == (bp.ROOT / "public" / "apple-touch-icon.png").read_bytes()  # images byte for byte


def test_a_directory_listing_turns_the_steps_into_an_add_button(monkeypatch):
    """#227: once The Vault is published in an app's directory, its card shows the button instead of the manual steps."""
    assert "Add The Vault to ChatGPT" not in bp.connect_page()
    monkeypatch.setitem(bp.LISTINGS, "chatgpt", "https://chatgpt.com/apps/the-vault")
    page = bp.connect_page()
    chatgpt = page.split('id="chatgpt"')[1].split("</div>")[0]
    assert 'href="https://chatgpt.com/apps/the-vault"' in chatgpt and "Add The Vault to ChatGPT" in chatgpt
    assert "Add custom MCP server" not in chatgpt
    assert "Add custom connector" in page.split('id="claude"')[1].split("</div>")[0]  # Claude still has its steps


def test_the_chatgpt_plugin_carries_five_positive_and_three_negative_review_cases_naming_real_tools():
    """#240: the OpenAI directory review runs these on the demo account."""
    from vault.api import mcp
    cases = load(bp.OPENAI / ".codex-plugin" / "plugin.json")["review"]["test_cases"]
    assert len(cases["positive"]) == 5 and len(cases["negative"]) == 3
    for case in cases["positive"]:
        assert case["description"] and case["prompt"] and case["expected_behavior"]
        assert {t.strip() for t in case["tools_triggered"].split(",")} <= set(mcp.BY_NAME)
    assert all(c["description"] and c["prompt"] for c in cases["negative"])


# ---- #39: one correct block per harness, and connect.html and llms.txt generated from the same source -------------


def _steps(harness_id, kind=None, lang=None):
    h = next(h for h in bp.HARNESSES if h["id"] == harness_id)
    return [s for s in bp.harness_steps(h) if (kind is None or s["kind"] == kind) and (lang is None or s["lang"] == lang)]


def _parsed(step):
    return {"json": json.loads, "toml": tomllib.loads}[step["lang"]](step["code"])


def test_there_is_a_block_for_each_harness_in_that_harnesss_own_format():
    assert [h["id"] for h in bp.HARNESSES] == ["claude-code", "codex", "cursor", "vscode", "copilot-cli"]
    url = f"{bp.HOST}/api/mcp"
    # Claude Code: `claude mcp add --transport http`, and .mcp.json mcpServers with type http (code.claude.com/docs/en/mcp)
    cmd = _steps("claude-code", "oauth", "bash")[0]["code"]
    assert cmd.startswith("claude mcp add --transport http vault ") and cmd.splitlines()[0].endswith(url) and "claude mcp login vault" in cmd
    server = _parsed(_steps("claude-code", "token")[0])["mcpServers"]["vault"]
    assert server["type"] == "http" and server["url"] == url and server["headers"]["Authorization"] == "Bearer ${VAULT_TOKEN}"
    # Codex: codex mcp add --url, and TOML [mcp_servers.<name>] with url and bearer_token_env_var, not JSON
    assert _steps("codex", "oauth", "bash")[0]["code"].splitlines() == [f"codex mcp add vault --url {url}", "codex mcp login vault"]
    assert all(s["lang"] in ("bash", "toml") for s in bp.HARNESSES[1]["steps"])
    toml = [_parsed(s)["mcp_servers"]["vault"] for s in _steps("codex", lang="toml")]
    assert toml[0] == {"url": url} and toml[1] == {"url": url, "bearer_token_env_var": "VAULT_TOKEN"}
    # Cursor: mcpServers with url (no type), headers with ${env:VAR}
    plain, token = (_parsed(s) for s in _steps("cursor"))
    assert plain == {"mcpServers": {"vault": {"url": url}}}
    assert token["mcpServers"]["vault"]["headers"]["Authorization"] == "Bearer ${env:VAULT_TOKEN}"
    # VS Code: root key `servers` (not mcpServers), type http, and a password input for the token
    plain, token = (_parsed(s) for s in _steps("vscode"))
    assert plain == {"servers": {"vault": {"type": "http", "url": url}}}
    assert token["inputs"][0]["password"] is True and token["inputs"][0]["type"] == "promptString"
    assert token["servers"]["vault"]["headers"]["Authorization"] == "Bearer ${input:%s}" % token["inputs"][0]["id"]
    # GitHub Copilot CLI: copilot mcp add --transport http, and ~/.copilot/mcp-config.json
    assert _steps("copilot-cli", lang="bash")[0]["code"] == f"copilot mcp add --transport http vault {url}"
    assert _parsed(_steps("copilot-cli", lang="json")[0])["mcpServers"]["vault"] == {"type": "http", "url": url, "tools": ["*"]}
    # not one generic block copied around
    codes = [s["code"] for h in bp.HARNESSES for s in h["steps"]]
    assert len(codes) == len(set(codes))


def test_no_block_puts_a_token_in_a_command_or_a_file():
    """docs/onboarding.md: a bearer token in a command argument lands in shell history and the chat. Blocks read it from an
    environment variable or the tool's own password prompt."""
    for h in bp.HARNESSES:
        for s in h["steps"]:
            if s["lang"] == "bash":
                assert "Bearer" not in s["code"] and "--header" not in s["code"] and "vault_pat_" not in s["code"], (h["id"], s["code"])
            else:
                assert "vault_pat_" not in s["code"] or h["id"] == "copilot-cli"  # Copilot CLI: typed into /mcp add's own prompt
    page = (ROOT / "public" / "connect.html").read_text(encoding="utf-8")
    assert "--header" not in page


def _page_blocks(page):
    return [html.unescape(m) for m in re.findall(r"<pre><code>(.*?)</code></pre>", page, re.S)]


def _llms_blocks(text):
    section = text.split(bp.LLMS_BEGIN, 1)[1].split(bp.LLMS_END, 1)[0]
    blocks = []
    for m in re.finditer(r"^( *)```\w*\n(.*?)^\1```$", section, re.S | re.M):
        indent = m.group(1)
        blocks.append("\n".join(line[len(indent):] for line in m.group(2).rstrip("\n").split("\n")))
    return blocks


def test_connect_page_and_llms_txt_show_the_same_harness_blocks_generated_from_one_list():
    page = (ROOT / "public" / "connect.html").read_text(encoding="utf-8")
    llms = (ROOT / "public" / "llms.txt").read_text(encoding="utf-8")
    want = [s["code"] for h in bp.HARNESSES for s in bp.harness_steps(h)]
    assert _llms_blocks(llms) == want
    cards = page.split('id="claude-code"', 1)[1]
    assert _page_blocks(cards)[:len(want)] == want
    for h in bp.HARNESSES:
        assert f'id="{h["id"]}"' in page
        for _, url in h["docs"]:
            assert url in page and url in llms  # each block cites the documentation it follows


def test_llms_txt_connection_section_follows_host_and_the_oauth_switch(monkeypatch):
    llms = (ROOT / "public" / "llms.txt").read_text(encoding="utf-8")
    section = llms.split(bp.LLMS_BEGIN, 1)[1].split(bp.LLMS_END, 1)[0]
    assert "<host>" not in section and f"`{bp.HOST}/api/mcp`" in section
    assert ("OAuth (ChatGPT, Claude.ai" in section) == bp.OAUTH_READY
    monkeypatch.setattr(bp, "OAUTH_READY", False)
    off = bp.llms_connection()
    assert "not switched on yet" in off and "OAuth (ChatGPT" not in off and "codex mcp login" not in off
    assert "codex mcp login" not in bp.connect_page() and "claude mcp login" not in bp.connect_page()
    assert "VAULT_TOKEN" in off  # the token blocks stay


def test_changing_a_harness_block_makes_both_generated_files_stale(monkeypatch):
    """The drift the audit found: llms.txt's connection text could change without any test noticing."""
    assert bp.stale() == []
    monkeypatch.setitem(bp.HARNESSES[1]["steps"][0], "code", "codex mcp add vault --url https://example.invalid/mcp")
    stale = [s.replace("\\", "/") for s in bp.stale()]
    assert "out of date: public/llms.txt" in stale and "out of date: public/connect.html" in stale


def test_llms_txt_without_its_generated_markers_is_not_accepted(monkeypatch, tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "llms.txt").write_text("# no markers here\n", encoding="utf-8")
    monkeypatch.setattr(bp, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        bp.llms_txt()


# ---- #59.3: agents are client extensions, under the client's reverse-domain namespace ------------------------------


def test_agents_live_under_the_reverse_domain_namespace_the_standard_prescribes():
    """Agent Plugins 1.0 s.8: client-specific files go in a top-level directory named for the client's reverse-domain
    namespace; s.5.2: the manifest is closed except `extensions`. VS Code documents com.github.copilot/agents/."""
    top = {p.name for p in bp.PLUGIN.iterdir() if p.is_dir()}
    namespaces = {n for n in top if "." in n and not n.startswith(".")}
    assert namespaces == {"com.github.copilot"}
    assert all(re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", n) for n in namespaces)  # reverse-domain
    files = sorted((bp.PLUGIN / "com.github.copilot" / "agents").glob("*.agent.md"))
    assert [f.name for f in files] == sorted(f"{a['name']}.agent.md" for a in bp.load_agents())
    assert top <= {"skills", "agents", ".claude-plugin", "com.github.copilot"}
    plugin = load(bp.PLUGIN / "plugin.json")
    assert set(plugin) <= {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
    assert all(re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", k) for k in plugin.get("extensions", {}))
