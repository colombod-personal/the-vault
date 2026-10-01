"""Native-app authentication: SDK ID tokens, bearer tokens, refresh rotation, PKCE handoff."""

import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from joserfc.jwk import RSAKey

from twins import Universe
from vault.app import create_app
from vault.config import Settings
from vault.tokens import s256

BUNDLE = "com.example.vault.ios"
APPLE_KEY = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
GOOGLE_IOS = "ios-client.apps.googleusercontent.com"
V1 = "/api/v1"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path}/native.db", session_secret="test", base_url="http://testserver",
        google_client_id="google-web", google_client_secret="x", apple_app_bundle_id=BUNDLE,
        google_ios_client_id=GOOGLE_IOS, app_redirect_uris=("vault://auth",),
    )


@pytest.fixture
def idp(settings):
    """The twin universe (named for what these tests use it as: the identity providers)."""
    universe = Universe()
    universe.register_vault(settings)
    yield universe
    assert not universe.escapes


@pytest.fixture
def app(settings, idp):
    return create_app(settings, serve_static=False, transport=idp.transport)


def web_callback(client, cb):
    return client.get(cb.url.replace("http://testserver", ""), follow_redirects=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def apple_sign_in(client, idp, sub="a-1", **extra):
    raw = secrets.token_urlsafe(16)
    idp.apple.add_account(sub, "cy@privaterelay.appleid.com")
    token = idp.apple.native_id_token(BUNDLE, sub, nonce=hashlib.sha256(raw.encode()).hexdigest())
    return client.post(f"{V1}/auth/native/apple", json={"id_token": token, "nonce": raw, **extra})


def test_auth_discovery(client):
    info = client.get(f"{V1}/auth").json()
    assert info["native_providers"] == ["apple", "google"] and info["app_redirect_uris"] == ["vault://auth"]
    assert {"native", "browser", "token", "revoke"} <= set(info["_links"])


def test_native_apple_sign_in_and_bearer_access(client, idp):
    res = apple_sign_in(client, idp, name="Cy Doe", device_name="Cy's iPhone")
    assert res.status_code == 200
    tokens = res.json()
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600 and tokens["refresh_token"]
    assert not client.cookies  # native sign-in never creates a browser session
    me = client.get(f"{V1}/me", headers=bearer(tokens)).json()
    assert (me["name"], me["providers"]) == ("Cy Doe", ["apple"])
    # bearer requests can write (no browser, no Origin, so no CSRF concern)
    csv = b'"sep=,"\r\nFolder Name,Quantity,Card Name\r\nx,2,Sol Ring\r\n'
    assert client.post(f"{V1}/imports", files={"file": ("a.csv", csv, "text/csv")}, headers=bearer(tokens)).status_code == 201
    assert client.get(f"{V1}/collection", headers=bearer(tokens)).json()["copies"] == 2
    # the same Apple account later: same user, name kept
    again = apple_sign_in(client, idp).json()
    assert client.get(f"{V1}/me", headers=bearer(again)).json()["id"] == me["id"]


def test_native_google(client, idp):
    raw = secrets.token_urlsafe(24)
    token = idp.google.native_id_token(GOOGLE_IOS, idp.google.add_account("g-1", "ann@gmail.com", "Ann"), nonce=raw)
    tokens = client.post(f"{V1}/auth/native/google", json={"id_token": token, "nonce": raw}).json()
    assert client.get(f"{V1}/me", headers=bearer(tokens)).json()["email"] == "ann@gmail.com"


@pytest.mark.parametrize("case", ["wrong_audience", "expired", "wrong_nonce", "forged", "wrong_issuer", "not_configured"])
def test_native_tokens_are_verified(client, idp, case):
    raw = "n0nce-value-for-this-test"
    kwargs = {"nonce": hashlib.sha256(raw.encode()).hexdigest()}
    provider, aud = "apple", BUNDLE
    if case == "wrong_audience":
        aud = "com.someone.else"
    elif case == "expired":
        kwargs["exp"] = int(time.time()) - 3600
    elif case == "wrong_nonce":
        kwargs["nonce"] = hashlib.sha256(b"other").hexdigest()
    elif case == "forged":  # signed by someone else, claiming Apple's key id
        kwargs["key"] = RSAKey.generate_key(2048, parameters={"kid": idp.apple.keys[0].kid})
    elif case == "wrong_issuer":
        kwargs["iss"] = "https://evil.example"
    token = idp.apple.native_id_token(aud, "a-9", **kwargs)
    url = f"{V1}/auth/native/{'microsoft' if case == 'not_configured' else provider}"
    res = client.post(url, json={"id_token": token, "nonce": raw})
    assert res.status_code == 401 and res.headers["content-type"].startswith("application/problem+json")


def test_refresh_rotation_and_reuse_detection(client, idp):
    first = apple_sign_in(client, idp).json()
    second = client.post(f"{V1}/auth/token", json={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]}).json()
    assert second["refresh_token"] != first["refresh_token"] and second["session_id"] == first["session_id"]
    assert client.get(f"{V1}/me", headers=bearer(first)).status_code == 401  # old access token replaced
    assert client.get(f"{V1}/me", headers=bearer(second)).status_code == 200
    # someone replays the old refresh token: the whole session is revoked
    replay = client.post(f"{V1}/auth/token", json={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]})
    assert replay.status_code == 400 and "reuse" in replay.json()["detail"]
    assert client.get(f"{V1}/me", headers=bearer(second)).status_code == 401
    res = client.post(f"{V1}/auth/token", json={"grant_type": "refresh_token", "refresh_token": second["refresh_token"]})
    assert res.status_code == 400


def test_an_expired_old_refresh_token_does_not_end_the_session(client, idp):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from vault.models import RetiredRefreshToken
    first = apple_sign_in(client, idp).json()
    second = client.post(f"{V1}/auth/token", json={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]}).json()
    with client.app.state.db.sessions() as db:  # the old token's own lifetime has run out
        db.execute(update(RetiredRefreshToken).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()
    stale = client.post(f"{V1}/auth/token", json={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]})
    assert stale.status_code == 400 and "reuse" not in stale.json()["detail"]
    assert client.get(f"{V1}/me", headers=bearer(second)).status_code == 200  # the session lives on
    with client.app.state.db.sessions() as db:
        assert db.query(RetiredRefreshToken).count() == 0  # and the expired row is gone


def test_sessions_list_remote_sign_out_and_revoke(client, idp):
    phone = apple_sign_in(client, idp, device_name="iPhone").json()
    ipad = apple_sign_in(client, idp, device_name="iPad").json()
    listed = client.get(f"{V1}/me/sessions", headers=bearer(phone)).json()["items"]
    assert {s["device_name"] for s in listed} == {"iPhone", "iPad"}
    assert [s["device_name"] for s in listed if s["current"]] == ["iPhone"]
    ipad_id = next(s["id"] for s in listed if s["device_name"] == "iPad")
    assert client.delete(f"{V1}/me/sessions/{ipad_id}", headers=bearer(phone)).json() == {"deleted": True}
    assert client.get(f"{V1}/me", headers=bearer(ipad)).status_code == 401
    assert client.post(f"{V1}/auth/revoke", headers=bearer(phone)).json() == {"revoked": True}
    res = client.get(f"{V1}/me", headers=bearer(phone))
    assert res.status_code == 401 and "invalid_token" in res.headers["www-authenticate"]


def test_browser_sign_in_handed_to_the_app_with_pkce(client, idp):
    verifier = secrets.token_urlsafe(48)
    res = client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth", "code_challenge": s256(verifier),
                                                       "code_challenge_method": "S256"}, follow_redirects=False)
    back = web_callback(client, idp.google.approve(res.headers["location"], "g-2", email="bo@gmail.com", name="Bo"))
    assert back.status_code == 303 and back.headers["location"].startswith("vault://auth?code=")
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]

    def redeem(**overrides):
        body = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                "redirect_uri": "vault://auth", "device_name": "iPhone"} | overrides
        return client.post(f"{V1}/auth/token", json=body)

    assert redeem(code_verifier="wrong").status_code == 400  # the code is burned by any attempt
    # start again and redeem properly
    res = client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth", "code_challenge": s256(verifier)},
                     follow_redirects=False)
    back = web_callback(client, idp.google.approve(res.headers["location"], "g-2"))
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
    tokens = redeem().json()
    assert client.get(f"{V1}/me", headers=bearer(tokens)).json()["providers"] == ["google"]
    assert redeem().status_code == 400  # single use


def test_app_handoff_rejects_unknown_redirects_and_missing_pkce(client):
    ok = s256(secrets.token_urlsafe(48))
    assert client.get("/api/auth/login/google", params={"app_redirect_uri": "https://evil.example/cb",
                                                        "code_challenge": ok}).status_code == 400
    assert client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth"}).status_code == 400
    assert client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth", "code_challenge": ok,
                                                        "code_challenge_method": "plain"}).status_code == 400
    for bad in ("a" * 129, "a" * 42, "not/base64url+chars" + "a" * 30):  # RFC 7636: 43-128 base64url characters
        assert client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth",
                                                            "code_challenge": bad}).status_code == 400, bad


def test_cancelled_app_sign_in_returns_error_to_the_app(client, idp):
    res = client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth",
                                                       "code_challenge": s256("v" * 50)}, follow_redirects=False)
    back = web_callback(client, idp.google.deny(res.headers["location"]))
    assert back.headers["location"].startswith("vault://auth?error=")


def test_native_sign_in_while_signed_in_on_the_web_links_accounts(client, idp):
    res = client.get("/api/auth/login/google", follow_redirects=False)
    web_callback(client, idp.google.approve(res.headers["location"], "g-3"))
    web_id = client.get(f"{V1}/me").json()["id"]
    tokens = apple_sign_in(client, idp, sub="a-3").json()  # cookie present -> link to the web account
    me = client.get(f"{V1}/me", headers=bearer(tokens)).json()
    assert me["id"] == web_id and me["providers"] == ["apple", "google"]


def test_deleting_the_account_ends_app_sessions(client, idp):
    tokens = apple_sign_in(client, idp).json()
    res = client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}, headers=bearer(tokens))
    assert res.json()["removed"]["api_sessions"] == 1
    assert client.get(f"{V1}/me", headers=bearer(tokens)).status_code == 401


def test_provider_key_rotation(client, idp):
    """Apple rotates its signing keys: the vault refetches them instead of refusing sign-ins."""
    assert apple_sign_in(client, idp).status_code == 200  # keys now cached
    idp.apple.rotate_keys(keep_old=False)
    assert apple_sign_in(client, idp).status_code == 200
    assert sum(c.path == "/auth/keys" for c in idp.apple.calls) == 2


def test_provider_outage_is_a_503_the_app_can_retry(client, idp):
    idp.apple.outage = True
    res = apple_sign_in(client, idp)
    assert res.status_code == 503 and res.headers["content-type"].startswith("application/problem+json")
    idp.apple.outage = False
    assert apple_sign_in(client, idp).status_code == 200


def refresh_with(client, tokens):
    return client.post(f"{V1}/auth/token", json={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})


def test_any_already_rotated_refresh_token_revokes_the_session(client, idp):
    first = apple_sign_in(client, idp).json()
    second = refresh_with(client, first).json()
    third = refresh_with(client, second).json()  # two rotations: `first` is two generations old
    replay = refresh_with(client, first)
    assert replay.status_code == 400 and "reuse" in replay.json()["detail"]
    assert client.get(f"{V1}/me", headers=bearer(third)).status_code == 401
    assert refresh_with(client, third).status_code == 400


def test_racing_refreshes_rotate_once(app, client, idp, monkeypatch):
    from vault import tokens

    first = apple_sign_in(client, idp).json()
    real, winner = tokens._new_pair, {}

    def other_request_wins_meanwhile():
        monkeypatch.setattr(tokens, "_new_pair", real)
        with app.state.db.sessions() as other:
            winner.update(tokens.refresh(other, first["refresh_token"]))
        return real()

    monkeypatch.setattr(tokens, "_new_pair", other_request_wins_meanwhile)
    with app.state.db.sessions() as db, pytest.raises(tokens.TokenError, match="just used"):
        tokens.refresh(db, first["refresh_token"])
    assert client.get(f"{V1}/me", headers=bearer(winner)).status_code == 200  # the winner's pair is intact
    assert refresh_with(client, winner).status_code == 200


def test_linking_never_switches_accounts(client, idp):
    with TestClient(client.app) as other:  # a-4 belongs to someone else's account
        assert apple_sign_in(other, idp, sub="a-4").status_code == 200
    res = client.get("/api/auth/login/google", follow_redirects=False)
    web_callback(client, idp.google.approve(res.headers["location"], "g-4"))
    mine = client.get(f"{V1}/me").json()["id"]

    assert apple_sign_in(client, idp, sub="a-4").status_code == 409  # native, while signed in
    with TestClient(client.app) as other:  # g-5 belongs to someone else's account
        res = other.get("/api/auth/login/google", follow_redirects=False)
        web_callback(other, idp.google.approve(res.headers["location"], "g-5"))
    res = client.get("/api/auth/login/google", follow_redirects=False)  # "Link Google" with g-5
    back = web_callback(client, idp.google.approve(res.headers["location"], "g-5"))
    assert back.headers["location"] == "/?link_error=identity_in_use"
    me = client.get(f"{V1}/me").json()
    assert me["id"] == mine and me["providers"] == ["google"]


def test_personal_access_tokens_cannot_link_sign_ins(client, idp):
    res = client.get("/api/auth/login/google", follow_redirects=False)
    web_callback(client, idp.google.approve(res.headers["location"], "g-6"))
    pat = client.post(f"{V1}/me/tokens", json={"name": "bot", "scopes": ["read", "write"]}).json()["token"]
    with TestClient(client.app) as agent:
        raw = secrets.token_urlsafe(16)
        idp.apple.add_account("a-6", "x@privaterelay.appleid.com")
        token = idp.apple.native_id_token(BUNDLE, "a-6", nonce=hashlib.sha256(raw.encode()).hexdigest())
        tokens = agent.post(f"{V1}/auth/native/apple", json={"id_token": token, "nonce": raw},
                            headers={"Authorization": f"Bearer {pat}"}).json()
        assert agent.get(f"{V1}/me", headers=bearer(tokens)).json()["providers"] == ["apple"]  # its own account
    assert client.get(f"{V1}/me").json()["providers"] == ["google"]


def test_racing_code_redemptions_issue_one_session(app, client, idp):
    from vault import tokens

    verifier = secrets.token_urlsafe(48)
    res = client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth", "code_challenge": s256(verifier)},
                     follow_redirects=False)
    back = web_callback(client, idp.google.approve(res.headers["location"], "g-9"))
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
    with app.state.db.sessions() as db:
        real_scalar, raced = db.scalar, []

        def scalar(stmt):  # the other request redeems the code right after this one has read it
            row = real_scalar(stmt)
            if not raced:
                raced.append(True)
                assert client.post(f"{V1}/auth/token", json={"grant_type": "authorization_code", "code": code,
                                   "code_verifier": verifier, "redirect_uri": "vault://auth"}).status_code == 200
            return row

        db.scalar = scalar
        with pytest.raises(tokens.TokenError, match="already used"):
            tokens.redeem_code(db, code, verifier, "vault://auth", None)


@pytest.mark.parametrize("provider, aud, azp, ok", [
    ("google", GOOGLE_IOS, GOOGLE_IOS, True),  # Google Sign-In for iOS
    ("google", "google-web", GOOGLE_IOS, True),  # iOS SDK with the server client id: aud web, azp the iOS app
    ("google", "google-web", "google-web", False),  # a token from the web sign-in
    ("apple", "com.example.vault.web", None, False),  # the web Services ID
])
def test_native_endpoint_takes_only_tokens_made_for_the_app(tmp_path, provider, aud, azp, ok):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/aud.db", session_secret="test", base_url="http://testserver",
        google_client_id="google-web", google_client_secret="x", google_ios_client_id=GOOGLE_IOS,
        apple_client_id="com.example.vault.web", apple_team_id="T", apple_key_id="K", apple_private_key=APPLE_KEY,
        apple_app_bundle_id=BUNDLE)
    universe = Universe()
    universe.register_vault(settings)
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as c:
        twin = universe.twins[provider]
        extra = {"azp": azp} if azp else {}
        raw = secrets.token_urlsafe(24)
        in_token = hashlib.sha256(raw.encode()).hexdigest() if provider == "apple" else raw
        token = twin.native_id_token(aud, f"{provider[0]}-aud", nonce=in_token, **extra)
        res = c.post(f"{V1}/auth/native/{provider}", json={"id_token": token, "nonce": raw})
        assert (res.status_code == 200) is ok, res.text


def test_bad_tokens_do_not_make_the_vault_refetch_provider_keys(client, idp):
    """Only a token signed with a key id the Vault hasn't seen triggers a key refresh, and at
    most once a minute: a flood of forged tokens can't turn into calls to Apple."""
    assert apple_sign_in(client, idp).status_code == 200  # keys cached
    keys = lambda: sum(c.path == "/auth/keys" for c in idp.apple.calls)  # noqa: E731
    before = keys()
    forger = RSAKey.generate_key(2048, parameters={"kid": idp.apple.keys[0].kid})
    for _ in range(5):  # known key id, wrong signature
        token = idp.apple.native_id_token(BUNDLE, "a-x", key=forger)
        assert client.post(f"{V1}/auth/native/apple", json={"id_token": token, "nonce": "n" * 24}).status_code == 401
    assert keys() == before
    for i in range(5):  # unknown key ids
        stranger = RSAKey.generate_key(2048, parameters={"kid": f"unknown-{i}"})
        token = idp.apple.native_id_token(BUNDLE, "a-x", key=stranger)
        assert client.post(f"{V1}/auth/native/apple", json={"id_token": token, "nonce": "n" * 24}).status_code == 401
    assert keys() == before + 1  # one refresh, then the cooldown


@pytest.mark.parametrize("aud, azp, ok", [
    ([GOOGLE_IOS, "someone-else"], GOOGLE_IOS, True),  # several audiences, issued to our app
    ([GOOGLE_IOS, "someone-else"], "someone-else", False),  # several audiences, issued to another party
    ([GOOGLE_IOS, "someone-else"], None, False),  # several audiences and no authorized party
])
def test_google_tokens_with_several_audiences_must_be_issued_to_the_app(client, idp, aud, azp, ok):
    raw = secrets.token_urlsafe(24)
    claims = {**_claims(idp.google.native_id_token(GOOGLE_IOS, f"g-multi-{ok}", nonce=raw)), "aud": aud, "azp": azp}
    token = idp.google.sign({k: v for k, v in claims.items() if v is not None})
    res = client.post(f"{V1}/auth/native/google", json={"id_token": token, "nonce": raw})
    assert (res.status_code == 200) is ok, res.text


def _claims(token):
    import base64 as b64, json as js
    part = token.split(".")[1]
    return js.loads(b64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def test_a_google_token_without_an_audience_is_refused_when_no_server_client_is_set(tmp_path):
    """Only the iOS client configured (no web client): a token with no `aud` must not slip
    through the server-client path as `None in {None}`."""
    settings = Settings(database_url=f"sqlite:///{tmp_path}/noaud.db", session_secret="test",
                        base_url="http://testserver", google_ios_client_id=GOOGLE_IOS)
    universe = Universe()
    universe.register_vault(settings)
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as c:
        claims = _claims(universe.google.native_id_token(GOOGLE_IOS, "g-noaud"))
        claims.pop("aud")
        claims["azp"] = GOOGLE_IOS
        res = c.post(f"{V1}/auth/native/google", json={"id_token": universe.google.sign(claims), "nonce": claims.get("nonce") or "n" * 24})
        assert res.status_code == 401, res.text


def test_a_native_sign_in_needs_a_nonce_and_each_token_works_once(client, idp):
    """Without a nonce a copied ID token could be replayed until it expires: the nonce is
    required, and a token whose nonce was already used is refused."""
    token = idp.google.native_id_token(GOOGLE_IOS, idp.google.add_account("g-once", "o@gmail.com", "O"))
    assert client.post(f"{V1}/auth/native/google", json={"id_token": token}).status_code in (401, 422)
    raw = secrets.token_urlsafe(24)
    token = idp.google.native_id_token(GOOGLE_IOS, "g-once", nonce=raw)
    first = client.post(f"{V1}/auth/native/google", json={"id_token": token, "nonce": raw})
    assert first.status_code == 200, first.text
    replay = client.post(f"{V1}/auth/native/google", json={"id_token": token, "nonce": raw})
    assert replay.status_code == 401 and "already used" in replay.json()["detail"]
    assert apple_sign_in(client, idp).status_code == 200  # Apple: SHA-256 of the raw nonce in the token


def test_a_native_name_longer_than_an_account_name_is_invalid(client, idp):
    assert apple_sign_in(client, idp, name="N" * 201).status_code == 422
    assert apple_sign_in(client, idp, name="N" * 200).status_code == 200
