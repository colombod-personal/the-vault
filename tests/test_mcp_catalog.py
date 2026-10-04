"""The catalog and deck tools over MCP: every tool is classified for provenance, every answer that
carries third-party data says where it came from, read-only tokens can use the computations, and the
grounding rules reach the agent (instructions and prompts)."""

import pytest
from fastapi.testclient import TestClient

from tests.test_agents import CSV, auth, call_tool, make_token, rpc
from tests.test_catalog_api import BOLT, load
from tests.test_deck_api import CARDS, PRICES, TAGS, VALID, oid
from vault import provenance as prov
from vault.api import mcp
from vault.api.mcp_catalog import GROUNDING, PROMPTS, provenance_blocks


@pytest.fixture
def bot(app):
    """An agent's HTTP client: no browser cookies, only the token it is given."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def agent(signed_in, app):
    signed_in.post("/api/v1/imports", files={"file": ("e.csv", CSV, "text/csv")})
    load(app)
    from vault import catalog_sync as cs
    from vault.models import OracleCard, OracleTag, OracleTagLink
    with app.state.db.sessions() as db:  # added to what load() put there (a sync would replace it)
        cs._upsert(db, OracleCard, [cs.oracle_card_row(c) for c in CARDS], ("oracle_id",))
        cs._upsert(db, OracleTag, [{"id": "t-ramp", "slug": "ramp", "label": "ramp", "description": "", "parent_ids": [], "child_ids": []}], ("id",))
        cs._upsert(db, OracleTagLink, [{"tag_id": "t-ramp", "oracle_id": oid(n), "weight": "strong"} for n in TAGS["ramp"]], ("tag_id", "oracle_id"))
        cs.sync_oracle_prices(db, [{"oracle_id": oid(n), "scryfall_id": f"p{n}", "usd": usd, "usd_foil": None, "eur": None,
                                    "day": prov_day(), "source": "scryfall"} for n, usd in PRICES.items()])
        cs.record_source(db, "oracle_prices", version="oracle_prices-1", rows=1)
        db.commit()
    return signed_in


def prov_day():
    from datetime import date
    return date(2026, 10, 4)


def test_every_tool_is_named_in_llms_txt():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "public" / "llms.txt").read_text(encoding="utf-8")
    assert [t.name for t in mcp.TOOLS if f"`{t.name}`" not in text] == [], "add new tools to public/llms.txt"
    assert all(f"`{p['name']}`" in text for p in PROMPTS)


def test_every_tool_is_classified_for_provenance():
    own = mcp.OWN_DATA_ONLY
    unclassified = [t.name for t in mcp.TOOLS if not t.provenance and t.name not in own]
    assert unclassified == [], "classify new tools in vault/api/mcp.py (SCRYFALL_DATA or OWN_DATA_ONLY) or give them provenance="
    assert not [t.name for t in mcp.TOOLS if t.provenance and t.name in own]


def test_the_catalog_tools_are_listed_and_read_only(agent, bot):
    read = make_token(agent)
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}
    for name in ("whoami", "get_card_oracle", "get_rulings", "search_rules", "get_rule", "verify_citation", "deck_stats",
                 "deck_legality", "find_upgrades", "validate_deck_changes", "shopping_list"):
        assert name in tools and tools[name]["annotations"]["readOnlyHint"] is True, name


SAMPLE_ARGS = {
    "whoami": {}, "get_card_oracle": {"name": "Lightning Bolt"}, "get_rulings": {"oracle_id": BOLT},
    "search_rules": {"query": "sample rule"}, "get_rule": {"number": "100.1"},
    "verify_citation": {"kind": "rule", "ref": "100.1", "quote": "First sample rule."},
    "deck_stats": {"text": VALID}, "deck_legality": {"text": VALID, "format": "commander"},
    "find_upgrades": {"text": VALID, "format": "commander", "budget_usd": 5, "roles": ["ramp"]},
    "validate_deck_changes": {"text": VALID, "format": "commander", "adds": ["Cheap Ramp"], "cuts": ["Dull Bear"], "budget_usd": 1},
    "shopping_list": {"text": "1 Test Rock\n1 Cheap Ramp"},
}


@pytest.mark.parametrize("tool", sorted(SAMPLE_ARGS))
def test_each_new_tool_answers_with_provenance_even_to_a_read_only_token(agent, bot, tool):
    read = make_token(agent)
    result = call_tool(bot, read, tool, **SAMPLE_ARGS[tool])
    assert result["isError"] is False, result["content"][0]["text"]
    blocks = result["structuredContent"]["provenance"]
    assert blocks and all(b["kind"] in ("source", "computed") and b["source"] for b in blocks)


def test_tools_that_return_scryfall_data_get_a_provenance_block(agent, bot):
    read = make_token(agent)
    page = call_tool(bot, read, "search_cards", limit=1)["structuredContent"]
    block = page["provenance"][0]
    assert block["source"] == "Scryfall" and block["kind"] == "source" and block["notice"] == prov.FAN_CONTENT_NOTICE
    assert "TCGplayer" in block["origin"] and block["url"] == "https://scryfall.com"
    own = call_tool(bot, read, "list_decks")["structuredContent"]
    assert "provenance" not in own  # only the person's own data: nothing to attribute


def test_archidekt_decks_are_attributed_to_their_author_and_linked():
    blocks = provenance_blocks(("archidekt",), {"id": 42, "owner": {"username": "ann"}})
    assert blocks == [{"kind": "source", "source": "Archidekt", "origin": "deck by ann", "url": "https://archidekt.com/decks/42"}]


def test_whoami_reports_the_scopes_and_the_data_versions(agent, bot):
    read = make_token(agent)
    body = call_tool(bot, read, "whoami")["structuredContent"]
    assert body["auth"] == "bearer" and body["scopes"] == ["read"]
    assert body["data"]["rules_version"] == "2027-03-03" and body["data"]["sources"]["oracle_prices"]["version"] == "oracle_prices-1"
    assert "not the Vault's" in body["guidance"]


def test_a_misspelled_card_gets_suggestions_through_mcp_too(agent, bot):
    read = make_token(agent)
    body = call_tool(bot, read, "get_card_oracle", name="Lightnin Bolt")["structuredContent"]
    assert body["card"] is None and body["suggestions"] == ["Lightning Bolt"]


def test_the_grounding_rules_reach_the_agent_in_the_instructions_and_prompts(agent, bot):
    read = make_token(agent)
    init = rpc(bot, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}, read).json()["result"]
    assert GROUNDING in init["instructions"] and "Never answer a rules or card question from memory" in init["instructions"]
    assert init["capabilities"]["prompts"] == {"listChanged": False}
    listed = rpc(bot, "prompts/list", token=read).json()["result"]["prompts"]
    assert {p["name"] for p in listed} == {p["name"] for p in PROMPTS} and "text" not in listed[0]
    got = rpc(bot, "prompts/get", {"name": "rules_judge", "arguments": {"question": "Does Bolt kill a 3/3?"}}, read).json()["result"]
    text = got["messages"][0]["content"]["text"]
    assert "Does Bolt kill a 3/3?" in text and "verify_citation" in text and "Fan Content" in text
    assert rpc(bot, "prompts/get", {"name": "rules_judge", "arguments": {}}, read).json()["error"]["code"] == -32602
    assert rpc(bot, "prompts/get", {"name": "nope"}, read).json()["error"]["code"] == -32602
