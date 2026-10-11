"""Switching Vault account from the chat (bead vault-wxy.3.1): a host such as the ChatGPT desktop app gives a plugin's MCP server
no sign-out control and keeps reusing its saved token, so `sign_out` ends the one connection the request comes from. Without
`confirm` it says who is signed in, which app, and what happens, and changes nothing; with `confirm` it revokes only that
connection, whatever its scopes. Personal access tokens and web sign-ins are refused with where to end them. The old token
then gets the MCP 401 that points at the resource metadata, so the host signs in again."""

import pytest
from sqlalchemy import func, select

from tests.test_mcp_oauth import BASE, V1, app, client, db_do, make_client, settings, universe  # noqa: F401 - fixtures
from twins.mcp_client import McpClient
from vault import oauth_server, tokens as native_tokens
from vault.models import OAuthGrant, User

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
    assert body["signed_in_as"]["email"] == "d***@localhost" and "id" in body["signed_in_as"] and "name" in body["signed_in_as"]
    assert "dev@localhost" not in str(preview)  # a connected app is never handed the whole address
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


def test_the_preview_shows_what_this_token_carries_not_what_the_person_once_allowed(app, client):
    """A refresh may narrow the scopes (RFC 6749 section 6): the preview says what authenticate() grants this token."""
    tokens = client.connect(write=True, scope="read write")
    assert result(client, tokens["access_token"], "sign_out")["structuredContent"]["connection"]["scopes"] == ["read", "write"]
    narrowed = client.refresh(scope="read").json()
    assert narrowed["scope"] == "read"
    body = result(client, narrowed["access_token"], "sign_out")["structuredContent"]
    assert body["connection"]["scopes"] == ["read"]


def test_a_token_refresh_between_the_lookup_and_the_revoke_still_signs_the_connection_out(app, client, monkeypatch):
    """The connection is revoked by its id, not looked up a second time by an access token that a refresh just replaced."""
    tokens = client.connect()
    real = oauth_server.grant_of_access
    seen = {}

    def refreshed_meanwhile(db, user_id, bearer):
        grant = real(db, user_id, bearer)
        if not seen:
            seen["tokens"] = client.refresh(tokens["refresh_token"]).json()  # the app renews its token right here
        return grant

    monkeypatch.setattr(oauth_server, "grant_of_access", refreshed_meanwhile)
    done = result(client, tokens["access_token"], "sign_out", confirm=True)
    assert done["isError"] is False and done["structuredContent"]["signed_out"] is True
    assert grants(app) == 0
    assert client.mcp("tools/list", token=seen["tokens"]["access_token"]).status_code == 401  # the renewed token went too


def test_a_connection_ended_meanwhile_says_already_signed_out_not_that_it_is_the_apps_own_sign_in(app, client, monkeypatch):
    token = client.connect()["access_token"]
    monkeypatch.setattr(oauth_server, "grant_of_access", lambda db, user_id, bearer: None)
    for args in ({}, {"confirm": True}):
        answer = result(client, token, "sign_out", **args)
        assert answer["isError"] is True
        detail = answer["structuredContent"]["detail"]
        assert "already signed out" in detail and "Vault app" not in detail
    assert grants(app) == 1


def test_the_native_apps_own_sign_in_is_refused_and_keeps_working(app, client):
    client.connect()  # the person exists; one app connection is not touched
    native = db_do(app, lambda db: native_tokens.issue(db, db.scalar(select(User)), client="app"))["access_token"]
    for args in ({}, {"confirm": True}):
        refused = result(client, native, "sign_out", **args)
        assert refused["isError"] is True and "Vault app" in refused["structuredContent"]["detail"]
    assert grants(app) == 1
    assert result(client, native, "whoami")["isError"] is False  # the sign-in still works


@pytest.mark.parametrize("email,masked", [("dev@localhost", "d***@localhost"), ("a@b.example", "a***@b.example"),
                                          ("Long.Name+tag@sub.example.com", "L***@sub.example.com"), (None, None), ("", None),
                                          ("no-at-sign", None), ("@nolocal.example", None)])
def test_an_email_is_masked_to_its_first_letter_and_domain(email, masked):
    from vault.api.catalog_api import mask_email
    assert mask_email(email) == masked
