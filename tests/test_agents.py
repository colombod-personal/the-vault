"""Agents: personal access tokens with scopes, idempotent retries, and the MCP server."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vault.models import AccessToken

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
V1 = "/api/v1"


def make_token(client, scopes=("read",), **extra):
    res = client.post(f"{V1}/me/tokens", json={"name": "my agent", "scopes": list(scopes), **extra})
    assert res.status_code == 201, res.text
    return res.json()


def auth(token):
    return {"Authorization": f"Bearer {token['token']}"}


@pytest.fixture
def bot(app):
    """An agent's HTTP client: no browser cookies, only the token it is given."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def agent(signed_in):
    """A second client with no cookies: only the token."""
    signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")})
    return signed_in


def test_tokens_are_shown_once_and_listed_without_the_secret(signed_in):
    token = make_token(signed_in)
    assert token["token"].startswith("vault_pat_") and token["scopes"] == ["read"]
    assert token["mcp_url"].endswith("/api/mcp")
    listed = signed_in.get(f"{V1}/me/tokens").json()["items"]
    assert listed[0]["prefix"] == token["token"][:14] and "token" not in listed[0]


def test_read_token_reads_but_cannot_write(agent, bot):
    token = make_token(agent)
    h = auth(token)
    assert bot.get(f"{V1}/collection", headers=h).json()["copies"] == 7
    assert bot.post(f"{V1}/decks/coverage", json={"text": "1 Sol Ring"}, headers=h).status_code == 200
    res = bot.post(f"{V1}/decks", json={"name": "x", "text": "1 Sol Ring"}, headers=h)
    assert res.status_code == 403 and "insufficient_scope" in res.headers["www-authenticate"]


def test_write_token_writes_but_cannot_manage_the_account(agent, bot):
    token = make_token(agent, scopes=["read", "write"])
    h = auth(token)
    assert bot.post(f"{V1}/decks", json={"name": "Ramp", "text": "1 Sol Ring"}, headers=h).status_code == 201
    assert bot.post(f"{V1}/me/tokens", json={"name": "more"}, headers=h).status_code == 403  # no escalation
    assert bot.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}, headers=h).status_code == 403
    assert bot.get(f"{V1}/me/export", headers=h).status_code == 403


def test_expired_revoked_and_deleted_tokens_stop_working(app, agent, bot):
    token = make_token(agent)
    with app.state.db.sessions() as db:
        row = db.get(AccessToken, token["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert bot.get(f"{V1}/me", headers=auth(token)).status_code == 401

    second = make_token(agent)
    assert agent.delete(f"{V1}/me/tokens/{second['id']}").json() == {"deleted": True}
    assert bot.get(f"{V1}/me", headers=auth(second)).status_code == 401

    third = make_token(agent)
    assert bot.post(f"{V1}/auth/revoke", headers=auth(third)).json() == {"revoked": True}
    assert bot.get(f"{V1}/me", headers=auth(third)).status_code == 401


def test_tokens_are_erased_with_the_account(signed_in):
    make_token(signed_in)
    removed = signed_in.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}).json()["removed"]
    assert removed["access_tokens"] == 1


# -- retries ---------------------------------------------------------------------------------

def test_idempotency_key_makes_posts_safe_to_retry(signed_in):
    key = {"Idempotency-Key": "4f1c2b8e-deck-1"}
    first = signed_in.post(f"{V1}/decks", json={"name": "Ramp", "text": "1 Sol Ring"}, headers=key)
    again = signed_in.post(f"{V1}/decks", json={"name": "Ramp", "text": "1 Sol Ring"}, headers=key)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()
    assert again.headers["idempotent-replayed"] == "true"
    assert signed_in.get(f"{V1}/decks").json()["total"] == 1
    other = signed_in.post(f"{V1}/shares", json={"kind": "collection"}, headers=key)
    assert other.status_code == 422  # same key, different request

    imp = {"Idempotency-Key": "import-1"}
    a = signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")}, headers=imp).json()
    b = signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")}, headers=imp).json()
    assert a == b and signed_in.get(f"{V1}/imports").json()["total"] == 1


def test_summary_version_changes_with_the_data(signed_in):
    signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")})
    v1 = signed_in.get(f"{V1}/collection").json()["version"]
    assert signed_in.get(f"{V1}/collection").json()["version"] == v1
    signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV.replace(b",3,0,", b",5,0,"), "text/csv")})
    assert signed_in.get(f"{V1}/collection").json()["version"] != v1


# -- MCP ---------------------------------------------------------------------------------------

def rpc(client, method, params=None, token=None, id=1):
    body = {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}
    return client.post("/api/mcp", json=body, headers=auth(token) if token else {})


def call_tool(client, token, tool, **arguments):
    return rpc(client, "tools/call", {"name": tool, "arguments": arguments}, token).json()["result"]


def test_mcp_needs_a_token(client):
    res = rpc(client, "initialize")
    assert res.status_code == 401 and "Bearer" in res.headers["www-authenticate"]
    assert client.get("/api/mcp").status_code == 405


def test_mcp_handshake_and_tools(agent, bot):
    read = make_token(agent)
    init = rpc(bot, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}}, read).json()["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "the-vault"
    assert "search_cards" in init["instructions"]
    note = bot.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=auth(read))
    assert note.status_code == 202
    names = {t["name"] for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}
    assert {"search_cards", "check_decklist", "get_card"} <= names and "save_deck" not in names  # read-only token


def test_mcp_tools_page_and_answer(agent, bot):
    read = make_token(agent)
    summary = call_tool(bot, read, "get_collection_summary")["structuredContent"]
    assert summary["copies"] == 7
    first = call_tool(bot, read, "search_cards", sort="-value", limit=2)["structuredContent"]
    assert first["count"] == 2 and first["total"] == 4 and first["next_cursor"]
    rest = call_tool(bot, read, "search_cards", sort="-value", limit=2, cursor=first["next_cursor"])["structuredContent"]
    assert {c["name"] for c in first["items"] + rest["items"]} == {"Sol Ring", "A Killer Among Us", "Accursed Marauder", "Belfry Spirit"}
    card = call_tool(bot, read, "get_card", card_id=first["items"][0]["id"])["structuredContent"]
    assert card["copies"]
    coverage = call_tool(bot, read, "check_decklist", text="1 Sol Ring\n1 Rhystic Study")["structuredContent"]
    assert {c["name"]: c["status"] for c in coverage["cards"]} == {"Sol Ring": "owned", "Rhystic Study": "missing"}

    denied = call_tool(bot, read, "save_deck", name="x", text="1 Sol Ring")
    assert denied["isError"] is True and "read-only" in denied["content"][0]["text"]
    bad = rpc(bot, "tools/call", {"name": "search_cards", "arguments": {"nope": 1}}, read).json()
    assert bad["error"]["code"] == -32602

    write = make_token(agent, scopes=["read", "write"])
    saved = call_tool(bot, write, "save_deck", name="Ramp", text="1 Sol Ring")
    assert saved["isError"] is False and saved["structuredContent"]["name"] == "Ramp"


def test_discovery_for_agents(client):
    links = client.get(V1).json()["_links"]
    assert links["mcp"]["href"] == "/api/mcp" and links["llms"]["href"] == "/llms.txt"
