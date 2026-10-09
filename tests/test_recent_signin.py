"""A recent sign-in for the serious account actions, and the e-mailed code that can supply one (#347, criteria 2 and 4).

The owner's decision (2026-10-09): delete the account, export all data, create a personal access token, add or remove a passkey,
link or unlink a provider need a sign-in from the last ten minutes; the person confirms with a passkey or a linked provider, or
with a one-time code e-mailed to the address on the account. These tests are the evidence, one behaviour each:

* every protected route refuses a stale session (403, the stable code ``recent_sign_in_required``, nothing changed) and accepts a
  fresh one; the window is the server's and expires; a cookie from before the release is stale;
* a provider sign-in that LINKS a method does not make a session fresh; signing in with a method the account had does;
* a personal access token, a connected app and a reviewer's demo session get 403 as before; an app session is as old as its sign-in;
* the e-mailed code: sent to the twin of Resend (nothing contacts the network), hashed, single use, five tries, ten minutes,
  three an hour and a daily cap, bound to the browser session that asked, safe against a link prefetcher, and with a fallback
  for an account with no address or a Vault with no sender.
"""

import base64
import hashlib
import json
import re
import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import func, select, text

from twins import Universe
from twins.authenticator import SoftAuthenticator
from vault import recent_signin, retention, tokens
from vault.app import create_app
from vault.config import Settings
from vault.models import AccessToken, EmailCode, Identity, Passkey, User, utcnow
from vault.privacy import purge_user

ORIGIN = "https://vault.test"
V1 = "/api/v1"
START, CONFIRM, POLL, LINK = ("/api/auth/recent/email/" + p for p in ("start", "confirm", "poll", "link"))
BUNDLE = "com.example.vault.ios"
SECRET = "s" * 32
CHROME = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}


def make_settings(database_url, **overrides):
    return Settings(**{
        "database_url": database_url, "session_secret": SECRET, "base_url": ORIGIN,
        "google_client_id": "google-app", "google_client_secret": "g-secret",
        "microsoft_client_id": "ms-app", "microsoft_client_secret": "m-secret",
        "apple_app_bundle_id": BUNDLE, "resend_api_key": "re_test",
        "auth_rate_limit": 1000, "auth_verify_rate_limit": 1000, **overrides})


@pytest.fixture
def universe(database_url):
    u = Universe()
    u.register_vault(make_settings(database_url))
    u.resend.add_key("re_test")
    yield u
    assert not u.escapes, f"calls left the twin universe: {u.escapes}"


@pytest.fixture
def make_app(database_url, universe):
    apps = []

    def make(**overrides):
        app = create_app(make_settings(database_url, **overrides), serve_static=False, transport=universe.transport)
        apps.append(app)
        return app

    yield make
    for app in apps:
        app.state.db.engine.dispose()


@pytest.fixture
def app(make_app):
    return make_app()


def browser(app):
    return TestClient(app, base_url=ORIGIN)


@pytest.fixture
def client(app):
    with browser(app) as c:
        yield c


def go(client, location: str) -> str:
    res = client.get(location.replace(ORIGIN, ""), follow_redirects=False)
    assert res.status_code == 303, res.text
    return res.headers["location"]


def sign_in(client, twin, sub: str) -> str:
    """The browser signs in (or links, when it is signed in) with the twin account ``sub``."""
    location = client.get(f"/api/auth/login/{twin.name}", follow_redirects=False).headers["location"]
    return go(client, twin.approve(location, sub).url)


def web_account(client, universe, sub="g-ann", email="ann@gmail.com") -> int:
    """A signed-in account with Google (so it has an address), its sign-in aged past the 24-hour window."""
    universe.google.add_account(sub, email, "Ann")
    sign_in(client, universe.google, sub)
    uid = client.get(f"{V1}/me").json()["id"]
    age_methods(client, uid)
    return uid


def age_methods(client, uid, days=10):
    with client.app.state.db.sessions() as db:
        for row in db.scalars(select(Identity).where(Identity.user_id == uid)):
            row.created_at = utcnow() - timedelta(days=days)
        for row in db.scalars(select(Passkey).where(Passkey.user_id == uid)):
            row.created_at = utcnow() - timedelta(days=days)
        db.commit()


def add_passkey(client, uid, name, hours_ago):
    with client.app.state.db.sessions() as db:
        if not db.scalar(select(Identity.id).where(Identity.user_id == uid, Identity.provider == "passkey")):
            db.add(Identity(user_id=uid, provider="passkey", subject=f"handle-{uid}"))
        row = Passkey(user_id=uid, credential_id=f"cred-{uid}-{name}", public_key=b"k", name=name,
                      created_at=utcnow() - timedelta(hours=hours_ago))
        db.add(row)
        db.commit()
        return row.id


def add_provider(client, uid, provider, hours_ago):
    with client.app.state.db.sessions() as db:
        row = Identity(user_id=uid, provider=provider, subject=f"{provider}-{uid}-{hours_ago}", created_at=utcnow() - timedelta(hours=hours_ago))
        db.add(row)
        db.commit()
        return row.id


def refused(res) -> bool:
    return (res.status_code == 403 and res.json()["code"] == "recent_sign_in_required"
            and res.headers["x-error"] == "recent_sign_in_required" and res.json()["window_seconds"] == 600)


def status(client) -> dict:
    res = client.get(f"{V1}/me/recent-sign-in")
    assert res.status_code == 200, res.text
    return res.json()


def sent(universe) -> list[dict]:
    return universe.resend.sent


def last_mail(universe) -> tuple[str, str]:
    """The code and the link, read from the mail the person would have received."""
    mail = universe.resend.sent[-1]
    return re.search(r"Your code: (\d{6})", mail["text"]).group(1), re.search(r"(https://vault\.test/\S+)", mail["text"]).group(1)


def forge_cookie(client, data: dict) -> None:
    raw = base64.b64encode(json.dumps(data).encode("utf-8"))
    client.cookies.set("vault_session", TimestampSigner(SECRET).sign(raw).decode(), domain="vault.test")


# -- the window ---------------------------------------------------------------------------------

def test_a_sign_in_is_recent_for_ten_minutes_and_then_it_is_not(client, universe, go_stale):
    web_account(client, universe)
    now = status(client)
    assert now["fresh"] is True and 595 <= now["seconds_left"] <= 600 and now["window_seconds"] == 600
    go_stale(500)
    assert status(client)["fresh"] is True and 90 <= status(client)["seconds_left"] <= 100
    go_stale(110)  # 610 seconds after the sign-in
    stale = status(client)
    assert stale["fresh"] is False and stale["seconds_left"] == 0


def test_the_window_is_the_servers_setting(make_app, universe, go_stale):
    app = make_app(recent_signin_seconds=120)
    with browser(app) as c:
        web_account(c, universe)
        assert status(c)["window_seconds"] == 120
        go_stale(100)
        assert status(c)["fresh"] is True
        go_stale(30)
        assert status(c)["fresh"] is False


def test_the_window_setting_is_bounded():
    for bad in (0, 59, 3601):
        with pytest.raises(RuntimeError, match="RECENT_SIGNIN_SECONDS"):
            Settings(database_url="postgresql://x", session_secret=SECRET, base_url=ORIGIN, recent_signin_seconds=bad).check()


@pytest.mark.parametrize("claim", ["missing", "future", "text", "boolean"])
def test_a_cookie_without_a_believable_sign_in_time_is_stale(client, universe, claim):
    """A cookie from before this release has no ``auth_at``: everybody confirms once. A time in the future or a value of
    another type never counts either (the cookie is signed, so only the Vault ever wrote it)."""
    uid = web_account(client, universe)
    with client.app.state.db.sessions() as db:
        key = db.get(User, uid).session_key
    data = {"uid": uid, "sk": key}
    if claim != "missing":
        data["auth_at"] = {"future": recent_signin.now_ts() + 3600, "text": "yesterday", "boolean": True}[claim]
    forge_cookie(client, data)
    assert client.get(f"{V1}/me").status_code == 200  # a valid session ...
    assert status(client)["fresh"] is False  # ... that is not recent
    assert refused(client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}))


# -- every protected route: stale is refused with nothing changed, fresh is accepted --------------

ACTIONS = {
    "delete the account": (lambda c: c.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}), 200),
    "export all data": (lambda c: c.get(f"{V1}/me/export"), 200),
    "create a personal access token": (lambda c: c.post(f"{V1}/me/tokens", json={"name": "bot", "scopes": ["read"]}), 201),
    "start adding a passkey": (lambda c: c.post("/api/auth/passkey/register/options", json={}), 200),
    # no ceremony is in progress, so a fresh session gets a 400 from the passkey code: past the recent-sign-in check
    "finish adding a passkey": (lambda c: c.post("/api/auth/passkey/register/verify", json={"credential": {}}), 400),
}


@pytest.mark.parametrize("name", ACTIONS)
def test_a_stale_session_is_refused_and_a_fresh_one_accepted(client, universe, go_stale, name):
    act, ok = ACTIONS[name]
    uid = web_account(client, universe)
    if name != "delete the account":
        assert act(client).status_code == ok  # signed in a moment ago
    go_stale()
    res = act(client)
    assert refused(res), (name, res.status_code, res.text[:200])
    with client.app.state.db.sessions() as db:
        assert db.get(User, uid) is not None  # nothing was deleted
        assert db.scalar(select(func.count()).select_from(AccessToken)) == (1 if name == "create a personal access token" else 0)
    sign_in(client, universe.google, "g-ann")  # signing in again with a method the account has
    assert status(client)["fresh"] is True
    assert act(client).status_code == ok


def test_the_fresh_delete_really_deletes_after_confirming_again(client, universe, go_stale):
    uid = web_account(client, universe)
    go_stale()
    assert refused(client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}))
    sign_in(client, universe.google, "g-ann")
    res = client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"})
    assert res.status_code == 200 and res.json()["deleted"] is True
    with client.app.state.db.sessions() as db:
        assert db.get(User, uid) is None


def test_removing_an_older_passkey_needs_a_recent_sign_in_and_a_new_one_does_not(client, universe, go_stale):
    uid = web_account(client, universe)
    old = add_passkey(client, uid, "Old laptop", hours_ago=24 * 30)
    fresh_one = add_passkey(client, uid, "Planted", hours_ago=2)
    go_stale()
    res = client.delete(f"{V1}/me/passkeys/{old}")
    assert refused(res)
    assert client.delete(f"{V1}/me/passkeys/{fresh_one}").status_code == 200  # what "Sign out everywhere" offers stays free
    sign_in(client, universe.google, "g-ann")
    assert client.delete(f"{V1}/me/passkeys/{old}").status_code == 200  # Google (older) remains
    with client.app.state.db.sessions() as db:
        assert db.scalar(select(func.count(Passkey.id)).where(Passkey.user_id == uid)) == 0


def test_an_older_provider_stays_linked_even_to_a_fresh_session_and_a_new_one_is_free(client, universe, go_stale):
    """The relaxation (unlinking an older provider after a recent sign-in) was built, reviewed and withdrawn; see remove_identity."""
    uid = web_account(client, universe)
    old = add_provider(client, uid, "microsoft", hours_ago=24 * 30)
    new = add_provider(client, uid, "facebook", hours_ago=2)
    assert client.delete(f"{V1}/me/identities/{old}").status_code == 409  # fresh
    go_stale()
    assert client.delete(f"{V1}/me/identities/{old}").status_code == 409  # stale: the same answer
    assert client.delete(f"{V1}/me/identities/{new}").status_code == 200  # what Sign out everywhere offers needs no sign-in


def test_a_fresh_session_still_cannot_remove_the_last_old_method(client, universe):
    """The 24-hour rule stays under the new check: a copied cookie inside its ten fresh minutes cannot lock the owner out. The
    account's only old method is a passkey; a method added an hour ago would be all that is left: refused, though the session is fresh."""
    uid = web_account(client, universe)
    with client.app.state.db.sessions() as db:
        db.execute(text("UPDATE identities SET created_at = now() - interval '1 hour' WHERE provider = 'google'"))
        db.commit()
    old = add_passkey(client, uid, "Owner", hours_ago=24 * 30)
    assert status(client)["fresh"] is True
    res = client.delete(f"{V1}/me/passkeys/{old}")
    assert res.status_code == 409 and "older than 24 hours" in res.json()["detail"]
    listed = {m["name"]: m for m in client.get(f"{V1}/me/sign-in-methods").json()["items"]}
    assert listed["Owner"]["removable_reason"] == "needs_older_method"


def test_the_address_a_code_goes_to_is_one_a_provider_vouches_for_and_that_is_a_day_old(client, universe, go_stale):
    """Found by review: ``users.email`` is the first provider's unverified word, and a copied session could set it on a passkey-only
    account by linking its own Google inside a fresh window. The code goes to a verified address of a provider that has been
    linked for a day, the oldest first; nothing else is ever mailed."""
    uid = web_account(client, universe)
    universe.microsoft.add_account("m-1", "ann@outlook.com", "Ann")
    sign_in(client, universe.microsoft, "m-1")  # Microsoft's address is never taken as verified
    with client.app.state.db.sessions() as db:
        rows = {i.provider: i for i in db.scalars(select(Identity).where(Identity.user_id == uid))}
        assert rows["google"].email_verified is True and rows["microsoft"].email_verified is False
        db.execute(text("UPDATE identities SET created_at = now() - interval '5 days' WHERE provider = 'microsoft'"))
        db.commit()
    go_stale()
    assert status(client)["email"]["to"] == "***@g***.com"  # Google's (oldest, verified), not Microsoft's
    # the copied session links its own Google inside a fresh window: its address is new, so it receives nothing
    universe.google.add_account("g-eve", "eve@gmail.com", "Eve")
    sign_in(client, universe.google, "g-ann")  # the owner (fresh again)
    assert sign_in(client, universe.google, "g-eve") == "/?linked=google"
    client.post(START)
    assert [m["to"] for m in sent(universe)] == [["ann@gmail.com"]]


def test_a_passkey_only_account_has_no_address_to_poison_for_a_day(app, universe):
    with browser(app) as c:
        options = c.post("/api/auth/passkey/signup/options", json={"name": "Pat"}, headers={"Origin": ORIGIN}).json()
        c.post("/api/auth/passkey/signup/verify", json={"credential": SoftAuthenticator().create(options, ORIGIN), "name": "Phone"},
               headers={"Origin": ORIGIN})
        universe.google.add_account("g-eve", "eve@gmail.com", "Eve")
        assert sign_in(c, universe.google, "g-eve") == "/?linked=google"  # a copied session, fresh, links its own Google
        assert c.get(f"{V1}/me").json()["email"] == "eve@gmail.com"  # (the profile address, unverified for this purpose)
        assert status(c)["email"] == {"available": False, "to": None, "reason": "no_address"}
        assert c.post(START).json()["code"] == "no_email_on_account" and not sent(universe)


def test_an_unverified_google_address_receives_nothing(client, universe):
    universe.google.add_account("g-un", "un@gmail.com", "Un", email_verified=False)
    sign_in(client, universe.google, "g-un")
    age_methods(client, client.get(f"{V1}/me").json()["id"])
    assert status(client)["email"]["reason"] == "no_address" and client.post(START).status_code == 409


def test_a_second_ask_replaces_the_first_and_a_copy_of_the_old_cookie_gets_nothing(app, universe, go_stale):
    with browser(app) as mine:
        web_account(mine, universe)
        go_stale()
        mine.post(START)
        first, _ = last_mail(universe)
        copy = browser(app)
        copy.cookies.update(mine.cookies)  # a copy taken after the first ask: it holds that ask's request id
        mine.post(START)  # a new request id, the first code is dead
        second, _ = last_mail(universe)
        assert copy.post(CONFIRM, json={"code": first}).json()["code"] == "no_code"
        assert copy.post(CONFIRM, json={"code": second}).json()["code"] == "no_code"
        assert copy.post(POLL).json() == {"fresh": False, "waiting": False}
        assert mine.post(CONFIRM, json={"code": second}).status_code == 200


def test_the_approve_button_posts_with_the_sites_own_origin(client, universe, go_stale):
    """A form POST sends ``Origin: null`` under ``Referrer-Policy: no-referrer``, which the cross-site-write guard refuses: the page
    uses same-origin, so the browser sends the site's own origin, which is let through."""
    web_account(client, universe)
    go_stale()
    client.post(START)
    _, link = last_mail(universe)
    token = link.split("token=")[1]
    with browser(client.app) as phone:
        assert phone.get(link.replace(ORIGIN, "")).headers["referrer-policy"] == "same-origin"
        refused_ = phone.post("/api/auth/recent/email/link", data={"token": token, "decision": "approve"}, headers={"Origin": "null"})
        assert refused_.status_code == 403  # what no-referrer would have produced
        ok = phone.post("/api/auth/recent/email/link", data={"token": token, "decision": "approve"}, headers={"Origin": ORIGIN})
        assert ok.status_code == 200 and "Approved" in ok.text


def test_linking_from_the_app_flow_with_a_stale_session_goes_back_to_the_app_with_the_error(client, universe, go_stale):
    web_account(client, universe)
    universe.microsoft.add_account("m-app", "ann@outlook.com", "Ann")
    go_stale()
    location = client.get("/api/auth/login/microsoft", params={"app_redirect_uri": "vault://auth", "code_challenge": "a" * 43},
                          follow_redirects=False).headers["location"]
    assert go(client, universe.microsoft.approve(location, "m-app").url) == "vault://auth?error=recent_sign_in_required"
    assert client.get(f"{V1}/me").json()["providers"] == ["google"]


def test_sign_out_everywhere_works_without_a_recent_sign_in(client, universe, go_stale):
    uid = web_account(client, universe)
    add_passkey(client, uid, "Planted", hours_ago=1)
    go_stale()
    assert client.post("/api/auth/sign-out-others").status_code == 200
    assert client.delete(f"{V1}/me/sign-in-methods/recent").json() == {"deleted": 1}
    assert client.get(f"{V1}/me/sign-in-methods", params={"recent_only": "true"}).json()["items"] == []


def test_linking_a_provider_needs_a_recent_sign_in(client, universe, go_stale):
    web_account(client, universe)
    universe.microsoft.add_account("m-1", "ann@outlook.com", "Ann")
    go_stale()
    back = sign_in(client, universe.microsoft, "m-1")
    assert back == "/?link_error=recent_sign_in_required&provider=microsoft"
    assert client.get(f"{V1}/me").json()["providers"] == ["google"]  # nothing linked, and still signed in
    sign_in(client, universe.google, "g-ann")  # confirm with the method the account has
    assert sign_in(client, universe.microsoft, "m-1") == "/?linked=microsoft"
    assert client.get(f"{V1}/me").json()["providers"] == ["google", "microsoft"]


def test_a_copied_cookie_cannot_link_its_own_sign_in_or_claim_an_empty_account(app, universe, go_stale):
    with browser(app) as owner, browser(app) as thief:
        web_account(owner, universe)
        universe.microsoft.add_account("m-evil", "evil@outlook.com", "Eve")
        thief.cookies.update(owner.cookies)  # the copy
        go_stale()
        # the thief's own Microsoft account is new to the owner's account: a link
        assert sign_in(thief, universe.microsoft, "m-evil") == "/?link_error=recent_sign_in_required&provider=microsoft"
        # ... and so is one that already has an empty account of its own (a claim)
        with browser(app) as eve:
            sign_in(eve, universe.microsoft, "m-evil")
        assert sign_in(thief, universe.microsoft, "m-evil") == "/?link_error=recent_sign_in_required&provider=microsoft"
        assert owner.get(f"{V1}/me").json()["providers"] == ["google"]


def test_linking_does_not_make_a_session_fresh_but_signing_in_with_a_known_method_does(client, universe, go_stale):
    web_account(client, universe)
    universe.microsoft.add_account("m-2", "ann@outlook.com", "Ann")
    go_stale(300)
    assert sign_in(client, universe.microsoft, "m-2") == "/?linked=microsoft"  # allowed: 300 s after the sign-in
    go_stale(350)  # 650 s after the SIGN-IN; the link at 300 s did not renew it
    assert status(client)["fresh"] is False
    sign_in(client, universe.microsoft, "m-2")  # a method the account now has
    assert status(client)["fresh"] is True


# -- who is not the person ---------------------------------------------------------------------

def test_a_personal_access_token_still_has_no_account_powers(client, universe):
    web_account(client, universe)
    secret = client.post(f"{V1}/me/tokens", json={"name": "bot", "scopes": ["read", "write"]}).json()["token"]
    agent = {"Authorization": f"Bearer {secret}"}
    with browser(client.app) as bot:
        for res in (bot.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}, headers=agent),
                    bot.get(f"{V1}/me/export", headers=agent), bot.post(f"{V1}/me/tokens", json={"name": "x", "scopes": ["read"]}, headers=agent),
                    bot.get(f"{V1}/me/recent-sign-in", headers=agent), bot.post(START, headers=agent),
                    bot.post(CONFIRM, json={"code": "123456"}, headers=agent), bot.post(POLL, headers=agent),
                    bot.post("/api/auth/passkey/register/options", json={}, headers=agent)):
            assert res.status_code == 403 and "code" not in res.json(), res.text  # the old refusal, not the recent-sign-in one
    assert not universe.resend.sent and not universe.resend.calls


def test_a_reviewers_demo_session_cannot_ask_for_a_code(database_url, universe):
    settings = make_settings(database_url, reviewer_passphrase="correct horse battery staple")
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with browser(app) as c:
        assert c.post("/api/auth/reviewer-login", json={"passphrase": "correct horse battery staple"}).status_code == 200
        assert c.get(f"{V1}/me").status_code == 200
        for res in (c.post(START), c.post(CONFIRM, json={"code": "123456"}), c.post(POLL), c.get(f"{V1}/me/recent-sign-in"),
                    c.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}), c.get(f"{V1}/me/export")):
            assert res.status_code == 403, res.text
    assert not universe.resend.sent
    app.state.db.engine.dispose()


def native_sign_in(client, universe, sub="a-1"):
    raw = secrets.token_urlsafe(16)
    universe.apple.add_account(sub, "ann@privaterelay.appleid.com")
    token = universe.apple.native_id_token(BUNDLE, sub, nonce=hashlib.sha256(raw.encode()).hexdigest())
    return client.post(f"{V1}/auth/native/apple", json={"id_token": token, "nonce": raw})


def test_an_app_session_is_as_recent_as_its_sign_in(client, universe):
    """The Vault app holds a bearer token. It is recent while the app session is: refreshing does not renew it, signing in
    again (a new session) does. Nothing about a cookie is involved."""
    tokens_ = native_sign_in(client, universe).json()
    header = {"Authorization": f"Bearer {tokens_['access_token']}"}
    assert client.get(f"{V1}/me/recent-sign-in", headers=header).json()["fresh"] is True
    assert client.get(f"{V1}/me/export", headers=header).status_code == 200
    with client.app.state.db.sessions() as db:
        db.execute(text("UPDATE api_sessions SET created_at = now() - interval '1 hour'"))
        db.commit()
    for res in (client.get(f"{V1}/me/export", headers=header), client.post(f"{V1}/me/tokens", json={"name": "x", "scopes": ["read"]}, headers=header),
                client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}, headers=header)):
        assert refused(res), res.text
    assert client.get(f"{V1}/me/recent-sign-in", headers=header).json()["email"] == {"available": False, "to": None, "reason": "app"}
    assert client.post(START, headers=header).json()["code"] == "app_sign_in_required"  # the app signs in again; no mail for apps
    with client.app.state.db.sessions() as db:  # a refresh does not renew it
        rotated = tokens.refresh(db, tokens_["refresh_token"])
    header = {"Authorization": f"Bearer {rotated['access_token']}"}
    assert refused(client.get(f"{V1}/me/export", headers=header))
    again = native_sign_in(client, universe).json()  # signing in again makes a new session
    assert client.get(f"{V1}/me/export", headers={"Authorization": f"Bearer {again['access_token']}"}).status_code == 200


def test_an_app_cannot_link_another_sign_in_from_a_stale_session(client, universe):
    first = native_sign_in(client, universe, "a-1").json()
    header = {"Authorization": f"Bearer {first['access_token']}"}
    with client.app.state.db.sessions() as db:
        db.execute(text("UPDATE api_sessions SET created_at = now() - interval '1 hour'"))
        db.commit()
    raw = secrets.token_urlsafe(16)
    universe.apple.add_account("a-evil", "eve@privaterelay.appleid.com")
    evil = universe.apple.native_id_token(BUNDLE, "a-evil", nonce=hashlib.sha256(raw.encode()).hexdigest())
    res = client.post(f"{V1}/auth/native/apple", json={"id_token": evil, "nonce": raw}, headers=header)
    assert refused(res)
    assert client.get(f"{V1}/me", headers=header).json()["providers"] == ["apple"]


# -- the e-mailed code ---------------------------------------------------------------------------

def test_a_code_is_mailed_to_the_address_on_the_account_and_says_who_asked(client, universe):
    web_account(client, universe)
    res = client.post(START, headers=CHROME)
    assert res.status_code == 200
    assert res.json() == {"sent": True, "to": "***@g***.com", "expires_in": 600}
    assert "ann@gmail.com" not in res.text and re.search(r"\d{6}", res.text) is None  # neither the address nor the code comes back
    [mail] = sent(universe)
    assert mail["to"] == ["ann@gmail.com"] and mail["from"] == "The Vault <login@mtgvault.cards>"
    code, link = last_mail(universe)
    assert re.fullmatch(r"\d{6}", code) and link.startswith("https://vault.test/api/auth/recent/email/link?token=")
    assert "Chrome on Windows" in mail["text"] and "If this was not you, ignore this e-mail" in mail["text"]
    assert "expires in 10 minutes" in mail["text"] and "Chrome on Windows" in mail["html"]
    assert status(client)["email"] == {"available": True, "to": "***@g***.com", "reason": None}


def test_the_code_works_once_and_confirms_the_session(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    assert status(client)["fresh"] is False
    client.post(START)
    code, _ = last_mail(universe)
    res = client.post(CONFIRM, json={"code": code})
    assert res.status_code == 200 and res.json()["fresh"] is True
    assert status(client)["fresh"] is True
    assert client.get(f"{V1}/me/export").status_code == 200  # what it was for
    go_stale()
    again = client.post(CONFIRM, json={"code": code})  # single use
    assert again.status_code == 400 and again.json()["code"] == "no_code"
    assert status(client)["fresh"] is False


def test_a_code_may_be_typed_with_a_space_or_a_dash(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START)
    code, _ = last_mail(universe)
    assert client.post(CONFIRM, json={"code": f"{code[:3]} {code[3:]}"}).status_code == 200


def test_the_code_is_stored_only_as_a_keyed_hash(client, universe, app):
    uid = web_account(client, universe)
    client.post(START)
    code, link = last_mail(universe)
    token = link.split("token=")[1]
    with app.state.db.sessions() as db:
        row = db.scalars(select(EmailCode)).one()
        everything = " ".join(str(v) for v in (row.code_hash, row.link_hash, row.session_digest, row.asked_from))
        assert code not in everything and token not in everything and "ann@" not in everything
        assert len(row.code_hash) == 64 and row.user_id == uid
        expected = hmac_of(code, uid, row.session_digest)
        assert row.code_hash == expected  # keyed with SESSION_SECRET: a leaked table cannot be tried offline without it
        assert (row.expires_at - row.created_at).total_seconds() == 600


def hmac_of(code, uid, digest):
    return recent_signin._mac(Settings(database_url="x", session_secret=SECRET), "code", uid, digest, code)


def test_the_code_is_compared_in_constant_time(client, universe, monkeypatch):
    web_account(client, universe)
    client.post(START)
    seen = []
    real = recent_signin.hmac.compare_digest
    monkeypatch.setattr(recent_signin.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    client.post(CONFIRM, json={"code": "000000"})
    assert any(isinstance(a, str) and len(a) == len(b) == 64 for a, b in seen)  # equal-length digests ...
    assert not any("000000" in (a, b) for a, b in seen)  # ... never the digits themselves


def test_five_wrong_tries_kill_the_code_even_for_the_right_one(client, universe):
    web_account(client, universe)
    client.post(START)
    code, _ = last_mail(universe)
    wrong = "000000" if code != "000000" else "111111"
    left = []
    for _ in range(4):
        res = client.post(CONFIRM, json={"code": wrong})
        assert res.status_code == 400 and res.json()["code"] == "wrong_code"
        left.append(res.json()["tries_left"])
    assert left == [4, 3, 2, 1]
    last = client.post(CONFIRM, json={"code": wrong})
    assert last.json()["tries_left"] == 0 and "last try" in last.json()["detail"]
    dead = client.post(CONFIRM, json={"code": code})  # the right code, too late
    assert dead.status_code == 400 and dead.json()["code"] == "code_dead"
    with client.app.state.db.sessions() as db:
        assert db.scalars(select(EmailCode)).one().used_at is None and db.scalars(select(EmailCode)).one().tries == 5


def test_a_new_code_replaces_the_one_before(client, universe, go_stale, monkeypatch):
    web_account(client, universe)
    go_stale()
    numbers = iter([111111, 222222])
    monkeypatch.setattr(recent_signin.secrets, "randbelow", lambda n: next(numbers))
    client.post(START)
    client.post(START)
    assert [m["text"].count("111111") + m["text"].count("222222") for m in sent(universe)] == [1, 1]
    stale_code = client.post(CONFIRM, json={"code": "111111"})  # the earlier code stopped working when the second went out
    assert stale_code.status_code == 400 and stale_code.json()["code"] == "wrong_code"
    assert client.post(CONFIRM, json={"code": "222222"}).status_code == 200


def test_a_code_expires_after_ten_minutes(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START)
    code, _ = last_mail(universe)
    with client.app.state.db.sessions() as db:
        db.execute(text("UPDATE email_codes SET expires_at = now() - interval '1 second'"))
        db.commit()
    res = client.post(CONFIRM, json={"code": code})
    assert res.status_code == 400 and res.json()["code"] == "no_code"
    assert status(client)["fresh"] is False


def test_a_code_that_is_not_six_digits_is_refused_without_counting(client, universe):
    web_account(client, universe)
    client.post(START)
    for bad in ("12345", "1234567", "abcdef", "12 34"):
        assert client.post(CONFIRM, json={"code": bad}).json()["code"] == "invalid_code"
    with client.app.state.db.sessions() as db:
        assert db.scalars(select(EmailCode)).one().tries == 0


def test_three_codes_an_hour_for_an_account_and_then_429(client, universe):
    web_account(client, universe)
    for _ in range(3):
        assert client.post(START).status_code == 200
    res = client.post(START)
    assert res.status_code == 429 and res.json()["code"] == "email_hourly_limit"
    assert 1 <= int(res.headers["retry-after"]) <= 3600 and res.json()["retry_after_seconds"] == int(res.headers["retry-after"])
    assert len(sent(universe)) == 3  # the fourth was never sent
    with client.app.state.db.sessions() as db:
        db.execute(text("UPDATE email_codes SET created_at = now() - interval '61 minutes'"))
        db.commit()
    assert client.post(START).status_code == 200  # an hour later


def test_the_vaults_daily_cap_stops_every_account(make_app, universe):
    app = make_app(email_daily_cap=2)
    with browser(app) as ann, browser(app) as bob:
        web_account(ann, universe)
        universe.google.add_account("g-bob", "bob@gmail.com", "Bob")
        sign_in(bob, universe.google, "g-bob")
        age_methods(bob, bob.get(f"{V1}/me").json()["id"])
        assert ann.post(START).status_code == 200 and bob.post(START).status_code == 200
        res = ann.post(START)
        assert res.status_code == 429 and res.json()["code"] == "email_daily_cap" and res.headers["retry-after"]
        assert len(sent(universe)) == 2
        assert "passkey" in res.json()["detail"]  # and says what else works


def test_a_code_requested_in_one_browser_cannot_be_used_in_another(app, universe, go_stale):
    with browser(app) as mine, browser(app) as other:
        web_account(mine, universe)
        sign_in(other, universe.google, "g-ann")  # another browser on the same account (same session key)
        thief = browser(app)
        thief.cookies.update(mine.cookies)  # a copy of the cookie, taken before the code was asked for
        go_stale()
        mine.post(START)
        code, _ = last_mail(universe)
        for b in (other, thief):
            res = b.post(CONFIRM, json={"code": code})
            assert res.status_code == 400 and res.json()["code"] == "no_code", res.text
            assert status(b)["fresh"] is False
        # the thief asks for a code of its own: it goes to the owner's mailbox; mine does not open its session
        thief.post(START)
        res = thief.post(CONFIRM, json={"code": code})
        assert res.status_code == 400 and res.json()["code"] == "wrong_code"
        assert status(thief)["fresh"] is False
        assert mine.post(CONFIRM, json={"code": code}).status_code == 200  # the browser that asked
        assert status(mine)["fresh"] is True and status(other)["fresh"] is False


def test_a_code_belongs_to_one_account(app, universe, go_stale):
    with browser(app) as ann, browser(app) as bob:
        web_account(ann, universe)
        universe.google.add_account("g-bob", "bob@gmail.com", "Bob")
        sign_in(bob, universe.google, "g-bob")
        go_stale()
        ann.post(START)
        code, _ = last_mail(universe)
        res = bob.post(CONFIRM, json={"code": code})
        assert res.json()["code"] == "no_code" and status(bob)["fresh"] is False


def test_signing_everyone_out_kills_a_code_that_was_waiting(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START)
    code, link = last_mail(universe)
    assert client.post("/api/auth/sign-out-others").status_code == 200  # the account's session key changes
    res = client.post(CONFIRM, json={"code": code})
    assert res.status_code == 400 and res.json()["code"] == "no_code"
    with browser(client.app) as anywhere:  # the link too: approving it does not confirm the browser that asked
        anywhere.post(f"/api/auth/recent/email/link", data={"token": link.split("token=")[1], "decision": "approve"})
    assert client.post(POLL).json() == {"fresh": False, "waiting": False}


# -- the link: opening it changes nothing, the button does ------------------------------------------

def test_a_link_prefetcher_cannot_spend_the_code(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START, headers=CHROME)
    code, link = last_mail(universe)
    path = link.replace(ORIGIN, "")
    with browser(client.app) as scanner:  # a mail scanner or a link preview: GET, no cookies, maybe twice
        for _ in range(3):
            page = scanner.get(path)
            assert page.status_code == 200 and "Approve" in page.text and 'method="post"' in page.text
            assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "same-origin"
            assert "Chrome on Windows" in page.text and "***@g***.com" in page.text
            assert "ann@gmail.com" not in page.text  # the address is masked on the page too
    assert client.post(POLL).json() == {"fresh": False, "waiting": True}  # nothing was approved
    assert status(client)["fresh"] is False
    assert client.post(CONFIRM, json={"code": code}).status_code == 200  # the code is still good


def test_approving_the_link_elsewhere_confirms_the_browser_that_asked_once(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START)
    _, link = last_mail(universe)
    token = link.split("token=")[1]
    with browser(client.app) as phone:  # the mail opened on another device, not signed in
        done = phone.post("/api/auth/recent/email/link", data={"token": token, "decision": "approve"})
        assert done.status_code == 200 and "Approved" in done.text
        again = phone.post("/api/auth/recent/email/link", data={"token": token, "decision": "approve"})
        assert again.status_code == 200  # approving twice changes nothing more
    assert status(client)["fresh"] is False  # until the asking browser notices
    polled = client.post(POLL)
    assert polled.json()["fresh"] is True and status(client)["fresh"] is True
    go_stale()
    assert client.post(POLL).json() == {"fresh": False, "waiting": False}  # single use
    with browser(client.app) as phone:
        assert phone.get(link.replace(ORIGIN, "")).status_code == 410  # the link is spent
    assert client.get(f"{V1}/me/export").status_code == 403  # stale again


def test_approving_in_the_browser_that_asked_confirms_it_at_once(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START)
    _, link = last_mail(universe)
    res = client.post("/api/auth/recent/email/link", data={"token": link.split("token=")[1], "decision": "approve"})
    assert res.status_code == 200 and "Confirmed" in res.text
    assert status(client)["fresh"] is True


def test_a_link_opened_in_another_browser_of_the_same_account_does_not_confirm_that_one(app, universe, go_stale):
    with browser(app) as asker, browser(app) as other:
        web_account(asker, universe)
        sign_in(other, universe.google, "g-ann")
        go_stale()
        asker.post(START)
        _, link = last_mail(universe)
        other.post("/api/auth/recent/email/link", data={"token": link.split("token=")[1], "decision": "approve"})
        assert status(other)["fresh"] is False  # it did not ask
        assert asker.post(POLL).json()["fresh"] is True


def test_this_was_not_me_kills_the_request(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START)
    code, link = last_mail(universe)
    with browser(client.app) as phone:
        res = phone.post("/api/auth/recent/email/link", data={"token": link.split("token=")[1], "decision": "deny"})
        assert res.status_code == 200 and "Nothing was approved" in res.text
    assert client.post(POLL).json() == {"fresh": False, "waiting": False}
    assert client.post(CONFIRM, json={"code": code}).json()["code"] == "no_code"


def test_a_link_with_a_wrong_or_expired_token_is_gone(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START)
    _, link = last_mail(universe)
    with browser(client.app) as phone:
        assert phone.get("/api/auth/recent/email/link?token=" + "x" * 43).status_code == 410
        assert phone.post("/api/auth/recent/email/link", data={"token": "x" * 43, "decision": "approve"}).status_code == 410
        with client.app.state.db.sessions() as db:
            db.execute(text("UPDATE email_codes SET expires_at = now() - interval '1 second'"))
            db.commit()
        assert phone.get(link.replace(ORIGIN, "")).status_code == 410
    assert client.post(POLL).json() == {"fresh": False, "waiting": False}


# -- no address, no sender, a mail that does not go ------------------------------------------------

def test_an_account_with_no_address_is_told_and_confirms_with_a_passkey(app, universe, go_stale):
    phone = SoftAuthenticator()
    with browser(app) as c:
        options = c.post("/api/auth/passkey/signup/options", json={"name": "Pat"}, headers={"Origin": ORIGIN}).json()
        c.post("/api/auth/passkey/signup/verify", json={"credential": phone.create(options, ORIGIN), "name": "Phone"}, headers={"Origin": ORIGIN})
        assert c.get(f"{V1}/me").json()["email"] is None
        go_stale()
        assert status(c)["fresh"] is False and status(c)["email"] == {"available": False, "to": None, "reason": "no_address"}
        res = c.post(START)
        assert res.status_code == 409 and res.json()["code"] == "no_email_on_account" and "passkey" in res.json()["detail"]
        assert not sent(universe)
        # the fallback: sign in again with the passkey
        options = c.post("/api/auth/passkey/login/options", json={}, headers={"Origin": ORIGIN}).json()
        assert c.post("/api/auth/passkey/login/verify", json={"credential": phone.get(options, ORIGIN)}, headers={"Origin": ORIGIN}).status_code == 200
        assert status(c)["fresh"] is True


def test_with_no_mail_sender_configured_the_feature_is_off_and_the_other_paths_work(make_app, universe, go_stale):
    app = make_app(resend_api_key="")
    assert app.state.email_sender is None
    with browser(app) as c:
        web_account(c, universe)
        go_stale()
        assert status(c)["email"] == {"available": False, "to": None, "reason": "no_sender"}
        res = c.post(START)
        assert res.status_code == 409 and res.json()["code"] == "email_unavailable"
        assert c.post(CONFIRM, json={"code": "123456"}).json()["code"] == "no_code"
        sign_in(c, universe.google, "g-ann")  # the provider path
        assert status(c)["fresh"] is True
    assert not universe.resend.calls and not universe.resend.sent


def test_a_mail_that_cannot_be_sent_costs_nothing_and_leaves_no_code(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    universe.resend.outage = True
    res = client.post(START)
    assert res.status_code == 502 and res.json()["code"] == "email_send_failed" and "passkey" in res.json()["detail"]
    assert not sent(universe)
    with client.app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(EmailCode)) == 0  # it does not count against the caps
    universe.resend.outage = False
    assert client.post(START).status_code == 200


def test_a_failed_resend_leaves_the_earlier_code_valid(client, universe, go_stale):
    web_account(client, universe)
    go_stale()
    client.post(START)
    code, _ = last_mail(universe)
    universe.resend.outage = True
    assert client.post(START).status_code == 502
    assert client.post(CONFIRM, json={"code": code}).status_code == 200


def test_one_transient_failure_is_retried_and_one_mail_goes(client, universe):
    web_account(client, universe)
    universe.resend.fail_next("/emails", 503, times=1)
    assert client.post(START).status_code == 200
    assert len(sent(universe)) == 1 and len(universe.resend.calls) == 2


def test_the_mail_service_never_sees_anything_but_the_mail(client, universe):
    web_account(client, universe)
    client.post(START)
    assert [(c.method, c.host, c.path) for c in universe.resend.calls] == [("POST", "api.resend.com", "/emails")]
    assert universe.resend.calls[0].headers["authorization"] == "Bearer re_test"


# -- housekeeping ------------------------------------------------------------------------------------

def test_old_codes_are_pruned_by_the_daily_job_and_erased_with_the_account(client, universe):
    uid = web_account(client, universe)
    client.post(START)
    with client.app.state.db.sessions() as db:
        db.add(EmailCode(user_id=uid, session_digest="d", code_hash="c", link_hash="l-old", asked_from="x",
                         expires_at=utcnow() - timedelta(days=3), created_at=utcnow() - timedelta(days=3)))
        db.commit()
        assert retention.apply(db)["email_codes_deleted"] == 1
        assert db.scalar(select(func.count()).select_from(EmailCode)) == 1  # the one that still counts toward the caps
        removed = purge_user(db, uid)
        assert removed["email_codes"] == 1
        assert db.scalar(select(func.count()).select_from(EmailCode)) == 0


def test_the_routes_and_settings_are_documented():
    from pathlib import Path

    api = (Path(__file__).parent.parent / "docs" / "api.md").read_text(encoding="utf-8")
    for needle in ("/me/recent-sign-in", "/api/auth/recent/email/start", "/api/auth/recent/email/confirm", "/api/auth/recent/email/link",
                   "recent_sign_in_required", "RECENT_SIGNIN_SECONDS", "RESEND_API_KEY"):
        assert needle in api, f"docs/api.md does not mention {needle}"


# -- guessing odds, Apple's relay, bounded locks (review of #429) -----------------------------------------------

def test_an_account_has_a_budget_of_failed_tries_an_hour_and_then_only_the_other_paths_remain(client, universe, go_stale):
    """Five tries a code, three codes an hour would be 15 guesses an hour at a six-digit code from a stale cookie: the account
    gets 10 an hour over all its codes (about 0.7 percent over a cookie's 30 days even at the maximum, before the daily limit)."""
    web_account(client, universe)
    go_stale()
    for _ in range(2):
        client.post(START)
        code, _link = last_mail(universe)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            assert client.post(CONFIRM, json={"code": wrong}).json()["code"] in ("wrong_code",)
    client.post(START)  # (a third code is mailed: ten tries are spent, the budget is what stops the guessing)
    res = client.post(CONFIRM, json={"code": "123456"})
    assert res.status_code == 429 and res.json()["code"] == "email_attempts_exhausted" and int(res.headers["retry-after"]) > 0
    again = client.post(START)
    assert again.status_code == 429 and again.json()["code"] == "email_attempts_exhausted"
    code, _ = last_mail(universe)
    assert client.post(CONFIRM, json={"code": code}).status_code == 429  # not even the right one
    assert status(client)["fresh"] is False
    sign_in(client, universe.google, "g-ann")  # the passkey and provider paths remain
    assert status(client)["fresh"] is True
    with client.app.state.db.sessions() as db:  # an hour later the budget is whole again
        db.execute(text("UPDATE email_codes SET created_at = now() - interval '61 minutes'"))
        db.commit()
    go_stale()
    assert client.post(START).status_code == 200


def test_an_account_may_ask_for_ten_codes_a_day(client, universe, go_stale):
    uid = web_account(client, universe)
    go_stale()
    with client.app.state.db.sessions() as db:
        for n in range(8):  # eight earlier today, outside the hour
            db.add(EmailCode(user_id=uid, session_digest="d", code_hash="c", link_hash=f"old-{n}", asked_from="x",
                             created_at=utcnow() - timedelta(hours=2), expires_at=utcnow() - timedelta(hours=2), used_at=utcnow()))
        db.commit()
    assert client.post(START).status_code == 200 and client.post(START).status_code == 200
    res = client.post(START)
    assert res.status_code == 429 and res.json()["code"] == "email_account_daily_limit" and res.headers["retry-after"]
    assert len(sent(universe)) == 2


def relay_account(client, universe):
    uid = web_account(client, universe)
    with client.app.state.db.sessions() as db:
        db.execute(text("UPDATE identities SET email = 'abc123@privaterelay.appleid.com' WHERE provider = 'google'"))
        db.commit()
    return uid


def test_an_apple_private_relay_address_is_not_mailed_until_the_sender_is_registered(client, universe):
    relay_account(client, universe)
    assert status(client)["email"] == {"available": False, "to": None, "reason": "relay_unregistered"}
    res = client.post(START)
    assert res.status_code == 409 and res.json()["code"] == "email_relay_unregistered" and "Nothing was sent" in res.json()["detail"]
    assert not sent(universe)
    with client.app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(EmailCode)) == 0  # and it counted for nothing


def test_an_apple_private_relay_address_is_mailed_once_the_owner_registered_the_sender(make_app, universe):
    app = make_app(apple_relay_registered=True)
    with browser(app) as c:
        relay_account(c, universe)
        assert status(c)["email"]["available"] is True
        assert c.post(START).status_code == 200
        assert sent(universe)[-1]["to"] == ["abc123@privaterelay.appleid.com"]


def test_a_held_account_lock_makes_start_answer_503_and_does_not_hang(client, universe, monkeypatch):
    import time

    from vault import locks

    uid = web_account(client, universe)
    monkeypatch.setattr(locks, "LOCK_TIMEOUT", "300ms")
    with client.app.state.db.sessions() as other:
        locks.lock_account(other, uid)  # another request of this person holds the account lock
        began = time.monotonic()
        res = client.post(START)
        assert res.status_code == 503 and "Retry-After" in res.headers and time.monotonic() - began < 4
        other.rollback()
    assert client.post(START).status_code == 200
    assert len(sent(universe)) == 1


def test_a_held_vault_wide_cap_lock_does_not_stall_an_ask_for_ever(client, universe, monkeypatch):
    import time

    from vault import locks

    web_account(client, universe)
    monkeypatch.setattr(locks, "LOCK_TIMEOUT", "300ms")
    with client.app.state.db.sessions() as other:
        other.execute(select(func.pg_advisory_xact_lock(recent_signin.CAP_LOCK)))
        began = time.monotonic()
        res = client.post(START)
        assert res.status_code == 503 and time.monotonic() - began < 4
        other.rollback()
    assert client.post(START).status_code == 200
