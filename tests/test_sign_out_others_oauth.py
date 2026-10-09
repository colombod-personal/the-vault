"""Step 1 of "Sign out everywhere" and the OAuth authorization codes a copied cookie can mint (#347).

An authorization code lives 60 seconds and is redeemed at /oauth/token with no cookie, so a code minted with the copied
session must die with it, and a consent answer already past authentication must not mint one after the session ended."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from twins import Universe
from twins.mcp_client import McpClient
from vault import oauth_routes
from vault.app import create_app
from vault.config import Settings
from vault.models import OAuthCode, OAuthConsent, OAuthGrant, User, new_session_key


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, session_secret="test", dev_login=True, base_url="http://testserver",
                    oauth_rate_limit=1000, oauth_register_rate_limit=1000, oauth_fetch_limit=100_000, oauth_fetch_ip_limit=100_000)


@pytest.fixture
def universe(settings):
    u = Universe()
    u.register_vault(settings)
    yield u
    assert not u.escapes


@pytest.fixture
def app(settings, universe):
    app = create_app(settings, serve_static=False, transport=universe.transport, resolver=universe.resolve)
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def client(app, universe):
    return McpClient(lambda: TestClient(app), universe.client_hosts.publish())


def count(app, model):
    with app.state.db.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_an_authorization_code_minted_with_the_copied_cookie_is_dead_after_step_1(app, client):
    client.sign_in()
    code = client.approve()["code"]  # minted by the copy; its holder redeems it at /oauth/token, which needs no cookie
    page = client.authorize()  # and a consent screen left unanswered
    assert count(app, OAuthCode) == 1 and count(app, OAuthConsent) == 1
    assert client.browser.post("/api/auth/sign-out-others").json()["ok"] is True
    res = client.redeem(code)
    assert res.status_code == 400 and res.json()["error"] == "invalid_grant"
    assert count(app, OAuthGrant) == 0 and count(app, OAuthCode) == 0 and count(app, OAuthConsent) == 0
    assert page.status_code == 200


def test_a_code_that_was_redeemed_before_step_1_leaves_a_recent_grant_that_step_1_removes(app, client):
    client.connect()
    assert count(app, OAuthGrant) == 1
    res = client.browser.post("/api/auth/sign-out-others").json()
    assert res["connected_apps_removed"] == 1 and count(app, OAuthGrant) == 0


def test_a_consent_answer_already_past_authentication_mints_no_code_after_the_session_ended(app, client, monkeypatch):
    client.sign_in()
    page = client.authorize()
    real = oauth_routes.require_live_session

    def rotate_first(db, request, user_id):  # "Sign out the other browsers" commits just before the route re-reads the key
        with app.state.db.sessions() as other:
            other.get(User, user_id).session_key = new_session_key()
            other.commit()
        return real(db, request, user_id)

    monkeypatch.setattr(oauth_routes, "require_live_session", rotate_first)
    res = client.answer(page)
    assert res.status_code != 303 and "location" not in res.headers and "session ended" in res.text
    assert count(app, OAuthCode) == 0
