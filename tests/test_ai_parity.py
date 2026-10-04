"""Web-app actions an assistant can also do (docs/ai-parity.md, #101): one import's changes, deleting a deck and
ending a share, both only after a confirm, and accepting an invite. Creating a share stays with the person."""

from fastapi.testclient import TestClient

from test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures


def tools():
    from vault.api import mcp
    return {name: tool.schema() for name, tool in mcp.BY_NAME.items()}


def test_one_imports_changes(agent, bot):
    read = make_token(agent)
    imp = call_tool(bot, read, "list_imports")["structuredContent"]["items"][0]
    one = call_tool(bot, read, "get_import", import_id=imp["id"])["structuredContent"]
    assert one["id"] == imp["id"]


def test_deleting_a_deck_needs_a_confirm(agent, bot):
    deck = agent.post(f"{V1}/decks", json={"name": "Ramp", "text": "1 Sol Ring"}).json()
    write = make_token(agent, scopes=["read", "write"])
    preview = call_tool(bot, write, "delete_deck", deck_id=deck["id"])
    assert not preview.get("isError") and preview["structuredContent"]["name"] == "Ramp"
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 200  # still there
    call_tool(bot, write, "delete_deck", deck_id=deck["id"], confirm=True)
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 404


def test_a_read_only_connection_cannot_delete(agent, bot):
    deck = agent.post(f"{V1}/decks", json={"name": "Ramp", "text": "1 Sol Ring"}).json()
    read = make_token(agent)
    assert call_tool(bot, read, "delete_deck", deck_id=deck["id"], confirm=True).get("isError")
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 200


def test_accept_list_and_stop_a_share(agent, bot):
    invite = agent.post(f"{V1}/shares", json={"kind": "collection"}).json()
    with TestClient(agent.app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        bob_write = make_token(bob, scopes=["read", "write"])
        accepted = call_tool(bot, bob_write, "accept_share", invite_token=invite["url"].split("invite=")[1])
        assert not accepted.get("isError")
        assert call_tool(bot, bob_write, "list_shared_with_me")["structuredContent"]["items"]
    owner = make_token(agent, scopes=["read", "write"])
    mine = call_tool(bot, owner, "list_my_shares")["structuredContent"]["items"]
    assert mine[0]["status"] == "active"
    call_tool(bot, owner, "stop_sharing", share_id=mine[0]["id"])  # preview only
    assert agent.get(f"{V1}/shares").json()["total"] == 1
    call_tool(bot, owner, "stop_sharing", share_id=mine[0]["id"], confirm=True)
    assert agent.get(f"{V1}/shares").json()["total"] == 0


def test_hosts_are_told_which_tools_destroy_and_no_tool_creates_a_share():
    listed = tools()
    assert listed["delete_deck"]["annotations"]["destructiveHint"] is True
    assert listed["stop_sharing"]["annotations"]["destructiveHint"] is True
    assert listed["get_import"]["annotations"]["readOnlyHint"] is True
    assert not [n for n in listed if "create" in n and "share" in n]  # sharing is started by the person


def test_an_import_shows_what_would_change_before_replacing_the_collection(agent, bot):
    from test_agents import CSV
    write = make_token(agent, scopes=["read", "write"])
    smaller = b"\n".join(CSV.splitlines()[:3]) + b"\n"  # sep line, header, one row: most cards would go
    before = agent.get(f"{V1}/collection").json()["copies"]
    preview = call_tool(bot, write, "import_collection_csv", csv=smaller.decode())
    assert not preview.get("isError"), preview
    assert "changes" in preview["structuredContent"] and preview["structuredContent"]["rows"] == 1
    assert agent.get(f"{V1}/collection").json()["copies"] == before  # nothing changed yet
    done = call_tool(bot, write, "import_collection_csv", csv=smaller.decode(), confirm=True)
    assert not done.get("isError"), done
    assert agent.get(f"{V1}/collection").json()["copies"] < before


def test_the_preview_route_never_writes(agent):
    from test_agents import CSV
    imports = agent.get(f"{V1}/imports").json()["total"]
    res = agent.post(f"{V1}/imports/preview", files={"file": ("c.csv", CSV, "text/csv")})
    assert res.status_code == 200 and agent.get(f"{V1}/imports").json()["total"] == imports


def test_the_preview_lists_rows_it_cannot_match_so_the_file_can_be_fixed_first(agent, bot):
    from test_agents import CSV
    lines = CSV.decode().splitlines()
    bogus = "my cards,1,0,Not A Real Card,ZZZ,Nowhere,9999,Mint,Normal,English,0.10,2024-01-01,0.01,0.20,0.09"
    file = "\n".join(lines + [bogus]) + "\n"
    write = make_token(agent, scopes=["read", "write"])
    preview = call_tool(bot, write, "import_collection_csv", csv=file)["structuredContent"]
    assert preview["matched_rows"] + preview["unmatched_rows"] == preview["rows"]
    assert {"row": preview["rows"], "name": "Not A Real Card", "set": "zzz", "number": "9999", "quantity": 1} in preview["unmatched"]
    assert "Fix the set code and collector number" in preview["note"]
