"""Native-app authentication: SDK ID tokens, bearer tokens, refresh rotation, PKCE handoff."""

import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from joserfc.jwk import RSAKey

from fake_idp import FakeIdP
from vault.app import create_app
from vault.config import Settings
from vault.tokens import s256

BUNDLE = "com.example.vault.ios"
GOOGLE_IOS = "ios-client.apps.googleusercontent.com"
V1 = "/api/v1"


@pytest.fixture
def idp():
    return FakeIdP()


@pytest.fixture
def app(tmp_path, idp):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/native.db", session_secret="test", base_url="http://testserver",
        google_client_id="google-web", google_client_secret="x", apple_app_bundle_id=BUNDLE,
        google_ios_client_id=GOOGLE_IOS, app_redirect_uris=("vault://auth",),
    )
    return create_app(settings, serve_static=False, auth_transport=idp.transport)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def apple_sign_in(client, idp, sub="a-1", **extra):
    raw = secrets.token_urlsafe(16)
    token = idp.native_id_token("apple", sub=sub, aud=BUNDLE, nonce=hashlib.sha256(raw.encode()).hexdigest(),
                                email="cy@privaterelay.appleid.com")
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
    token = idp.native_id_token("google", sub="g-1", aud=GOOGLE_IOS, email="ann@gmail.com", name="Ann")
    tokens = client.post(f"{V1}/auth/native/google", json={"id_token": token}).json()
    assert client.get(f"{V1}/me", headers=bearer(tokens)).json()["email"] == "ann@gmail.com"


@pytest.mark.parametrize("case", ["wrong_audience", "expired", "wrong_nonce", "forged", "wrong_issuer", "not_configured"])
def test_native_tokens_are_verified(client, idp, case):
    raw = "n0nce-value"
    kwargs = {"sub": "a-9", "aud": BUNDLE, "nonce": hashlib.sha256(raw.encode()).hexdigest()}
    provider = "apple"
    if case == "wrong_audience":
        kwargs["aud"] = "com.someone.else"
    elif case == "expired":
        kwargs["exp"] = int(time.time()) - 3600
    elif case == "wrong_nonce":
        kwargs["nonce"] = hashlib.sha256(b"other").hexdigest()
    elif case == "forged":
        kwargs["key"] = RSAKey.generate_key(2048, parameters={"kid": "fake-1"})
    elif case == "wrong_issuer":
        kwargs["iss"] = "https://evil.example"
    token = idp.native_id_token(provider, **kwargs)
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
    answer = idp.authorize(res.headers["location"], sub="g-2", email="bo@gmail.com", name="Bo")
    back = client.get("/api/auth/callback/google", params=answer, follow_redirects=False)
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
    back = client.get("/api/auth/callback/google", params=idp.authorize(res.headers["location"], sub="g-2"),
                      follow_redirects=False)
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


def test_cancelled_app_sign_in_returns_error_to_the_app(client, idp):
    res = client.get("/api/auth/login/google", params={"app_redirect_uri": "vault://auth",
                                                       "code_challenge": s256("v" * 50)}, follow_redirects=False)
    state = parse_qs(urlsplit(res.headers["location"]).query)["state"][0]
    back = client.get("/api/auth/callback/google", params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert back.headers["location"].startswith("vault://auth?error=")


def test_native_sign_in_while_signed_in_on_the_web_links_accounts(client, idp):
    res = client.get("/api/auth/login/google", follow_redirects=False)
    client.get("/api/auth/callback/google", params=idp.authorize(res.headers["location"], sub="g-3"), follow_redirects=False)
    web_id = client.get(f"{V1}/me").json()["id"]
    tokens = apple_sign_in(client, idp, sub="a-3").json()  # cookie present -> link to the web account
    me = client.get(f"{V1}/me", headers=bearer(tokens)).json()
    assert me["id"] == web_id and me["providers"] == ["apple", "google"]


def test_deleting_the_account_ends_app_sessions(client, idp):
    tokens = apple_sign_in(client, idp).json()
    res = client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}, headers=bearer(tokens))
    assert res.json()["removed"]["api_sessions"] == 1
    assert client.get(f"{V1}/me", headers=bearer(tokens)).status_code == 401
