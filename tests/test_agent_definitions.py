"""Agent definitions (agents/*.md and what scripts/build_plugin.py makes of them for Claude Code, Codex, Cursor
and GitHub Copilot): tied to real tools and skills, limited to the Vault's tools, faithful to the attribution
rules, and every generated file parses in its own format."""

import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from vault.api import mcp

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_plugin as bp  # noqa: E402

AGENTS = bp.load_agents()
NAMES = {a["name"] for a in AGENTS}
SKILL_NAMES = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}


def frontmatter(text: str):
    front, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(front), body


def test_the_three_agents_exist():
    assert NAMES == {"vault-judge", "vault-deckbuilder", "vault-buyer"}


@pytest.mark.parametrize("agent", AGENTS, ids=lambda a: a["name"])
def test_each_agent_uses_real_tools_and_skills_and_only_those(agent):
    assert set(agent["tools"]) <= set(mcp.BY_NAME) and set(agent["skills"]) <= SKILL_NAMES
    assert "vault-attribution" in agent["skills"]
    assert not [t for t in agent["tools"] if mcp.BY_NAME[t].write], "agents are read-only"
    body = " ".join(agent["body"].split())  # wrapped lines are one sentence
    for phrase in ("provenance", "never present", "only the Vault's tools"):
        assert phrase.lower() in body.lower(), f"{agent['name']} lacks: {phrase}"
    mentioned = {t for t in mcp.BY_NAME if f"`{t}`" in body}
    assert mentioned <= set(agent["tools"]), f"names tools it may not use: {mentioned - set(agent['tools'])}"


def test_the_judge_never_answers_from_memory_and_the_deckbuilder_validates_before_presenting():
    by = {a["name"]: " ".join(a["body"].split()) for a in AGENTS}
    assert "never answer a rules or card question from memory" in by["vault-judge"].lower() and "`verify_citation`" in by["vault-judge"]
    assert "`validate_deck_changes`" in by["vault-deckbuilder"] and "only when `valid` is true" in by["vault-deckbuilder"]
    assert "never contacts stores" in by["vault-buyer"].lower() or "does not contact stores" in by["vault-buyer"].lower()


@pytest.mark.parametrize("agent", AGENTS, ids=lambda a: a["name"])
def test_the_claude_agent_can_only_call_the_vaults_own_tools(agent):
    meta, body = frontmatter((bp.PLUGIN / "agents" / f"{agent['name']}.md").read_text(encoding="utf-8"))
    assert meta["name"] == agent["name"] and meta["model"] == "inherit"
    tools = [t.strip() for t in meta["tools"].split(",")]
    assert tools == [f"mcp__plugin_the-vault_the-vault__{t}" for t in agent["tools"]]  # nothing else: no files, shell or web
    assert not {"Bash", "Write", "Edit", "Read", "WebFetch"} & set(tools)
    assert meta["description"] == agent["description"] and agent["body"] in body


@pytest.mark.parametrize("agent", AGENTS, ids=lambda a: a["name"])
def test_the_codex_agent_is_valid_toml_and_read_only(agent):
    data = tomllib.loads((bp.DEFS / "codex" / f"{agent['name']}.toml").read_text(encoding="utf-8"))
    assert data["name"] == agent["name"] and data["description"] == agent["description"] and data["sandbox_mode"] == "read-only"
    assert agent["body"] in data["developer_instructions"]


@pytest.mark.parametrize("agent", AGENTS, ids=lambda a: a["name"])
def test_the_cursor_and_copilot_agents_parse_and_say_the_same_thing(agent):
    cursor, cbody = frontmatter((bp.DEFS / "cursor" / f"{agent['name']}.md").read_text(encoding="utf-8"))
    copilot, pbody = frontmatter((bp.DEFS / "copilot" / f"{agent['name']}.agent.md").read_text(encoding="utf-8"))
    assert cursor["readonly"] is True and cursor["name"] == copilot["name"] == agent["name"]
    assert agent["body"] in cbody and agent["body"] in pbody


def test_the_install_instructions_name_each_folder_and_say_what_is_unverified():
    text = (bp.DEFS / "README.md").read_text(encoding="utf-8")
    for needle in (".codex/agents/", ".cursor/agents/", ".github/agents/", "have not been run", "whoami"):
        assert needle in text, needle
