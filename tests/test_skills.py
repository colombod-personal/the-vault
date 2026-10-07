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
          "price_date", "store_format", "no_qualifying_printing", "not_checked", "include_sideboard", "choose_printing", "printing_unknown", "did_you_mean", "format_from", "colour_warning", "bracket_floor",
          "use_app_value", "replace_everything"}


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
    """The shop links are the formats checked on 2026-10-04 (issue #80). Magic Madhouse's was corrected on 2026-10-07 (the
    old ?q= format showed the home page, not results) and Cardmarket's could not be re-checked (bot check). The skill must
    not claim to edit Archidekt or know a shop's price, since the Vault has neither (docs/compliance.md)."""
    meta, body = parse(SKILLS / "archidekt-deck-helper")
    assert "get_archidekt_deck" in meta["metadata"]["vault-tools"]
    for link in ("https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=CARD+NAME",
                 "https://www.cardmarket.com/en/Magic/Products/Search?searchString=CARD+NAME",
                 "https://magicmadhouse.co.uk/search.php?search_query=CARD+NAME"):
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


# #97: every skill that works on one deck carries the whole flow: find it by the person's words (list_decks with query,
# `closest` when nothing matches), show it (get_deck), and when it is not saved read or save it from its Archidekt link
# (get_archidekt_deck, import_deck_from_link). The same flow is in the server instructions for hosts without skills
# (tests/test_mcp_catalog.py::test_hosts_without_the_skills_still_get_the_shop_and_deck_rules).
DECK_FLOW_TOOLS = ("list_decks", "get_deck", "import_deck_from_link", "get_archidekt_deck")
DECK_FLOW_SKILLS = ("shopping-assistant", "deck-upgrader", "archidekt-deck-helper")


def deck_flow_problems(meta: dict, body: str) -> list[str]:
    """What is missing from a skill's find-the-deck-by-name flow (empty when it is all there)."""
    text = " ".join(body.split())
    declared = set(meta["metadata"]["vault-tools"].split())
    problems = [f"does not declare {t}" for t in DECK_FLOW_TOOLS if t not in declared]
    if not re.search(r"`list_decks` with\s+`query`", text):
        problems.append("no by-name step: list_decks with query")
    if "`closest`" not in text:
        problems.append("does not say what to do when no deck matches (closest)")
    if "`get_deck`" not in text:
        problems.append("never shows the saved deck (get_deck)")
    if not re.search(r"`import_deck_from_link`", text) or not re.search(r"`get_archidekt_deck`", text):
        problems.append("no link step (import_deck_from_link or get_archidekt_deck)")
    if not re.search(r"not saved", text):
        problems.append("does not say the link step applies only when the deck is not saved")
    return problems


@pytest.mark.parametrize("name", DECK_FLOW_SKILLS)
def test_deck_skills_carry_the_whole_find_the_deck_by_name_flow(name):
    meta, body = parse(SKILLS / name)
    assert deck_flow_problems(meta, body) == []


@pytest.mark.parametrize("name", DECK_FLOW_SKILLS)
def test_removing_a_step_of_the_flow_fails_the_check(name):
    """The check must notice each step going missing: this is what the audit found (#97) that no test would."""
    meta, body = parse(SKILLS / name)
    flat = " ".join(body.split())
    without_by_name = re.sub(r"`list_decks` with\s+`query`", "`list_decks`", flat)
    assert any("by-name" in p for p in deck_flow_problems(meta, without_by_name))
    without_link = flat.replace("`import_deck_from_link`", "the import").replace("`get_archidekt_deck`", "the reader")
    assert any("link step" in p for p in deck_flow_problems(meta, without_link))
    without_show = flat.replace("`get_deck`", "the deck")
    assert any("get_deck" in p for p in deck_flow_problems(meta, without_show))
    undeclared = {**meta, "metadata": {**meta["metadata"], "vault-tools": meta["metadata"]["vault-tools"].replace("get_deck", "")}}
    assert any("does not declare" in p for p in deck_flow_problems(undeclared, body))
