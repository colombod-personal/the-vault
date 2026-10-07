"""The skills (skills/*/SKILL.md): valid in the Agent Skills format (with a real YAML parse, which is what
`npx skills add` does), tied to tools that exist, and faithful to the attribution rules."""

import re
from pathlib import Path

import pytest
import yaml

from vault.api import mcp

SKILLS = Path(__file__).parent.parent / "skills"
FOLDERS = sorted(p for p in SKILLS.iterdir() if p.is_dir())
TOOLS = set(mcp.BY_NAME)
# Words in backticks that are fields of tool answers or arguments, not tools.
FIELDS = {"oracle_id", "source_text", "discard_may_be_the_plan", "deck_id", "budget_usd", "added_cost_usd", "known_card", "next_cursor", "share_id", "total_usd",
          "not_checked", "include_sideboard", "choose_printing", "printing_unknown", "did_you_mean", "format_from", "colour_warning", "bracket_floor", "include_combos"}


def parse(folder: Path):
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    front, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(front), body


def test_the_expected_skills_exist():
    assert {p.name for p in FOLDERS} == {"rules-judge", "interaction-explainer", "deck-upgrader", "shopping-assistant", "archidekt-deck-helper", "expert-council",
                                         "collection-analyst", "vault-attribution"}


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda p: p.name)
def test_frontmatter_follows_the_agent_skills_format(folder):
    meta, body = parse(folder)
    assert meta["name"] == folder.name and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"]) and len(meta["name"]) <= 64
    assert isinstance(meta["description"], str) and 40 < len(meta["description"]) <= 1024
    assert re.search(r"\bUse (when|for|whenever)\b", meta["description"]), "say when to use it"
    assert meta["license"] == "MIT" and body.strip().startswith("# ")
    assert all(isinstance(v, str) for v in meta["metadata"].values())  # the spec's metadata values are strings
    assert set(meta) <= {"name", "description", "license", "metadata", "compatibility", "allowed-tools"}


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda p: p.name)
def test_declared_tools_exist_and_are_the_only_real_tools_the_body_names(folder):
    meta, body = parse(folder)
    declared = set(meta["metadata"]["vault-tools"].split())
    assert declared <= TOOLS, f"unknown tools: {declared - TOOLS}"
    mentioned = set(re.findall(r"`([a-z]+(?:_[a-z0-9]+)+)`", body))
    assert mentioned & TOOLS <= declared, f"names tools it does not declare: {(mentioned & TOOLS) - declared}"
    assert declared <= mentioned | {"whoami"}, f"declares tools it never mentions: {declared - mentioned}"
    invented = {m for m in mentioned if m not in TOOLS and m not in FIELDS}
    assert not invented, f"unknown tool-like names: {invented}"


@pytest.mark.parametrize("folder", [f for f in FOLDERS if f.name != "vault-attribution"], ids=lambda p: p.name)
def test_skills_that_show_data_point_at_the_attribution_skill(folder):
    _, body = parse(folder)
    assert "vault-attribution" in body


def test_the_attribution_skill_keeps_the_hard_rules():
    _, body = parse(SKILLS / "vault-attribution")
    for phrase in ("Repeat the provenance", "Never present Wizards", "Fan Content", "never cropped", "Invent a rule number"):
        assert phrase in body or phrase.lower() in body.lower(), phrase
    assert "computed" in body and "source" in body


def test_no_skill_contains_rules_or_card_text():
    """Skills point at the tools, which return current text with its source. Nothing is pasted."""
    for folder in FOLDERS:
        _, body = parse(folder)
        assert not re.search(r"^\s*\d{3}\.\d+[a-z]?\.? [A-Z]", body, re.M), f"{folder.name} looks like it pastes rule text"


def test_the_archidekt_helper_reads_one_deck_and_links_shops_without_contacting_them():
    """The shop links are the formats checked on 2026-10-04 (issue #80); the skill must not claim to edit Archidekt
    or know a shop's price, since the Vault has neither (docs/compliance.md)."""
    meta, body = parse(SKILLS / "archidekt-deck-helper")
    assert "get_archidekt_deck" in meta["metadata"]["vault-tools"]
    for link in ("https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=CARD+NAME",
                 "https://www.cardmarket.com/en/Magic/Products/Search?searchString=CARD+NAME",
                 "https://magicmadhouse.co.uk/?q=CARD+NAME"):
        assert f"`{link}`" in body
    assert "You never edit Archidekt" in body and "One deck per" in body
    assert "cannot say which shop is cheapest" in body


AGENTS = sorted((Path(__file__).parent.parent / "agents").glob("*.md"))


def _agent(path: Path):
    front, body = path.read_text(encoding="utf-8").replace("\r\n", "\n")[4:].split("\n---\n", 1)
    return yaml.safe_load(front), body


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_an_agent_can_call_every_tool_its_skills_tell_it_to_and_only_real_tools(path):
    """#247: an agent that lists a skill but not the skill's tools is told to call things it is not allowed to; and a
    council member must not list the council skill, which sends it to convene another council. Agents never change
    data, so a skill's write steps are left to the main assistant (the generated note says so)."""
    meta, _ = _agent(path)
    allowed = set(meta["vault-tools"])
    assert allowed <= TOOLS, f"unknown tools: {allowed - TOOLS}"
    for name in meta.get("skills", []):
        skill, _ = parse(SKILLS / name)
        needed = {t for t in skill["metadata"]["vault-tools"].split() if not mcp.BY_NAME[t].write}  # agents are read-only (tests/test_agent_definitions.py)
        assert needed <= allowed, f"{meta['name']} lists the skill {name} but may not call {sorted(needed - allowed)}"
    if "expert council" in meta["description"]:
        assert "expert-council" not in meta.get("skills", []), "a member of the council does not convene it"
