"""#102: every tool an agent, a skill, a generated host file, an MCP prompt or the server instructions name exists. Rename or remove a
tool and the text that still names it fails here, with the file and the name.

tests/test_skills.py checks the skills' own `metadata.vault-tools` and body; this file covers the rest of what tells an assistant
which tools to call, in every format the hosts read (Claude Code, Copilot, Codex, Cursor, ChatGPT)."""

import re
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from vault.api import mcp
from vault.api.mcp_catalog import GROUNDING, PROMPTS

from test_skills import FIELDS  # fields of tool answers and arguments that are not tools

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_plugin as bp  # noqa: E402

TOOLS = set(mcp.BY_NAME)
# Bare (not backticked) words in prompts and instructions that look like tool names but are fields, arguments or places.
BARE_OK = FIELDS | {"deck_id", "bucket_id", "budget_usd", "price_date", "share_id", "oracle_id", "card_id", "upload_id", "import_id",
                    "invite_token", "next_cursor", "next_offset", "include_combos", "total_usd", "known_card", "backup_download", "not_computed"}
NAME = re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")
BACKTICKED = re.compile(r"`([a-z]+(?:_[a-z0-9]+)+)`")


def split(path: Path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    front, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(front), body


def unknown_tool_names(names, tools=TOOLS, fields=BARE_OK) -> set[str]:
    """Names that are neither a tool nor a known field: a tool that was renamed or removed shows up here."""
    return {n for n in names if n not in tools and n not in fields}


def test_a_renamed_tool_is_found_in_text_that_still_names_it():
    """The check itself: with `get_deck_ideas` renamed, text naming the old name is reported, and the new name is not a tool yet."""
    renamed = (TOOLS - {"get_deck_ideas"}) | {"get_deck_lanes"}
    assert unknown_tool_names(BACKTICKED.findall("Call `get_deck_ideas` then `get_deck`."), renamed) == {"get_deck_ideas"}
    assert unknown_tool_names(BACKTICKED.findall("Call `get_deck_lanes`."), renamed) == set()


# ---- the agents ----------------------------------------------------------------------------------------------------------

AGENT_FILES = sorted((ROOT / "agents").glob("*.md"))


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_an_agent_names_only_tools_that_exist(path):
    meta, body = split(path)
    assert set(meta["vault-tools"]) <= TOOLS, f"{path.name}: unknown tools in vault-tools {set(meta['vault-tools']) - TOOLS}"
    assert unknown_tool_names(BACKTICKED.findall(body)) == set(), f"{path.name}: its instructions name a tool that does not exist"


# ---- what is generated for each host -------------------------------------------------------------------------------------


def test_the_claude_code_agents_allow_only_existing_tools():
    prefix = f"mcp__plugin_{bp.NAME}_{bp.NAME}__"
    seen = set()
    for path in sorted((bp.PLUGIN / "agents").glob("*.md")):
        meta, _ = split(path)
        names = {t.strip().removeprefix(prefix) for t in meta["tools"].split(",")}
        assert all(t.strip().startswith(prefix) for t in meta["tools"].split(",")), path.name
        assert names <= TOOLS, f"{path.name}: {sorted(names - TOOLS)}"
        seen |= names
    assert seen, "no Claude Code agents were generated"


def test_the_copilot_agents_allow_only_existing_tools_in_the_plugin_and_standalone():
    for folder, server in ((bp.PLUGIN / "com.github.copilot" / "agents", bp.NAME), (bp.DEFS / "copilot", "vault")):
        files = sorted(folder.glob("*.agent.md"))
        assert files, folder
        for path in files:
            meta, _ = split(path)
            names = {t.removeprefix(f"{server}/") for t in meta["tools"]}
            assert all(t.startswith(f"{server}/") for t in meta["tools"]) and names <= TOOLS, f"{path.name}: {sorted(names - TOOLS)}"


def test_the_codex_agents_enable_only_existing_tools():
    files = sorted((bp.DEFS / "codex").glob("*.toml"))
    assert files
    for path in files:
        enabled = set(tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["vault"]["enabled_tools"])
        assert enabled <= TOOLS, f"{path.name}: {sorted(enabled - TOOLS)}"


def test_the_generated_agent_text_for_every_host_names_only_existing_tools():
    """The instructions inside each generated file (Claude Code, Copilot, Codex, Cursor) and the experts that ChatGPT and Codex get as skills."""
    texts = {}
    for folder, pattern in ((bp.PLUGIN / "agents", "*.md"), (bp.PLUGIN / "com.github.copilot" / "agents", "*.agent.md"), (bp.DEFS / "cursor", "*.md"),
                            (bp.DEFS / "copilot", "*.agent.md"), (bp.DEFS / "codex", "*.toml"), (bp.OPENAI / "skills", "vault-*/SKILL.md")):
        for path in folder.glob(pattern):
            texts[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    assert len(texts) >= 5 * len(AGENT_FILES) - 5
    bad = {name: unknown_tool_names(BACKTICKED.findall(text)) for name, text in texts.items()}
    assert {k: v for k, v in bad.items() if v} == {}


def test_every_skill_the_hosts_receive_declares_and_names_only_existing_tools():
    """The plugin (Claude Code, Copilot, Cursor) and the ChatGPT/Codex package carry the skills and the experts-as-skills."""
    seen = 0
    for root in (bp.PLUGIN / "skills", bp.OPENAI / "skills"):
        for path in sorted(root.glob("*/SKILL.md")):
            meta, body = split(path)
            declared = set(meta["metadata"]["vault-tools"].split())
            assert declared <= TOOLS, f"{path.relative_to(ROOT)}: {sorted(declared - TOOLS)}"
            assert unknown_tool_names(BACKTICKED.findall(body)) == set(), str(path.relative_to(ROOT))
            seen += 1
    assert seen >= 2 * 8


# ---- prompts and instructions ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p["name"])
def test_an_mcp_prompt_names_only_existing_tools(prompt):
    text = prompt["text"] + GROUNDING
    args = {a["name"] for a in prompt["arguments"]}
    assert set(NAME.findall(text)) & TOOLS, "the check must see the tool names it is checking"
    assert unknown_tool_names(NAME.findall(text), fields=BARE_OK | args) == set(), f"prompt {prompt['name']} names a tool that does not exist"


def test_the_server_instructions_name_only_existing_tools():
    assert unknown_tool_names(NAME.findall(mcp.INSTRUCTIONS)) == set()
