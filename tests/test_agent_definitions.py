"""Agent definitions (agents/*.md and what scripts/build_plugin.py makes of them for Claude Code, Codex, Cursor
and GitHub Copilot): tied to real tools and skills, limited to the Vault's tools, faithful to the attribution
rules, and every generated file parses in its own format."""

import re
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


FORMAT_EXPERTS = {"vault-commander-expert": "commander", "vault-limited-expert": "draft and sealed",
                  "vault-pauper-expert": "pauper", "vault-standard-expert": "standard", "vault-pioneer-expert": "pioneer",
                  "vault-two-headed-giant-expert": "two-headed giant"}
COUNCIL = {"vault-devils-advocate", "vault-synergy-analyst", "vault-collection-analyst", "vault-casual-table"} | set(FORMAT_EXPERTS)


def test_the_agents_exist():
    assert NAMES == {"vault-judge", "vault-deckbuilder", "vault-buyer", "vault-curator"} | COUNCIL


def test_council_members_stay_in_their_lane_and_cite_evidence():
    """docs/expert-council.md: members give at most three points with evidence; format experts and the casual table
    speak only about their format (owner's rule: off-topic format experts derail the discussion)."""
    by = {a["name"]: " ".join(a["body"].split()).lower() for a in AGENTS}
    for name in COUNCIL:
        # a member is seated by the council skill, it does not convene one itself (#247)
        assert "at most three" in by[name] and "expert-council" not in {s for a in AGENTS if a["name"] == name for s in a["skills"]}
    assert "commander only" in by["vault-casual-table"]
    for name, fmt in FORMAT_EXPERTS.items():
        assert f"{fmt} only" in by[name], name  # speaks about its own format only
        assert "only when the question is about" in " ".join(next(a for a in AGENTS if a["name"] == name)["description"].split()).lower()
    assert "never object without a reason you can cite" in by["vault-devils-advocate"]
    assert "never say which shop is cheapest" in by["vault-collection-analyst"]


def test_the_council_skill_seats_only_on_topic_experts_and_validates_the_plan():
    text = " ".join((ROOT / "skills" / "expert-council" / "SKILL.md").read_text(encoding="utf-8").split())
    assert "Only the format expert for the format being discussed" in text and "Never seat an expert for another format" in text
    for name in COUNCIL | {"vault-judge"}:
        assert f"`{name}`" in text, name
    assert "present a plan only when `valid` is true" in text
    for section in ("**plan**", "**Agreed:**", "**Disputed:**", "**Not checked:**", "see the discussion"):
        assert section in text, section


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


# ---- #59: each variant restricts the agent to the Vault's tools where its format can, and says only that --------------


@pytest.mark.parametrize("agent", AGENTS, ids=lambda a: a["name"])
def test_the_copilot_agent_has_an_allow_list_of_only_the_vaults_read_only_tools(agent):
    """GitHub/VS Code custom agents: `tools` lists the tools available to the agent (anything not listed is not);
    MCP tools are `<server>/<tool>`. Standalone files use the name the connect page gives the server (`vault`), the plugin's
    com.github.copilot/agents copy the plugin's own server name."""
    for path, server in ((bp.DEFS / "copilot" / f"{agent['name']}.agent.md", "vault"),
                         (bp.PLUGIN / "com.github.copilot" / "agents" / f"{agent['name']}.agent.md", "the-vault")):
        meta, _ = frontmatter(path.read_text(encoding="utf-8"))
        assert meta["tools"] == [f"{server}/{t}" for t in agent["tools"]]
        assert all(t.startswith(f"{server}/") and "*" not in t for t in meta["tools"])  # nothing built in, no wildcard
        assert not [t for t in agent["tools"] if mcp.BY_NAME[t].write]


@pytest.mark.parametrize("agent", AGENTS, ids=lambda a: a["name"])
def test_the_codex_agent_limits_the_vault_server_to_its_tools(agent):
    """Codex: `enabled_tools` is the allow list of one MCP server; a custom agent file may carry its own mcp_servers."""
    data = tomllib.loads((bp.DEFS / "codex" / f"{agent['name']}.toml").read_text(encoding="utf-8"))
    assert data["mcp_servers"]["vault"] == {"url": "https://mtgvault.cards/api/mcp", "enabled_tools": agent["tools"]}
    assert set(data) == {"name", "description", "sandbox_mode", "developer_instructions", "mcp_servers"}


@pytest.mark.parametrize("agent", AGENTS, ids=lambda a: a["name"])
def test_the_cursor_agent_claims_no_tool_list_because_cursors_format_has_none(agent):
    """Cursor's subagent frontmatter is name, description, model, readonly, is_background; subagents inherit the parent's tools.
    If Cursor adds a tool allow list, add it here and in the README table."""
    meta, _ = frontmatter((bp.DEFS / "cursor" / f"{agent['name']}.md").read_text(encoding="utf-8"))
    assert set(meta) == {"name", "description", "model", "readonly"} and meta["readonly"] is True


def test_the_readme_claims_only_what_each_variant_enforces():
    """The audit found the README saying every agent 'answers only through the Vault's tools' while only the Claude Code
    variant enforced it. The table now says, per assistant, what is enforced and what is only asked."""
    text = (bp.DEFS / "README.md").read_text(encoding="utf-8")
    assert "answers only through the Vault's tools" not in text
    rows = {m.group(1): m.group(2) for m in re.finditer(r"^\| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|$", text, re.M) if "allow list" in m.group(0) or "read-only" in m.group(0)}
    table = {k.strip(): v for k, v in rows.items()}
    assert "allow list" in table["Claude Code (the plugin)"] and "allow list" in table["GitHub Copilot and VS Code"]
    assert "enabled_tools" in table["Codex"] and "sandbox" in table["Codex"]
    assert "readonly: true" in table["Cursor"]
    # and the Not-enforced column admits it for the two that cannot
    assert re.search(r"\| Codex \|[^|]+\| Codex's other tools", text) and re.search(r"\| Cursor \|[^|]+\| Which tools it uses", text)
    # the copilot claim is conditional on the server name, and the unverified status is stated
    assert "`vault`" in text and "have not been run" in text and "com.github.copilot" in text


def test_the_format_experts_that_do_not_exist_are_recorded_as_an_owner_decision():
    """#104: Modern, Legacy, cEDH, Brawl and Cube experts are deferred by the owner's own build order (docs/expert-council.md,
    Decisions item 3), not forgotten: the doc must say which are missing, and that must stay true of the agents."""
    doc = " ".join((ROOT / "docs" / "expert-council.md").read_text(encoding="utf-8").split())
    assert "Owner decision needed: the deferred format experts" in doc and "waive the Modern and Legacy part" in doc
    for fmt in ("modern", "legacy", "cedh", "brawl", "cube", "vintage", "oathbreaker"):
        assert not [n for n in NAMES if fmt in n], f"an agent for {fmt} exists: update docs/expert-council.md (owner decision)"
        assert fmt.lower() in doc.lower() or fmt == "cedh" and "cEDH" in doc


# #172: find_combos lists only the combos Commander Spellbook knows, so no member may say "no infinite combos" because it found none
NO_INFINITE = [
    "skills/expert-council/SKILL.md",
    "plugins/the-vault/skills/expert-council/SKILL.md",
    "plugins/the-vault-openai/skills/expert-council/SKILL.md",
    *[f"{base}/vault-{member}{ext}" for member in ("casual-table", "commander-expert") for base, ext in (
        ("agents", ".md"), ("plugins/the-vault/agents", ".md"), ("agent-definitions/codex", ".toml"),
        ("agent-definitions/cursor", ".md"), ("agent-definitions/copilot", ".agent.md"),
        ("plugins/the-vault/com.github.copilot/agents", ".agent.md"))],
    "plugins/the-vault-openai/skills/vault-casual-table/SKILL.md",
    "plugins/the-vault-openai/skills/vault-commander-expert/SKILL.md",
]


@pytest.mark.parametrize("path", NO_INFINITE)
def test_no_one_says_no_infinite_combos_because_find_combos_found_none(path):
    raw = (Path(__file__).parent.parent / path).read_text(encoding="utf-8")
    text = " ".join(raw.replace(chr(92) + "n", " ").replace(chr(92), "").split())  # TOML and Python literals escape the quotes and newlines
    assert re.search(r'never (tell the person a deck has|say (that )?a deck has) "no infinite combos"', text), path
    assert "find_combos" in text and re.search(r"Commander Spellbook (knows|know)|known to Commander Spellbook", text), path


# #172: find_combos can also give the Vault's own reading of the card text (include_possible_loops). Whoever uses it must call it the
# Vault's reading and not Commander Spellbook's, never call a possible loop infinite or a combo, and never round "one mana short" up
HOSTS = (("agents", ".md"), ("plugins/the-vault/agents", ".md"), ("agent-definitions/codex", ".toml"), ("agent-definitions/cursor", ".md"),
         ("agent-definitions/copilot", ".agent.md"), ("plugins/the-vault/com.github.copilot/agents", ".agent.md"))
READING_MEMBERS = ("casual-table", "synergy-analyst", "devils-advocate")
READS_THE_NOTE = [
    "skills/expert-council/SKILL.md", "plugins/the-vault/skills/expert-council/SKILL.md", "plugins/the-vault-openai/skills/expert-council/SKILL.md",
    *[f"{base}/vault-{member}{ext}" for member in READING_MEMBERS for base, ext in HOSTS],
    *[f"plugins/the-vault-openai/skills/vault-{member}/SKILL.md" for member in READING_MEMBERS],
]


def flat(path):
    raw = (Path(__file__).parent.parent / path).read_text(encoding="utf-8")
    return " ".join(raw.replace(chr(92) + "n", " ").replace(chr(92), "").split())


@pytest.mark.parametrize("path", READS_THE_NOTE)
def test_whoever_uses_the_possible_loops_note_calls_it_the_vaults_reading_and_never_rounds_one_short_up(path):
    text = flat(path)
    assert "include_possible_loops" in text and "possible_loops" in text, path
    assert "the Vault's reading" in text and "not Commander Spellbook's" in text, path
    assert "one mana short" in text and "engine" in text, path
    assert re.search(r"never (that the deck has no loops|infinite|round it up)|and never round it up", text) or "does not show the deck has none" in text, path


@pytest.mark.parametrize("path", [p for p in READS_THE_NOTE if "devils-advocate" not in p])
def test_and_never_calls_a_possible_loop_infinite_a_combo_or_guaranteed(path):
    text = flat(path)
    assert re.search(r"never infinite, (never )?a combo,? (or|never) guaranteed|never infinite, never a combo, never guaranteed", text), path
