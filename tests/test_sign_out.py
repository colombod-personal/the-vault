"""Switching Vault account from the chat (bead vault-wxy.3.1): a host such as the ChatGPT desktop app gives a plugin's MCP server
no sign-out control and keeps reusing its saved token, so `sign_out` ends the one connection the request comes from. Without
`confirm` it says who is signed in, which app, and what happens, and changes nothing; with `confirm` it revokes only that
connection, whatever its scopes. Personal access tokens and web sign-ins are refused with where to end them. The old token
then gets the MCP 401 that points at the resource metadata, so the host signs in again."""

from sqlalchemy import func, select

from tests.test_mcp_oauth import BASE, V1, app, client, db_do, make_client, settings, universe  # noqa: F401 - fixtures
from twins.mcp_client import McpClient
from vault.models import OAuthGrant

METADATA = f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/api/mcp"'


def grants(app) -> int:
    return db_do(app, lambda db: db.scalar(select(func.count()).select_from(OAuthGrant)))


def call(c: McpClient, token: str, name: str, **arguments):
    return c.mcp("tools/call", {"name": name, "arguments": arguments}, token=token)


def result(c: McpClient, token: str, name: str, **arguments) -> dict:
    res = call(c, token, name, **arguments)
    assert res.status_code == 200, res.text
    return res.json()["result"]


def another_device(c: McpClient) -> str:
    """The same app connecting once more from another device: its own browser trip and tokens."""
    res = c.redeem(c.approve()["code"])
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def test_the_preview_says_who_which_app_and_what_happens_and_changes_nothing(app, client):
    token = client.connect()["access_token"]
    preview = result(client, token, "sign_out")
    assert preview["isError"] is False
    body = preview["structuredContent"]
    assert body["signed_out"] is False
    assert body["signed_in_as"]["email"] == "dev@localhost" and "id" in body["signed_in_as"] and "name" in body["signed_in_as"]
    assert body["connection"]["app"] == "Twin Agent" and body["connection"]["domain"] == "app.example"
    assert body["connection"]["scopes"] == ["read"] and body["connection"]["other_connections"] == 0
    assert "stops working" in body["what_happens"] and "Use a different account" in body["what_happens"]
    assert result(client, token, "sign_out", confirm=False)["structuredContent"]["signed_out"] is False
    assert grants(app) == 1
    assert result(client, token, "whoami")["isError"] is False  # still signed in


def test_confirm_ends_only_this_connection_and_another_device_keeps_working(app, client, make_client):
    first = client.connect()["access_token"]
    second = another_device(client)  # the same app on another device
    other_app = make_client(host="other.example")
    third = other_app.connect()["access_token"]  # another app of the same person
    assert grants(app) == 3
    done = result(client, first, "sign_out", confirm=True)
    assert done["isError"] is False
    body = done["structuredContent"]
    assert body["signed_out"] is True and body["connection"]["other_connections"] == 2
    assert body["what_happens"].startswith("Signed out.")
    assert grants(app) == 2
    assert client.mcp("tools/list", token=first).status_code == 401
    assert result(client, second, "whoami")["isError"] is False
    assert result(other_app, third, "whoami")["isError"] is False


def test_a_read_only_connection_lists_the_tool_as_destructive_and_can_sign_out(app, client):
    tokens = client.connect()
    assert tokens["scope"] == "read"
    tools = {t["name"]: t for t in client.mcp("tools/list", token=tokens["access_token"]).json()["result"]["tools"]}
    assert "save_deck" not in tools  # still a read-only listing
    tool = tools["sign_out"]
    assert tool["title"] == "Sign out of the Vault here"
    assert tool["annotations"]["destructiveHint"] is True and tool["annotations"]["readOnlyHint"] is False
    assert tool["inputSchema"]["required"] == [] and set(tool["inputSchema"]["properties"]) == {"confirm"}
    assert result(client, tokens["access_token"], "sign_out", confirm=True)["structuredContent"]["signed_out"] is True
    assert grants(app) == 0


def test_a_write_connection_can_sign_out_too(app, client):
    token = client.connect(write=True, scope="read write")["access_token"]
    body = result(client, token, "sign_out", confirm=True)["structuredContent"]
    assert body["signed_out"] is True and body["connection"]["scopes"] == ["read", "write"]
    assert grants(app) == 0


def test_a_personal_access_token_is_refused_and_keeps_working(app, client):
    client.connect()  # an app connection exists too, and is not touched
    pat = client.browser.post(f"{V1}/me/tokens", json={"name": "bot", "scopes": ["read"]}).json()["token"]
    for args in ({}, {"confirm": True}):
        refused = result(client, pat, "sign_out", **args)
        assert refused["isError"] is True and "Agents & API" in refused["structuredContent"]["detail"]
    assert grants(app) == 1
    assert result(client, pat, "whoami")["isError"] is False  # the token still works


def test_a_web_session_is_refused_and_stays_signed_in(app, client):
    client.connect()
    for method in ("GET", "POST"):
        res = client.browser.request(method, f"{V1}/agent/sign-out")
        assert res.status_code == 409 and "Sign out" in res.json()["detail"], res.text
    assert client.browser.get(f"{V1}/me").status_code == 200
    assert grants(app) == 1


def test_the_route_called_directly_with_an_oauth_token_is_refused(app, client):
    """An OAuth access token is for the MCP server only (RFC 8707): the REST route itself does not take it."""
    token = client.connect()["access_token"]
    for method in ("GET", "POST"):
        assert client.api.request(method, f"{V1}/agent/sign-out", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert grants(app) == 1


def test_the_old_token_then_gets_401_with_the_resource_metadata_and_cannot_refresh(app, client):
    tokens = client.connect()
    first_user = result(client, tokens["access_token"], "whoami")["structuredContent"]["user"]["id"]
    assert result(client, tokens["access_token"], "sign_out", confirm=True)["structuredContent"]["signed_out"] is True
    res = client.mcp("tools/list", token=tokens["access_token"])
    assert res.status_code == 401
    challenge = res.headers["www-authenticate"]
    assert challenge.startswith('Bearer realm="the-vault"') and 'error="invalid_token"' in challenge and METADATA in challenge
    refreshed = client.refresh(tokens["refresh_token"])
    assert refreshed.status_code == 400 and refreshed.json()["error"] == "invalid_grant"
    # The host signs in again: the consent page offers another account, and the new connection is that account's.
    assert "Use a different account" in client.authorize().text
    switched = client.connect(email="someone-else@example.com")["access_token"]
    who = result(client, switched, "whoami")["structuredContent"]["user"]["id"]
    assert who != first_user
