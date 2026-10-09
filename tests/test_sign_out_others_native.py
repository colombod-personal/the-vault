"""Step 1 of "Sign out everywhere" and what a copied cookie can still mint through the app routes (#347).

The routes that hand out something that outlives the browser session (an app session, a one-time hand-over code) must not
run after the session was ended: each re-reads ``users.session_key`` under a lock, right before the insert, and refuses a
cookie whose key was replaced meanwhile. The tests replace the key between authentication and that check, which is what a
request already past authentication sees when "Sign out the other browsers" commits first."""

import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from twins import Universe
from vault import auth as auth_module
from vault import tokens
from vault.locks import lock_account
from vault.api import v1 as v1_module
from vault.app import create_app
from vault.config import Settings
from vault.models import ApiSession, AuthCode, Identity, User, new_session_key
from vault.tokens import s256

GOOGLE_IOS = "ios-client.apps.googleusercontent.com"
V1 = "/api/v1"


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, session_secret="test", base_url="http://testserver", dev_login=True,
                    google_client_id="google-web", google_client_secret="x", google_ios_client_id=GOOGLE_IOS,
                    app_redirect_uris=("vault://auth",), auth_verify_rate_limit=100)


@pytest.fixture
def idp(settings):
    universe = Universe()
    universe.register_vault(settings)
    yield universe
    assert not universe.escapes


@pytest.fixture
def app(settings, idp):
    app = create_app(settings, serve_static=False, transport=idp.transport)
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def count(app, model):
    with app.state.db.sessions() as db:
        return db.scalar(select(func.count()).select_from(model))


def rotate_before_the_check(monkeypatch, app, module):
    """The account's session key is replaced after the request was authenticated and just before the route re-reads it."""
    real = module.require_live_session

    def hook(db, request, user_id):
        with app.state.db.sessions() as other:
            other.get(User, user_id).session_key = new_session_key()
            other.commit()
        return real(db, request, user_id)

    monkeypatch.setattr(module, "require_live_session", hook)


def handoff_ready(client, idp, sub="g-7"):
    """A browser signed in with Google that is asked to hand the sign-in to the app (PKCE)."""
    verifier = secrets.token_urlsafe(48)
    res = client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth", "code_challenge": s256(verifier)},
                     follow_redirects=False)
    cb = idp.google.approve(res.headers["location"], sub, email="gil@gmail.com", name="Gil")
    back = client.get(cb.url.replace("http://testserver", ""), follow_redirects=False)
    assert back.headers["location"] == "/api/auth/app-handoff"
    assert client.get("/api/auth/app-handoff").status_code == 200
    return verifier


def test_a_native_sign_in_by_a_cookie_already_past_authentication_mints_no_app_session(client, app, idp, monkeypatch):
    client.post("/api/auth/dev-login")
    me = client.get(f"{V1}/me").json()["id"]
    with app.state.db.sessions() as db:  # the account's own Google sign-in, so the native token below signs in to this account
        db.add(Identity(user_id=me, provider="google", subject="g-9", email="gil@gmail.com"))
        db.commit()
    rotate_before_the_check(monkeypatch, app, v1_module)
    raw = secrets.token_urlsafe(24)
    token = idp.google.native_id_token(GOOGLE_IOS, idp.google.add_account("g-9", "gil@gmail.com", "Gil"), nonce=raw)
    res = client.post(f"{V1}/auth/native/google", json={"id_token": token, "nonce": raw})
    assert res.status_code == 401 and "session ended" in res.json()["detail"]
    assert count(app, ApiSession) == 0


def test_a_native_sign_in_by_a_cookie_still_works_while_the_session_lives(client, app, idp):
    client.post("/api/auth/dev-login")
    me = client.get(f"{V1}/me").json()["id"]
    with app.state.db.sessions() as db:
        db.add(Identity(user_id=me, provider="google", subject="g-9", email="gil@gmail.com"))
        db.commit()
    raw = secrets.token_urlsafe(24)
    token = idp.google.native_id_token(GOOGLE_IOS, idp.google.add_account("g-9", "gil@gmail.com", "Gil"), nonce=raw)
    assert client.post(f"{V1}/auth/native/google", json={"id_token": token, "nonce": raw}).status_code == 200
    assert count(app, ApiSession) == 1


def test_the_app_handoff_by_a_cookie_already_past_authentication_mints_no_code(client, app, idp, monkeypatch):
    handoff_ready(client, idp)
    rotate_before_the_check(monkeypatch, app, auth_module)
    res = client.post("/api/auth/app-handoff", data={"decision": "continue"}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "vault://auth?error=access_denied"
    assert count(app, AuthCode) == 0


def test_a_hand_over_code_minted_with_the_copied_cookie_is_dead_after_step_1(client, app, idp):
    verifier = handoff_ready(client, idp)
    back = client.post("/api/auth/app-handoff", data={"decision": "continue"}, follow_redirects=False)
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]  # the copy's code, not yet redeemed
    assert count(app, AuthCode) == 1
    assert client.post("/api/auth/sign-out-others").json()["ok"] is True
    res = client.post(f"{V1}/auth/token", json={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                                 "redirect_uri": "vault://auth"})
    assert res.status_code == 400
    assert count(app, ApiSession) == 0 and count(app, AuthCode) == 0


def test_a_redeem_that_already_claimed_the_code_cannot_outlive_step_1(client, app, idp):
    """Finding 3: the redeem used to delete the code and commit before it made the session, outside any lock, so a redeem that had
    already claimed the code survived step 1. Now it takes the account lock first and claims and issues in one transaction: with
    step 1 holding the lock the redeem waits, finds the code deleted when it gets through, and mints nothing."""
    verifier = handoff_ready(client, idp)
    back = client.post("/api/auth/app-handoff", data={"decision": "continue"}, follow_redirects=False)
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
    with app.state.db.sessions() as db:
        uid = db.scalar(select(AuthCode.user_id))
    answer = {}

    def redeem():
        answer["status"] = TestClient(app).post(
            f"{V1}/auth/token", json={"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                                       "redirect_uri": "vault://auth"}).status_code

    with app.state.db.sessions() as step_1:
        lock_account(step_1, uid)  # step 1 holds the account...
        racer = threading.Thread(target=redeem)
        racer.start()
        time.sleep(0.7)
        assert racer.is_alive()  # ...the redeem waits for it
        step_1.query(AuthCode).delete()  # and step 1 deletes the unredeemed codes
        step_1.commit()
    racer.join(20)
    assert answer["status"] == 400
    assert count(app, ApiSession) == 0


def test_an_app_session_cannot_link_a_sign_in_after_it_was_ended(client, app, idp, monkeypatch):
    """Finding 2, the link path: a bearer caller adding a new identity is held to its live session row too."""
    client.post("/api/auth/dev-login")
    me = client.get(f"{V1}/me").json()["id"]
    with app.state.db.sessions() as db:
        secret = tokens.issue(db, db.get(User, me), client="ios")["access_token"]
    real = auth_module.require_live_session

    def end_the_session_first(db, request, user_id):
        with app.state.db.sessions() as other:
            other.query(ApiSession).delete()
            other.commit()
        return real(db, request, user_id)

    monkeypatch.setattr(auth_module, "require_live_session", end_the_session_first)
    raw = secrets.token_urlsafe(24)
    token = idp.google.native_id_token(GOOGLE_IOS, idp.google.add_account("g-new", "new@gmail.com", "New"), nonce=raw)
    res = TestClient(app).post(f"{V1}/auth/native/google", json={"id_token": token, "nonce": raw},
                               headers={"Authorization": f"Bearer {secret}"})
    assert res.status_code == 401 and "session ended" in res.json()["detail"]
    with app.state.db.sessions() as db:
        assert db.scalar(select(Identity.id).where(Identity.subject == "g-new")) is None  # nothing was linked
