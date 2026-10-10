"""The MCP tools for tags (#127): list_tags, tag_cards, untag_cards, rename_tag, delete_tag, and the `tag` argument of search_cards.
Write tools need the write scope; a big change and a delete ask first; an assistant's tags carry its app's name."""

import pytest

from tests.ids import sid
from tests.test_agents import V1, bot, call_tool, make_token, rpc  # noqa: F401 - fixtures
from tests.test_tags_api import CSV, MOUNTAIN, SOL
from vault import tags as T
from vault.api import mcp
from vault.models import Card

NAMES = {"list_tags", "tag_cards", "untag_cards", "rename_tag", "delete_tag"}


@pytest.fixture
def stocked(app, signed_in):
    with app.state.db.sessions() as db:
        db.add_all([Card(scryfall_id=sid("sol-c21"), oracle_id=SOL, name="Sol Ring", set_code="c21", collector_number="263"),
                    Card(scryfall_id=sid("sol-cmm"), oracle_id=SOL, name="Sol Ring", set_code="cmm", collector_number="400"),
                    Card(scryfall_id=sid("mountain"), oracle_id=MOUNTAIN, name="Mountain", set_code="c17", collector_number="304")])
        db.commit()
    signed_in.post(f"{V1}/imports", files={"file": ("c.csv", CSV, "text/csv")})
    return signed_in


def ids_of(bot, token):
    items = call_tool(bot, token, "search_cards")["structuredContent"]["items"]
    out = {}
    for c in items:
        out.setdefault(c["name"], []).append(c["id"])
    return out


def test_the_tools_are_listed_classified_and_the_write_ones_are_marked(stocked, bot):
    assert NAMES <= set(mcp.BY_NAME) and NAMES <= mcp.OWN_DATA_ONLY
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=make_token(stocked, scopes=["read", "write"])).json()["result"]["tools"]}
    assert NAMES <= set(tools)
    assert tools["list_tags"]["annotations"]["readOnlyHint"] is True
    for name in ("tag_cards", "rename_tag"):
        assert tools[name]["annotations"]["readOnlyHint"] is False and tools[name]["annotations"]["destructiveHint"] is False
    for name in ("untag_cards", "delete_tag"):
        assert tools[name]["annotations"]["destructiveHint"] is True
    assert "tag" in tools["search_cards"]["inputSchema"]["properties"]
    assert "recorded as written by this app, not by the person" in " ".join(tools["tag_cards"]["description"].split())
    # #241: the order not to present them as the person's own is in the instructions, not in the description
    assert "never present them as the person's own" in " ".join(mcp.INSTRUCTIONS.split())


def test_an_assistant_tags_cards_lists_and_searches_them_and_its_app_is_named(stocked, bot):
    token = make_token(stocked, scopes=["read", "write"], name="Claude helper")
    ids = ids_of(bot, token)
    done = call_tool(bot, token, "tag_cards", tag="trade", card_ids=ids["Sol Ring"][:1] + ids["Mountain"])["structuredContent"]
    assert done["applied"] is True and done["added"] == 2 and done["written_by"] == "assistant"
    listed = call_tool(bot, token, "list_tags")["structuredContent"]
    assert [(t["tag"], t["cards"], t["by_source"]["assistant"]) for t in listed["items"]] == [("trade", 2, 2)]
    found = call_tool(bot, token, "search_cards", tag="trade")["structuredContent"]
    assert {c["name"] for c in found["items"]} == {"Sol Ring", "Mountain"} and found["total"] == 3  # both Sol Ring printings
    assert all(c["tags"] == ["trade"] for c in found["items"])
    assert call_tool(bot, token, "list_tags", query="zzz")["structuredContent"]["total"] == 0
    refused = rpc(bot, "tools/call", {"name": "tag_cards", "arguments": {"tag": "Not A Tag", "card_ids": ids["Mountain"]}}, token).json()
    assert "error" in refused and "tag" in refused["error"]["message"]  # refused by the schema before anything is sent
    assert call_tool(bot, token, "tag_cards", tag="x", card_ids=["0123456789abcdef"]).get("isError")  # not in the collection


def test_untag_rename_and_delete_with_the_confirmation_the_big_ones_need(stocked, bot, monkeypatch):
    token = make_token(stocked, scopes=["read", "write"])
    ids = ids_of(bot, token)
    both = ids["Sol Ring"][:1] + ids["Mountain"]
    call_tool(bot, token, "tag_cards", tag="old", card_ids=both)
    renamed = call_tool(bot, token, "rename_tag", tag="old", name="newer")["structuredContent"]
    assert renamed["tag"] == "newer" and renamed["cards"] == 2
    monkeypatch.setattr(T, "CONFIRM_ABOVE", 1)
    shown = call_tool(bot, token, "untag_cards", tag="newer", card_ids=both)["structuredContent"]
    assert shown["applied"] is False and stocked.get(f"{V1}/collection/tags/newer").json()["cards"] == 2
    removed = call_tool(bot, token, "untag_cards", tag="newer", card_ids=ids["Mountain"])["structuredContent"]
    assert removed["applied"] is True and removed["removed"] == 1
    preview = call_tool(bot, token, "delete_tag", tag="newer")["structuredContent"]  # no confirm: shows it, changes nothing
    assert preview["tag"] == "newer" and preview["cards"] == 1 and stocked.get(f"{V1}/collection/tags/newer").status_code == 200
    gone = call_tool(bot, token, "delete_tag", tag="newer", confirm=True)["structuredContent"]
    assert gone["deleted"] is True and stocked.get(f"{V1}/collection/tags/newer").status_code == 404


def test_a_read_only_connection_can_list_but_not_change_tags(stocked, bot):
    read = make_token(stocked)
    ids = ids_of(bot, read)
    assert call_tool(bot, read, "list_tags")["structuredContent"]["total"] == 0
    for tool, args in (("tag_cards", {"tag": "x", "card_ids": ids["Mountain"]}), ("untag_cards", {"tag": "x", "card_ids": ids["Mountain"]}),
                       ("rename_tag", {"tag": "x", "name": "y"}), ("delete_tag", {"tag": "x", "confirm": True})):
        assert call_tool(bot, read, tool, **args).get("isError"), tool
