"""The MCP tools for buckets (#123): list_buckets, create_bucket, rename_bucket, delete_bucket, and the `bucket` argument of
search_cards. Write tools need the write scope; delete asks first; one person's buckets are invisible to another."""

from tests.test_agents import V1, agent, bot, call_tool, make_token, rpc  # noqa: F401 - fixtures
from vault.api import mcp

TWO = (
    "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET\n"
    "Binder,3,0,Sol Ring,C21,Commander 2021,263,Mint,Normal,English,1.00,2024-02-17,1.00,2.00,2.30\n"
    "Trade box,4,0,Mountain,C17,Commander 2017,304,Mint,Normal,English,0.05,2024-02-17,0.05,0.05,0.05\n"
).encode()


def test_the_tools_are_listed_classified_and_the_write_ones_are_marked(agent, bot):
    names = {"list_buckets", "create_bucket", "rename_bucket", "delete_bucket"}
    assert names <= set(mcp.BY_NAME) and names <= mcp.OWN_DATA_ONLY
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=make_token(agent, scopes=["read", "write"])).json()["result"]["tools"]}
    assert names <= set(tools)
    assert tools["list_buckets"]["annotations"]["readOnlyHint"] is True
    for name in ("create_bucket", "rename_bucket"):
        assert tools[name]["annotations"]["readOnlyHint"] is False and tools[name]["annotations"]["destructiveHint"] is False
    assert tools["delete_bucket"]["annotations"]["destructiveHint"] is True
    assert "bucket" in tools["search_cards"]["inputSchema"]["properties"]
    assert "list_buckets" in " ".join(tools["search_cards"]["inputSchema"]["properties"]["bucket"]["description"].split())


def test_an_assistant_lists_makes_renames_and_deletes_buckets_and_searches_one(agent, bot):
    agent.post(f"{V1}/imports", files={"file": ("two.csv", TWO, "text/csv")})
    write = make_token(agent, scopes=["read", "write"])
    listed = call_tool(bot, write, "list_buckets")["structuredContent"]
    by_name = {b["name"]: b for b in listed["items"]}
    assert by_name["Binder"]["copies"] == 3 and by_name["Trade box"]["copies"] == 4
    trade = call_tool(bot, write, "search_cards", bucket=by_name["Trade box"]["id"])["structuredContent"]
    assert [(c["name"], c["quantity"]) for c in trade["items"]] == [("Mountain", 4)]  # "what is in my trade binder?"
    made = call_tool(bot, write, "create_bucket", name="Deck box")["structuredContent"]
    assert made["name"] == "Deck box" and made["kind"] == "made" and made["copies"] == 0
    assert call_tool(bot, write, "create_bucket", name="deck BOX").get("isError")  # names ignore case
    renamed = call_tool(bot, write, "rename_bucket", bucket_id=made["id"], name="Deck boxes")["structuredContent"]
    assert renamed["name"] == "Deck boxes"
    held = call_tool(bot, write, "delete_bucket", bucket_id=by_name["Binder"]["id"], confirm=True)
    assert held.get("isError") and "move them to another bucket first" in str(held["content"])
    preview = call_tool(bot, write, "delete_bucket", bucket_id=made["id"])["structuredContent"]  # no confirm: shows it, changes nothing
    assert preview["name"] == "Deck boxes" and agent.get(f"{V1}/collection/buckets/{made['id']}").status_code == 200
    done = call_tool(bot, write, "delete_bucket", bucket_id=made["id"], confirm=True)["structuredContent"]
    assert done["deleted"] is True and agent.get(f"{V1}/collection/buckets/{made['id']}").status_code == 404


def test_a_read_only_connection_can_list_but_not_change_buckets(agent, bot):
    made = agent.post(f"{V1}/collection/buckets", json={"name": "Mine"}).json()
    read = make_token(agent)
    assert not call_tool(bot, read, "list_buckets").get("isError")
    assert call_tool(bot, read, "create_bucket", name="No").get("isError")
    assert call_tool(bot, read, "rename_bucket", bucket_id=made["id"], name="No").get("isError")
    assert call_tool(bot, read, "delete_bucket", bucket_id=made["id"], confirm=True).get("isError")
    assert agent.get(f"{V1}/collection/buckets/{made['id']}").json()["name"] == "Mine"


def test_another_persons_bucket_is_not_found_through_the_tools(agent, bot):
    agent.cookies.clear()
    agent.post("/api/auth/dev-login", params={"email": "bob@example.com"})
    bobs = agent.post(f"{V1}/collection/buckets", json={"name": "Bob's box"}).json()
    agent.cookies.clear()
    agent.post("/api/auth/dev-login", params={"email": "alice@example.com"})
    write = make_token(agent, scopes=["read", "write"])
    assert "Bob's box" not in str(call_tool(bot, write, "list_buckets")["structuredContent"])
    for name, args in (("rename_bucket", {"name": "Mine now"}), ("delete_bucket", {"confirm": True}), ("delete_bucket", {})):
        assert call_tool(bot, write, name, bucket_id=bobs["id"], **args).get("isError"), name
    assert call_tool(bot, write, "search_cards", bucket=bobs["id"]).get("isError")
