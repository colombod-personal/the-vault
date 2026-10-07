"""Account -> Connected apps shows one row per app (#246). Connecting an app again (a second device, or re-adding it
so it reads a new tool list) leaves the older connection valid on purpose, so the list groups connections by app,
marks the unused ones idle, and Disconnect revokes all of them."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from tests.test_mcp_oauth import V1, app, client, db_do, make_client, settings, universe  # noqa: F401 - fixtures
from twins.mcp_client import ChatGptClient, McpClient
from vault import oauth_server
from vault.models import OAuthGrant, OAuthRetiredRefresh


def rows(browser):
    return browser.get(f"{V1}/me/apps").json()["items"]


def connect_again(c: McpClient, write: bool = False) -> dict:
    """The same app connecting once more (another device, or added again): its own browser trip and tokens."""
    c.tokens = c.redeem(c.approve(write=write, **({"scope": "read write"} if write else {}))["code"]).json()
    return c.tokens


def age(app, grant_id, **changes):
    def go(db):
        db.execute(update(OAuthGrant).where(OAuthGrant.id == grant_id).values(**changes))
        db.commit()
    db_do(app, go)


def now():
    return oauth_server._now()


def registered(browser, name):
    """An app that registers itself (RFC 7591), as Claude Code does: a new client id every time it is added."""
    res = browser.post("/oauth/register", json={"client_name": name, "redirect_uris": ["https://app.example/callback"]})
    assert res.status_code == 201, res.text
    return McpClient(lambda: browser, res.json()["client_id"])


# -- one row per app ---------------------------------------------------------------------------

def test_an_app_connected_three_times_is_one_row_with_its_connections_counted(app, client):
    client.sign_in()
    first = dict(client.connect())
    second = dict(connect_again(client))
    third = dict(connect_again(client, write=True))
    [item] = rows(client.browser)
    assert item["name"] == "Twin Agent" and item["domain"] == "app.example" and item["connections"] == 3
    assert item["scopes"] == ["read", "write"]  # the newest connection speaks for the app
    assert len(item["connection_ids"]) == 3 and item["id"] == max(item["connection_ids"])
    assert item["created_at"] and item["last_used_at"] is None and item["idle"] is False and item["idle_connections"] == 0
    assert len({first["access_token"], second["access_token"], third["access_token"]}) == 3
    listed = client.browser.get(f"{V1}/me/apps").json()
    assert listed["total"] == 1  # the total counts apps, not connections
    assert "token" not in str(item) and "hash" not in str(item)


def test_the_row_shows_the_widest_scopes_so_an_older_write_connection_is_not_hidden(app, client):
    client.sign_in()
    connect_again(client, write=True)
    connect_again(client)  # the newest connection is read only
    [item] = rows(client.browser)
    assert item["connections"] == 2 and item["scopes"] == ["read", "write"]


def test_the_first_connected_and_last_used_dates_span_all_connections(app, client):
    client.sign_in()
    client.connect()
    connect_again(client)
    old, new = sorted(oauth_server_ids(app))
    age(app, old, created_at=now() - timedelta(days=9), last_used_at=now() - timedelta(days=3))
    age(app, new, last_used_at=now() - timedelta(minutes=30))
    [item] = rows(client.browser)
    assert item["created_at"][:10] == (now() - timedelta(days=9)).strftime("%Y-%m-%d")
    assert item["last_used_at"][:16] == (now() - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")


def oauth_server_ids(app):
    return db_do(app, lambda db: list(db.scalars(select(OAuthGrant.id))))


def test_apps_that_register_themselves_are_told_apart_by_name_not_by_their_new_client_id(app, client):
    client.sign_in()
    browser = client.browser
    for _ in range(3):  # Claude Code adds itself afresh each time: a new client id, the same name
        c = registered(browser, "Claude Code")
        c.tokens = c.redeem(c.approve()["code"]).json()
    other = registered(browser, "My Script")
    other.tokens = other.redeem(other.approve()["code"]).json()
    client.tokens = client.redeem(client.approve()["code"]).json()  # a verified app
    by_name = {r["name"]: r for r in rows(browser)}
    assert set(by_name) == {"Claude Code", "My Script", "Twin Agent"} and by_name["Claude Code"]["connections"] == 3
    assert by_name["Claude Code"]["verified_by_address"] is False and by_name["Twin Agent"]["verified_by_address"] is True


def test_a_self_registered_app_never_joins_the_row_of_a_verified_app_with_the_same_name(app, make_client):
    verified = make_client()
    verified.sign_in()
    verified.tokens = verified.redeem(verified.approve()["code"]).json()
    impostor = registered(verified.browser, "Twin Agent")  # the name the verified app gave itself
    impostor.tokens = impostor.redeem(impostor.approve()["code"]).json()
    items = rows(verified.browser)
    assert sorted(i["verified_by_address"] for i in items) == [False, True] and all(i["connections"] == 1 for i in items)


# -- Disconnect revokes every connection of the app --------------------------------------------

def test_disconnect_stops_every_connection_of_the_app_each_one_by_its_id(app, client, make_client):
    client.sign_in()
    sessions = []
    for _ in range(3):
        connect_again(client)
        sessions.append(dict(client.tokens))
    other = make_client(host="other.example", redirect_uris=("https://other.example/cb",))
    other.redirect_uri = "https://other.example/cb"
    other.browser = client.browser
    other.tokens = other.redeem(other.approve()["code"]).json()
    for s in sessions:
        assert client.mcp("ping", token=s["access_token"]).status_code == 200
    by_domain = {r["domain"]: r for r in rows(client.browser)}
    row = by_domain["app.example"]
    assert row["connections"] == 3 and set(by_domain) == {"app.example", "other.example"}
    res = client.browser.delete(row["_links"]["self"]["href"])
    assert res.json() == {"deleted": True, "connections": 3}
    for s in sessions:  # each connection's access and refresh token stops at once
        assert client.mcp("ping", token=s["access_token"]).status_code == 401
        assert client.refresh(s["refresh_token"]).status_code == 400
    assert other.mcp("ping").status_code == 200  # another app is untouched
    assert [r["domain"] for r in rows(client.browser)] == ["other.example"]


def test_disconnect_is_one_transaction_all_or_nothing(app, client, monkeypatch):
    client.sign_in()
    client.connect()
    connect_again(client)
    commits = []
    real = oauth_server.Session.commit
    monkeypatch.setattr(oauth_server.Session, "commit", lambda self: (commits.append(1), real(self))[1])
    [item] = rows(client.browser)
    commits.clear()
    client.browser.delete(item["_links"]["self"]["href"])
    assert len(commits) == 1  # both connections went in one commit, so a failure leaves none revoked


def test_disconnecting_by_an_older_connections_id_disconnects_the_whole_app(app, client):
    client.sign_in()
    client.connect()
    connect_again(client)
    oldest = min(oauth_server_ids(app))
    assert client.browser.delete(f"{V1}/me/apps/{oldest}").json() == {"deleted": True, "connections": 2}
    assert oauth_server_ids(app) == [] and rows(client.browser) == []
    assert client.browser.delete(f"{V1}/me/apps/{oldest}").status_code == 404


def test_disconnect_removes_the_retired_refresh_tokens_of_every_connection(app, client):
    client.sign_in()
    client.connect()
    client.refresh()  # rotates: the old refresh token is kept as retired
    connect_again(client)
    count = lambda db: db.scalar(select(func.count()).select_from(OAuthRetiredRefresh))  # noqa: E731
    assert db_do(app, count) == 1
    [item] = rows(client.browser)
    client.browser.delete(item["_links"]["self"]["href"])
    assert db_do(app, count) == 0


def test_the_confirmation_names_an_app_used_in_the_last_hour(app, client):
    client.sign_in()
    client.connect()
    [grant_id] = oauth_server_ids(app)
    assert rows(client.browser)[0]["used_minutes_ago"] is None  # never used
    age(app, grant_id, last_used_at=now() - timedelta(minutes=12, seconds=20))
    assert rows(client.browser)[0]["used_minutes_ago"] == 12
    connect_again(client)
    newest = max(oauth_server_ids(app))
    age(app, newest, last_used_at=now() - timedelta(minutes=3))  # the freshest connection speaks for the app
    assert rows(client.browser)[0]["used_minutes_ago"] == 3
    age(app, newest, last_used_at=now() - timedelta(hours=2))
    age(app, grant_id, last_used_at=now() - timedelta(hours=2))
    assert rows(client.browser)[0]["used_minutes_ago"] is None  # not used in the last hour: nothing to name


# -- idle after 14 days, removed when the refresh token expires --------------------------------

def test_a_connection_unused_for_14_days_is_marked_idle_and_the_app_is_idle_when_all_are(app, client):
    client.sign_in()
    client.connect()
    connect_again(client)
    old, new = sorted(oauth_server_ids(app))
    age(app, old, last_used_at=now() - timedelta(days=14, minutes=1))
    age(app, new, last_used_at=now() - timedelta(days=13, hours=23))
    [item] = rows(client.browser)
    assert item["idle"] is False and item["idle_connections"] == 1 and item["connections"] == 2
    age(app, new, last_used_at=now() - timedelta(days=15))
    [item] = rows(client.browser)
    assert item["idle"] is True and item["idle_connections"] == 2


def test_a_connection_never_used_is_idle_from_the_day_it_was_made(app, client):
    client.sign_in()
    client.connect()
    [grant_id] = oauth_server_ids(app)
    assert rows(client.browser)[0]["idle"] is False
    age(app, grant_id, created_at=now() - timedelta(days=15))
    assert rows(client.browser)[0]["idle"] is True


def test_a_connection_is_removed_when_its_refresh_token_expires_but_its_siblings_stay(app, client):
    client.sign_in()
    expired = dict(client.connect())
    connect_again(client)
    old, new = sorted(oauth_server_ids(app))
    age(app, old, refresh_expires=now() - timedelta(seconds=1))
    [item] = rows(client.browser)  # listing it also removes it
    assert item["connections"] == 1 and item["connection_ids"] == [new]
    assert oauth_server_ids(app) == [new]
    assert client.refresh(expired["refresh_token"]).status_code == 400
    assert client.mcp("ping").status_code == 200


def test_an_app_whose_only_connection_expired_disappears_from_the_list(app, client):
    client.sign_in()
    client.connect()
    [grant_id] = oauth_server_ids(app)
    age(app, grant_id, refresh_expires=now() - timedelta(days=1))
    assert rows(client.browser) == [] and oauth_server_ids(app) == []


def test_using_a_connection_keeps_it_from_expiring_because_each_refresh_renews_the_30_days(app, client):
    client.sign_in()
    tokens = client.connect()
    [grant_id] = oauth_server_ids(app)
    age(app, grant_id, refresh_expires=now() + timedelta(days=1), last_used_at=now() - timedelta(days=20))
    assert client.refresh(tokens["refresh_token"]).status_code == 200
    renewed = db_do(app, lambda db: db.get(OAuthGrant, grant_id).refresh_expires)
    assert renewed - now() > timedelta(days=29)


def test_connecting_an_app_clears_everyones_expired_connections_even_if_nobody_opens_the_list(app, client, make_client):
    client.sign_in()
    client.connect()
    [stale] = oauth_server_ids(app)
    age(app, stale, refresh_expires=now() - timedelta(days=1))
    other = make_client(host="other.example", redirect_uris=("https://other.example/cb",))
    other.redirect_uri = "https://other.example/cb"
    other.sign_in("someone-else@example.com")
    other.tokens = other.redeem(other.approve()["code"]).json()
    assert stale not in oauth_server_ids(app)  # removed by the new connection, not by anyone reading a list


def test_the_cap_counts_connections_but_the_list_counts_apps(app, client, monkeypatch):
    monkeypatch.setattr(oauth_server, "MAX_APPS", 2)
    client.sign_in()
    client.connect()
    for _ in range(3):
        connect_again(client)
    [item] = rows(client.browser)
    assert item["connections"] == 2  # the oldest connections made room


# -- two devices of the same app both keep working (ChatGPT's twin) ----------------------------

def test_two_chatgpt_devices_both_keep_working_after_the_second_connects(app, universe):
    first = ChatGptClient(lambda: TestClient(app), universe.client_hosts)
    first.add_app(write=True)
    second = ChatGptClient(lambda: TestClient(app), universe.client_hosts, key=first.key)  # same app, another device
    second.add_app(write=True)
    assert first.tokens["access_token"] != second.tokens["access_token"]
    for device in (first, second):
        res = device.mcp("tools/list")
        assert res.status_code == 200 and res.json()["result"]["tools"]
    for device in (first, second):  # each refreshes its own chain, the way the app keeps the new pair
        res = device.refresh()
        assert res.status_code == 200
        device.tokens = res.json()
    assert first.mcp("tools/list").status_code == 200 and second.mcp("tools/list").status_code == 200
    [item] = rows(second.browser)
    assert item["name"] == "ChatGPT" and item["connections"] == 2  # the person sees one app, not two
    assert second.browser.delete(item["_links"]["self"]["href"]).json()["connections"] == 2
    assert first.mcp("tools/list").status_code == 401 and second.mcp("tools/list").status_code == 401
