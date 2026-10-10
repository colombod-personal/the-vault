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
    for name in ("whoami", "get_card_oracle", "get_rulings", "search_rules", "get_rule", "verify_citation", "present_steps", "deck_stats",
                 "deck_legality", "find_upgrades", "validate_deck_changes", "shopping_list"):
        assert name in tools and tools[name]["annotations"]["readOnlyHint"] is True, name


SAMPLE_ARGS = {
    "whoami": {}, "get_card_oracle": {"name": "Lightning Bolt"}, "get_rulings": {"oracle_id": BOLT},
    "search_rules": {"query": "sample rule"}, "get_rule": {"number": "100.1"}, "rules_outline": {"under": "1"},
    "find_rules_term": {"name": "sample term"}, "rules_changes": {"limit": 5},
    "verify_citation": {"kind": "rule", "ref": "100.1", "quote": "First sample rule."},
    "present_steps": {"steps": [{"text": "A thing happens.", "rules": ["100.1", "999.9"]}]},
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


def test_find_upgrades_can_use_the_persons_collection(agent, bot):
    read = make_token(agent)
    args = {**SAMPLE_ARGS["find_upgrades"], "use_collection": True}
    result = call_tool(bot, read, "find_upgrades", **args)
    assert result["isError"] is False, result["content"][0]["text"]
    ramp = result["structuredContent"]["result"]["candidates"]["ramp"]
    assert ramp and all("owned_copies" in c for c in ramp)  # asked for: each says how many the person owns


def test_tools_that_return_scryfall_data_get_a_provenance_block(agent, bot):
    read = make_token(agent)
    page = call_tool(bot, read, "search_cards", limit=1)["structuredContent"]
    block = page["provenance"][0]
    assert block["source"] == "Scryfall" and block["kind"] == "source" and block["notice"] == prov.FAN_CONTENT_NOTICE
    assert "TCGplayer" in block["origin"] and block["url"] == "https://scryfall.com"
    own = call_tool(bot, read, "list_imports")["structuredContent"]
    assert "provenance" not in own  # only the person's own data: nothing to attribute
    decks = call_tool(bot, read, "list_decks")["structuredContent"]  # carries the commanders' colour identity (#216)
    assert {b["source"] for b in decks["provenance"]} == {"Scryfall"}


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


def test_hosts_without_the_skills_still_get_the_shop_and_deck_rules():
    """claude.ai loads tools and instructions, not skills: a real run (2026-10-04, #82) claimed a cheapest shop,
    'current' prices and a cart, and did not credit Archidekt. The rules must be in what every host reads."""
    from vault.api import mcp
    assert "Never say which shop is cheapest" in GROUNDING and "never say anything goes into a cart" in GROUNDING
    # #304: a real run quoted rule 702.19 "word for word" with no get_rule call: the tools say they are the only source for a rule's wording
    from vault.api import mcp
    for name in ("get_rule", "find_rules_term", "search_rules"):
        assert "from memory" in " ".join(mcp.BY_NAME[name].description.split()), name
    assert "the only source for a rule's wording" in " ".join(mcp.BY_NAME["get_rule"].description.split())
    assert "never answer it from memory" in " ".join(mcp.BY_NAME["get_card_oracle"].description.split())
    # #295: an assistant with browser tools opened a shop's Mass Entry to fill a cart after "buy my missing cards"
    flat = " ".join(GROUNDING.split())
    assert "Never place or fill an order for the person" in flat and "never use a browser or any other tool to open a shop, fill its cart" in flat
    assert "find it with list_decks" in GROUNDING and "credit Archidekt" in GROUNDING
    assert "never say which store is cheapest" in mcp.BY_NAME["shopping_list"].description
    assert "credit Archidekt" in mcp.BY_NAME["get_archidekt_deck"].description


def test_the_instructions_say_the_vault_is_not_endorsed_and_consent_names_owned_card_edits():
    """#62 (audit): the instructions had no 'not endorsed' line, and #81: consent never told a person that granting write
    lets the assistant change which cards they own."""
    from vault.oauth_routes import SCOPE_TEXT
    assert "not approved or endorsed by Wizards of the Coast" in GROUNDING and "never speak for any of them" in GROUNDING
    assert "change which cards you own" in SCOPE_TEXT["write"]


def test_every_tool_has_a_title_and_a_read_only_or_destructive_annotation_as_the_claude_directory_requires():
    """#241: Anthropic's submission needs a title and readOnlyHint or destructiveHint on every tool."""
    from vault.api import mcp
    assert len(mcp.TOOLS) >= 57
    for tool in mcp.TOOLS:
        entry = tool.schema()
        assert entry["title"].strip() and entry["description"].strip(), tool.name
        hints = entry["annotations"]
        assert isinstance(hints["readOnlyHint"], bool) and isinstance(hints["destructiveHint"], bool), tool.name
        assert hints["title"] == entry["title"], tool.name  # the Claude portal reads annotations.title
        if tool.write:
            assert hints["readOnlyHint"] is False, f"{tool.name} writes but says it is read only"
        else:
            assert hints["readOnlyHint"] is True and hints["destructiveHint"] is False, tool.name


def test_verify_citation_refuses_an_edition_it_cannot_read_and_says_so_in_its_description(agent, bot):
    """#25: `version` is used, not ignored: the current edition is checked, any other is an error naming the current one."""
    read = make_token(agent)
    tool = {t["name"]: t for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}["verify_citation"]
    said = tool["inputSchema"]["properties"]["version"]["description"]
    assert "current edition" in said and "no archive" in said
    ok = call_tool(bot, read, "verify_citation", **SAMPLE_ARGS["verify_citation"], version="2027-03-03")
    assert ok["isError"] is False and ok["structuredContent"]["verified"] is True
    refused = call_tool(bot, read, "verify_citation", **SAMPLE_ARGS["verify_citation"], version="2026-06-19")
    assert refused["isError"] is True
    text = refused["content"][0]["text"]
    assert "2026-06-19" in text and "2027-03-03" in text, text


def test_get_deck_and_shopping_list_say_which_price_basis_a_card_has():  # #328
    by_name = {t.name: t.description for t in mcp.TOOLS}
    assert "cheapest priced paper printing" in by_name["get_deck"] and "`shopping_list`" in by_name["get_deck"]
    assert "cheapest printing" in by_name["shopping_list"]


def test_the_listings_doc_and_the_connect_page_state_that_grounding_needs_the_assistant_to_call_the_vault():  # #319
    from pathlib import Path
    root = Path(__file__).parent.parent
    listings = " ".join((root / "docs" / "listings.md").read_text(encoding="utf-8").split())
    assert "What a listing cannot promise" in listings and "from the assistant's own memory" in listings and "USE_THE_VAULT" in listings
    connect = " ".join((root / "public" / "connect.html").read_text(encoding="utf-8").split())
    assert "Make your assistant use it" in connect and "answered from the assistant's own memory" in connect


def test_the_claude_ai_check_for_the_shop_and_deck_rules_is_prepared_and_not_claimed():
    """#82: the real claude.ai run needs a signed-in person. The doc holds the exact script, the pass rules (tied to the
    claims the 2026-10-04 run got wrong) and an empty record; it must not claim a result nobody recorded."""
    import re
    from pathlib import Path
    text = (Path(__file__).parent.parent / "docs" / "ai-integration-testing.md").read_text(encoding="utf-8")
    section = text.split("## claude.ai check: the shop and deck rules with Archidekt deck 6803907", 1)[1]
    assert "6803907" in section and "read scope" in section.lower() and "Write unticked" in section
    for must in ("cheapest", "cart", "current", "credited", "list_decks", "get_deck", "shopping_list", "get_archidekt_deck"):
        assert must in section, must
    status = section.split("**Status: ", 1)[1].split("**", 1)[0]
    if status.startswith("NOT RUN"):
        assert "| Result | NOT RUN |" in section
    else:  # a recorded result needs a date and evidence
        assert re.search(r"20\d\d-\d\d-\d\d", status) and "| Capture (link or file in the issue) |  |" not in section


def test_the_archidekt_provenance_links_the_real_deck_not_the_vaults_id_for_it():
    """#321: refresh_deck and import_deck_from_link carry the Vault's saved deck as `deck`; its id is not Archidekt's."""
    from vault.api import mcp_catalog

    refresh = {"deck": {"id": 9, "source_url": "https://archidekt.com/decks/6803907", "source_author": "layer0"},
               "source": {"url": "https://archidekt.com/decks/6803907", "author": "layer0"}}
    (block,) = mcp_catalog.provenance_blocks(("archidekt",), refresh)
    assert block["url"] == "https://archidekt.com/decks/6803907" and block["origin"] == "deck by layer0"
    imported = {"deck": {"id": 9, "credit": {"url": "https://archidekt.com/decks/6803907", "author": "layer0"}}}
    assert mcp_catalog.provenance_blocks(("archidekt",), imported)[0]["url"] == "https://archidekt.com/decks/6803907"
    read = {"id": 6803907, "owner": {"username": "layer0"}}  # get_archidekt_deck: the id is Archidekt's own
    block = mcp_catalog.provenance_blocks(("archidekt",), read)[0]
    assert block["url"] == "https://archidekt.com/decks/6803907" and block["origin"] == "deck by layer0"
    none = mcp_catalog.provenance_blocks(("archidekt",), {"deck": {"id": 9}})[0]  # nothing says where: the site, never a made-up deck
    assert none["url"] == "https://archidekt.com"
