"""The expert council for connectors (vault/experts.py, #220): the panel the council's rules seat, each member's full
brief from the same agent files the plugin installs, and the tools that hand them to Claude and ChatGPT."""

import json
import sys
from pathlib import Path

import pytest

from test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from vault import experts

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_plugin as bp  # noqa: E402

FORMAT_EXPERTS = {"vault-commander-expert", "vault-casual-table", "vault-limited-expert", "vault-pauper-expert",
                  "vault-standard-expert", "vault-pioneer-expert", "vault-two-headed-giant-expert"}


def ids(panel):
    return [m["id"] for m in panel]


def test_commander_seats_the_commander_expert_and_the_casual_table_and_no_other_format():
    panel = ids(experts.seat("commander", "tune it"))
    assert panel[:2] == ["vault-commander-expert", "vault-casual-table"]
    assert panel[-2:] == ["vault-judge", "vault-devils-advocate"]  # always, the devil's advocate last
    assert not (set(panel) & FORMAT_EXPERTS) - {"vault-commander-expert", "vault-casual-table"}


@pytest.mark.parametrize("fmt,expert", [("limited", "vault-limited-expert"), ("draft", "vault-limited-expert"),
                                        ("Pauper", "vault-pauper-expert"), ("standard", "vault-standard-expert"),
                                        ("pioneer", "vault-pioneer-expert"), ("EDH", "vault-commander-expert")])
def test_each_format_seats_only_its_own_expert(fmt, expert):
    seated = set(ids(experts.seat(fmt, "check it"))) & FORMAT_EXPERTS
    assert expert in seated and seated <= {expert, "vault-casual-table"}


def test_two_headed_giant_adds_the_expert_for_the_format_the_team_plays():
    panel = ids(experts.seat("2HG", "synergies", team_format="standard"))
    assert "vault-two-headed-giant-expert" in panel and "vault-standard-expert" in panel
    assert "vault-commander-expert" not in panel


def test_an_unknown_format_runs_without_a_format_expert_and_says_so():
    brief = experts.council("vintage", "check it")
    assert not set(ids(brief["panel"])) & FORMAT_EXPERTS and "no vintage expert" in brief["note"].lower()


def test_analysts_join_when_the_goal_calls_for_them_and_the_panel_stays_small():
    assert "vault-collection-analyst" in ids(experts.seat("commander", "what can I build on a budget"))
    assert "vault-collection-analyst" in ids(experts.seat("commander", "check it", budget=True))
    assert "vault-collection-analyst" not in ids(experts.seat("commander", "is it legal"))
    assert all(4 <= len(experts.seat(f, g, budget=True)) <= 6 for f in ("commander", "pauper", "two-headed-giant")
               for g in ("tune", "legal?", "budget synergies"))


def test_briefs_are_the_plugins_agent_files_and_the_chairs_procedure():
    agents = {a["name"]: a for a in bp.load_agents()}
    assert set(experts.DATA["experts"]) == set(agents)
    for name, agent_def in agents.items():
        assert experts.brief(name)["brief"] == agent_def["body"]
    assert "Independent views" in experts.DATA["chair"]["brief"]  # skills/expert-council/SKILL.md
    assert bp.stale() == [], "run: python scripts/build_plugin.py (vault/experts_data.py is generated)"


def test_a_council_brief_fits_in_a_chat():
    brief = experts.council("commander", "tune it on a budget", budget=True)
    assert len(json.dumps(brief)) < 30_000
    assert all(m["brief"] and m["why"] for m in brief["panel"])


def test_the_tools_hand_the_panel_to_any_assistant_and_read_the_format_from_a_saved_deck(agent, bot):
    read = make_token(agent)
    deck = agent.post(f"{V1}/decks", json={"name": "Sliver Swarm", "text": "Commander\n1 Sliver Overlord\n\nDeck\n99 Island"}).json()
    brief = call_tool(bot, read, "council_brief", deck_id=deck["id"], goal="tune it")["structuredContent"]
    assert brief["format"] == "commander" and "Sliver Overlord" in brief["format_note"]
    assert ids(brief["panel"])[:2] == ["vault-commander-expert", "vault-casual-table"]
    assert "chair" in brief and "not independent" in brief["how_to_run"]
    judge = call_tool(bot, read, "expert_brief", expert="vault-judge")["structuredContent"]
    assert judge["id"] == "vault-judge" and judge["brief"] == experts.brief("vault-judge")["brief"]
    from test_agents import rpc
    nope = rpc(bot, "tools/call", {"name": "expert_brief", "arguments": {"expert": "vault-nope"}}, read).json()
    assert nope["error"]["code"] == -32602  # not one of the experts
    assert agent.get(f"{V1}/experts").json()["experts"][0]["id"].startswith("vault-")


def test_another_persons_deck_is_not_read(agent, bot):
    from fastapi.testclient import TestClient
    deck = agent.post(f"{V1}/decks", json={"name": "Mine", "text": "Commander\n1 Sliver Overlord"}).json()
    with TestClient(agent.app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        bob_read = make_token(bob)
    assert call_tool(bot, bob_read, "council_brief", deck_id=deck["id"]).get("isError")


RULES = ("exactly as", "no power score", "does not check colours", "from memory")  # #235: what keeps a review grounded


def test_every_payload_the_assistant_reads_carries_the_grounding_rules():
    """#235: in ChatGPT the council review called the deck 'low and weak' and misquoted numbers: nothing told it not to.
    The rules are in the server instructions, the council brief the connector hands over, and the expert-council skill
    (the Claude plugin and the ChatGPT plugin copy it)."""
    from vault.api import mcp
    skill = (bp.ROOT / "skills" / "expert-council" / "SKILL.md").read_text(encoding="utf-8")
    brief = json.dumps(experts.council("commander", "tune it"))
    for where, text in {"server instructions": mcp.INSTRUCTIONS, "council_brief": brief, "expert-council skill": skill}.items():
        lowered = " ".join(text.lower().split())
        for rule in RULES:
            assert rule in lowered or (rule == "no power score" and "has no power score" in lowered), (where, rule)
    assert "exactly as" in (bp.OPENAI / "skills" / "expert-council" / "SKILL.md").read_text(encoding="utf-8").lower()
