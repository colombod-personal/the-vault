"""End-to-end sign-in with Google, Microsoft, Apple and Facebook against a fake provider.

Runs the real redirect -> provider -> callback -> session flow for each provider,
including the provider-specific parts: Microsoft's per-tenant issuer, Apple's
form_post callback, signed client secret and one-time name, and Facebook's Graph
profile. Real-credential checks are listed in README ("Testing sign-in").
"""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import ECKey

from fake_idp import FakeIdP
from vault.app import create_app
from vault.config import Settings

APPLE_KEY = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


@pytest.fixture
def idp():
    return FakeIdP()


@pytest.fixture
def client(tmp_path, idp):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/auth.db", session_secret="test", base_url="http://testserver",
        google_client_id="google-app", google_client_secret="g-secret",
        microsoft_client_id="ms-app", microsoft_client_secret="m-secret",
        facebook_client_id="fb-app", facebook_client_secret="f-secret",
        apple_client_id="com.example.vault", apple_team_id="TEAM1234", apple_key_id="KEY1234",
        apple_private_key=APPLE_KEY,
    )
    with TestClient(create_app(settings, serve_static=False, auth_transport=idp.transport)) as c:
        yield c


def start(client, provider):
    res = client.get(f"/api/auth/login/{provider}", follow_redirects=False)
    assert res.status_code == 302, res.text
    location = res.headers["location"]
    return location, {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


def finish(client, provider, answer, *, post=False, **extra):
    url = f"/api/auth/callback/{provider}"
    if post:  # Apple's form_post, sent by the browser from appleid.apple.com
        res = client.post(url, data={**answer, **extra}, headers={"Origin": "https://appleid.apple.com"},
                          follow_redirects=False)
    else:
        res = client.get(url, params={**answer, **extra}, follow_redirects=False)
    assert res.status_code == 303, res.text
    return res.headers["location"]


def sign_in(client, idp, provider, sub, email=None, name=None, **kwargs):
    location, _ = start(client, provider)
    answer = idp.authorize(location, sub=sub, email=email, name=name, claims=kwargs.pop("claims", None))
    return finish(client, provider, answer, post=(provider == "apple"), **kwargs)


def test_only_configured_providers_are_offered(client):
    assert client.get("/api/auth/providers").json()["providers"] == ["google", "microsoft", "apple", "facebook"]


def test_google(client, idp):
    location, q = start(client, "google")
    assert location.startswith("https://idp.test/google/authorize")
    assert q["client_id"] == "google-app" and q["redirect_uri"] == "http://testserver/api/auth/callback/google"
    assert set(q["scope"].split()) == {"openid", "email", "profile"} and q["nonce"]
    assert finish(client, "google", idp.authorize(location, sub="g-1", email="ann@gmail.com", name="Ann")) == "/"
    me = client.get("/api/me").json()
    assert (me["email"], me["name"], me["providers"]) == ("ann@gmail.com", "Ann", ["google"])

    # a later sign-in with the same Google account reopens the same vault
    client.cookies.clear()
    sign_in(client, idp, "google", "g-1", "ann@gmail.com", "Ann")
    assert client.get("/api/me").json()["id"] == me["id"]


def test_microsoft_checks_issuer_against_tenant(client, idp):
    assert sign_in(client, idp, "microsoft", "m-1", "bo@outlook.com", "Bo") == "/"
    assert client.get("/api/me").json()["providers"] == ["microsoft"]

    client.cookies.clear()
    forged = {"iss": "https://login.microsoftonline.com/some-other-tenant/v2.0"}
    assert sign_in(client, idp, "microsoft", "m-2", claims=forged).startswith("/?signin_error=")
    assert client.get("/api/me").status_code == 401


def test_apple_form_post_signed_secret_and_one_time_name(client, idp):
    location, q = start(client, "apple")
    assert q["response_mode"] == "form_post" and set(q["scope"].split()) == {"openid", "name", "email"}
    answer = idp.authorize(location, sub="a-1", email="x1y2@privaterelay.appleid.com")
    user_json = json.dumps({"name": {"firstName": "Cy", "lastName": "Doe"}})
    assert finish(client, "apple", answer, post=True, user=user_json) == "/"
    me = client.get("/api/me").json()
    assert (me["name"], me["email"], me["providers"]) == ("Cy Doe", "x1y2@privaterelay.appleid.com", ["apple"])

    # the client secret sent to Apple is an ES256 JWT signed with our key
    secret = idp.token_requests[-1]["client_secret"]
    public = ECKey.import_key(APPLE_KEY)
    token = jwt.decode(secret, public)
    assert token.header["kid"] == "KEY1234" and token.claims["iss"] == "TEAM1234"
    assert token.claims["sub"] == "com.example.vault" and token.claims["aud"] == "https://appleid.apple.com"

    # Apple sends the name only the first time; the account keeps it
    client.cookies.clear()
    sign_in(client, idp, "apple", "a-1")
    assert client.get("/api/me").json()["name"] == "Cy Doe"


def test_facebook(client, idp):
    location, q = start(client, "facebook")
    assert location.startswith("https://www.facebook.com/v23.0/dialog/oauth")
    assert q["client_id"] == "fb-app" and set(q["scope"].split()) == {"email", "public_profile"}
    assert finish(client, "facebook", idp.authorize(location, sub="f-1", email="dee@fb.test", name="Dee")) == "/"
    me = client.get("/api/me").json()
    assert (me["email"], me["name"], me["providers"]) == ("dee@fb.test", "Dee", ["facebook"])


def test_signing_in_with_a_second_provider_links_it(client, idp):
    sign_in(client, idp, "google", "g-9", "eve@gmail.com", "Eve")
    first = client.get("/api/me").json()["id"]
    sign_in(client, idp, "facebook", "f-9", "eve@fb.test", "Eve")  # still signed in -> link
    me = client.get("/api/me").json()
    assert me["id"] == first and me["providers"] == ["facebook", "google"]

    client.cookies.clear()  # either provider now opens the same account
    sign_in(client, idp, "facebook", "f-9")
    assert client.get("/api/me").json()["id"] == first


def test_same_email_on_another_provider_is_a_separate_account(client, idp):
    sign_in(client, idp, "google", "g-5", "sam@example.com", "Sam")
    first = client.get("/api/me").json()["id"]
    client.cookies.clear()
    sign_in(client, idp, "microsoft", "m-5", "sam@example.com", "Sam")
    assert client.get("/api/me").json()["id"] != first  # never merged by e-mail


@pytest.mark.parametrize("provider", ["google", "microsoft", "apple", "facebook"])
def test_user_cancels(client, idp, provider):
    _, q = start(client, provider)
    error = {"error": "access_denied", "state": q["state"]}
    if provider == "apple":
        res = client.post("/api/auth/callback/apple", data=error, follow_redirects=False)
    else:
        res = client.get(f"/api/auth/callback/{provider}", params=error, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"].startswith("/?signin_error=")
    assert client.get("/api/me").status_code == 401


def test_replayed_or_forged_callbacks_are_rejected(client, idp):
    location, _ = start(client, "google")
    answer = idp.authorize(location, sub="g-7")
    assert finish(client, "google", answer) == "/"
    client.cookies.clear()
    # the same code and state again: state no longer in the session, code already used
    assert finish(client, "google", answer).startswith("/?signin_error=")
    # a token whose nonce doesn't match the one we sent
    assert sign_in(client, idp, "google", "g-8", claims={"nonce": "not-ours"}).startswith("/?signin_error=")
    # a token issued for another app
    assert sign_in(client, idp, "google", "g-8", claims={"aud": "someone-else"}).startswith("/?signin_error=")
    assert client.get("/api/me").status_code == 401
