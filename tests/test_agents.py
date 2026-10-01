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


def test_tokens_always_read_and_cannot_edit_the_profile(app, agent, bot):
    assert agent.post(f"{V1}/me/tokens", json={"name": "w", "scopes": ["write"]}).status_code == 422
    from vault import tokens
    from vault.models import User

    with app.state.db.sessions() as db:  # a write-only token made some other way still can't read
        _, secret = tokens.create_pat(db, db.query(User).one(), "w", ["write"], 30)
    assert bot.get(f"{V1}/collection", headers={"Authorization": f"Bearer {secret}"}).status_code == 403
    h = auth(make_token(agent, scopes=["read", "write"]))
    assert bot.patch(f"{V1}/me", json={"name": "Mallory"}, headers=h).status_code == 403


def test_a_retried_invite_gets_a_fresh_link_and_none_is_stored(app, signed_in):
    from vault.models import IdempotentRequest

    key = {"Idempotency-Key": "invite-1"}
    first = signed_in.post(f"{V1}/shares", json={"kind": "collection"}, headers=key).json()
    again = signed_in.post(f"{V1}/shares", json={"kind": "collection"}, headers=key)
    assert again.status_code == 201 and again.headers["idempotent-replayed"] == "true"
    assert again.json()["id"] == first["id"] and again.json()["url"] != first["url"]
    assert signed_in.get(f"{V1}/shares").json()["total"] == 1
    with app.state.db.sessions() as db:
        assert "invite=" not in str(db.query(IdempotentRequest).one().body)
    with TestClient(signed_in.app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        old = first["url"].split("invite=")[1]
        assert bob.post(f"{V1}/shares/accept", json={"token": old}).status_code == 404  # replaced
        new = again.json()["url"].split("invite=")[1]
        assert bob.post(f"{V1}/shares/accept", json={"token": new}).status_code == 200
    assert signed_in.post(f"{V1}/shares", json={"kind": "collection"}, headers=key).status_code == 409  # accepted


def test_idempotency_key_is_reserved_before_the_work(app, signed_in):
    from vault.api.idempotency import PENDING
    from vault.models import IdempotentRequest, User

    with app.state.db.sessions() as db:  # another request with this key is still running
        db.add(IdempotentRequest(user_id=db.query(User).one().id, key="busy", endpoint="POST /api/v1/decks",
                                 status=PENDING, body={}))
        db.commit()
    res = signed_in.post(f"{V1}/decks", json={"name": "Ramp", "text": "1 Sol Ring"}, headers={"Idempotency-Key": "busy"})
    assert res.status_code == 409 and res.headers["retry-after"]
    assert signed_in.get(f"{V1}/decks").json()["total"] == 0

    key = {"Idempotency-Key": "retry-after-failure"}  # a failed attempt releases its key
    bad = signed_in.post(f"{V1}/imports", files={"file": ("e.csv", b"hello\n", "text/csv")}, headers=key)
    assert bad.status_code == 400
    good = signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")}, headers=key)
    assert good.status_code == 201 and "idempotent-replayed" not in good.headers


def test_mcp_tool_calls_honour_the_idempotency_key(agent, bot):
    token = make_token(agent, scopes=["read", "write"])
    call = {"name": "save_deck", "arguments": {"name": "Ramp", "text": "1 Sol Ring"}}
    for id_ in (1, 2):  # a retry, even with a new JSON-RPC id, saves once
        body = {"jsonrpc": "2.0", "id": id_, "method": "tools/call", "params": call}
        res = bot.post("/api/mcp", json=body, headers={**auth(token), "Idempotency-Key": "save-1"})
        assert res.json()["result"]["isError"] is False
    assert agent.get(f"{V1}/decks").json()["total"] == 1
    batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": call} for i in (1, 2)]
    answers = bot.post("/api/mcp", json=batch, headers={**auth(token), "Idempotency-Key": "batch-1"}).json()
    assert [a["result"]["isError"] for a in answers] == [False, False]
    assert agent.get(f"{V1}/decks").json()["total"] == 3  # a batch's calls are separate operations


def test_mcp_arguments_must_be_an_object(agent, bot):
    token = make_token(agent)
    res = rpc(bot, "tools/call", {"name": "get_card", "arguments": ["not", "an", "object"]}, token)  # has required args
    assert res.status_code == 200 and res.json()["error"]["code"] == -32602


def test_a_retried_token_creation_never_makes_a_second_token(signed_in):
    key = {"Idempotency-Key": "token-1"}
    first = signed_in.post(f"{V1}/me/tokens", json={"name": "bot"}, headers=key)
    assert first.status_code == 201 and first.json()["token"].startswith("vault_pat_")
    again = signed_in.post(f"{V1}/me/tokens", json={"name": "bot"}, headers=key)
    assert again.status_code == 409 and again.headers["location"].endswith(f"/me/tokens/{first.json()['id']}")
    assert "vault_pat_" not in again.text  # the secret is never stored or shown again
    assert signed_in.get(f"{V1}/me/tokens").json()["total"] == 1


@pytest.mark.parametrize("params", [[1, 2], "x", 3, True])
@pytest.mark.parametrize("method", ["initialize", "tools/call", "tools/list"])
def test_mcp_params_that_are_not_an_object_are_invalid(agent, bot, method, params):
    read = make_token(agent)
    body = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}
    res = bot.post("/api/mcp", json=body, headers=auth(read))
    assert res.status_code == 200 and res.json()["error"]["code"] == -32602


@pytest.mark.parametrize("tool, arguments", [
    ("get_collection_summary", {"share_id": "not-a-number"}),
    ("get_collection_summary", {"share_id": True}),
    ("get_collection_summary", {"share_id": 0}),  # never "no share": that would read your own collection
    ("get_value_history", {"since": "yesterday"}),
    ("get_value_history", {"since": "2026-13-45"}),
    ("search_cards", {"limit": "10"}),
    ("check_decklist", {"text": 42}),
])
def test_mcp_arguments_of_the_wrong_type_are_invalid(agent, bot, tool, arguments):
    read = make_token(agent)
    res = rpc(bot, "tools/call", {"name": tool, "arguments": arguments}, read)
    assert res.status_code == 200 and res.json()["error"]["code"] == -32602, res.text


@pytest.mark.parametrize("arguments", [
    {"identifiers": [1]},  # an identifier that isn't an object
    {"identifiers": [{"nope": "x"}]},  # a property the schema doesn't allow
    {"identifiers": [{"id": 5}]},  # a nested value of the wrong type
    {"identifiers": [{"name": "Sol Ring"}] * 76},  # more than maxItems
])
def test_mcp_tool_arguments_are_checked_all_the_way_down(agent, bot, arguments):
    read = make_token(agent)
    res = rpc(bot, "tools/call", {"name": "lookup_cards", "arguments": arguments}, read)
    assert res.status_code == 200 and res.json()["error"]["code"] == -32602, res.text


def test_an_idempotent_import_that_fails_late_is_undone_and_can_be_retried(signed_in, monkeypatch):
    """If anything fails after the import was written, the import and the key go together: a
    retry with the same key imports once, never twice."""
    from vault import importer

    real = importer.compute_values
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the database went away")
        return real(*a, **kw)

    monkeypatch.setattr(importer, "compute_values", flaky)
    headers = {"Idempotency-Key": "import-late-failure"}
    with pytest.raises(RuntimeError):
        signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")}, headers=headers)
    retry = signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")}, headers=headers)
    assert retry.status_code == 201, retry.text
    assert signed_in.get(f"{V1}/imports").json()["total"] == 1


@pytest.mark.parametrize("identifier", [{}, {"set": "c21"}, {"collector_number": "263"}])
def test_mcp_lookup_identifiers_need_an_id_a_name_or_a_set_and_number(agent, bot, identifier):
    read = make_token(agent)
    res = rpc(bot, "tools/call", {"name": "lookup_cards", "arguments": {"identifiers": [identifier]}}, read)
    assert res.status_code == 200 and res.json()["error"]["code"] == -32602, res.text


@pytest.mark.parametrize("arguments", [[], "", 0, False])
def test_mcp_arguments_given_as_a_non_object_are_invalid_even_when_empty(agent, bot, arguments):
    read = make_token(agent)
    res = rpc(bot, "tools/call", {"name": "get_collection_summary", "arguments": arguments}, read)
    assert res.status_code == 200 and res.json()["error"]["code"] == -32602, res.text
