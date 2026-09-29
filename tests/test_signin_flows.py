"""End-to-end web sign-in with Google, Microsoft, Apple and Facebook, against their twins.

The app runs unchanged. Its calls to the providers go to the twin universe, which checks what
the real providers check and answers the way they do. This covers:
- redirect URI registration and single-use codes
- Apple's form_post, its ES256 client secret, and a name that arrives only once
- Microsoft's tenant issuer and missing ``email`` claim
- Facebook users who decline to share their e-mail
Real-credential checks are listed in README ("Testing sign-in").
"""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from twins import Callback, Universe
from vault.app import create_app
from vault.config import Settings


def new_key() -> str:
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


APPLE_KEY = new_key()


def vault_settings(tmp_path, **overrides):
    return Settings(**{
        "database_url": f"sqlite:///{tmp_path}/auth.db", "session_secret": "test", "base_url": "http://testserver",
        "google_client_id": "google-app", "google_client_secret": "g-secret",
        "microsoft_client_id": "ms-app", "microsoft_client_secret": "m-secret",
        "facebook_client_id": "fb-app", "facebook_client_secret": "f-secret",
        "apple_client_id": "com.example.vault", "apple_team_id": "TEAM1234", "apple_key_id": "KEY1234",
        "apple_private_key": APPLE_KEY, **overrides,
    })


@pytest.fixture
def universe(tmp_path):
    u = Universe()
    u.register_vault(vault_settings(tmp_path))
    yield u
    assert not u.escapes, f"calls left the twin universe: {u.escapes}"


@pytest.fixture
def client(tmp_path, universe):
    with TestClient(create_app(vault_settings(tmp_path), serve_static=False, transport=universe.transport)) as c:
        yield c


def start(client, provider, **params):
    res = client.get(f"/api/auth/login/{provider}", params=params, follow_redirects=False)
    assert res.status_code == 302, res.text
    return res.headers["location"]


def deliver(client, cb: Callback) -> str:
    """The browser follows the provider's answer back to the app."""
    if cb.method == "POST":  # Apple's form_post, submitted from appleid.apple.com
        res = client.post(cb.url.replace("http://testserver", ""), data=cb.data,
                          headers={"Origin": "https://appleid.apple.com"}, follow_redirects=False)
    else:
        res = client.get(cb.url.replace("http://testserver", ""), follow_redirects=False)
    assert res.status_code == 303, res.text
    return res.headers["location"]


def sign_in(client, twin, account, **kwargs) -> str:
    return deliver(client, twin.approve(start(client, twin.name), account, **kwargs))


def me(client):
    return client.get("/api/v1/me").json()


def test_only_configured_providers_are_offered(client):
    assert client.get("/api/auth/providers").json()["providers"] == ["google", "microsoft", "apple", "facebook"]


def test_google(client, universe):
    google = universe.google
    location = start(client, "google")
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    ann = google.add_account("g-1", "ann@gmail.com", "Ann Smith")
    assert deliver(client, google.approve(location, ann)) == "/"
    first = me(client)
    assert (first["email"], first["name"], first["providers"]) == ("ann@gmail.com", "Ann Smith", ["google"])
    token_call = next(c for c in google.calls if c.path == "/token")
    assert token_call.form["redirect_uri"] == "http://testserver/api/auth/callback/google"

    client.cookies.clear()  # a later sign-in with the same account reopens the same vault
    sign_in(client, google, "g-1")
    assert me(client)["id"] == first["id"]


def test_google_consent_page(client, universe):
    """The provider's own page, as a person (or Playwright) would use it."""
    universe.google.add_account("g-2", "cy@gmail.com", "Cy")
    http = universe.client()
    page = http.get(start(client, "google"))
    assert page.status_code == 200 and "Sign in with Google" in page.text and "cy@gmail.com" in page.text
    fields = dict(__import__("re").findall(r'name="(q_[^"]+)" value="([^"]*)"', page.text))
    answer = http.post(str(page.url), data={**fields, "account": "g-2"}, follow_redirects=False)
    assert answer.status_code == 302
    assert deliver(client, Callback(answer.headers["location"])) == "/" and me(client)["email"] == "cy@gmail.com"


def test_microsoft_issuer_and_missing_email_claim(client, universe):
    ms = universe.microsoft
    # Microsoft leaves "email" out of ID tokens unless the app opts in; preferred_username has it.
    assert sign_in(client, ms, ms.add_account("m-1", "bo@outlook.com", "Bo")) == "/"
    assert (me(client)["email"], me(client)["providers"]) == ("bo@outlook.com", ["microsoft"])

    client.cookies.clear()
    forged = {"iss": "https://login.microsoftonline.com/some-other-tenant/v2.0"}
    assert sign_in(client, ms, "m-2", claims=forged).startswith("/?signin_error=")
    assert client.get("/api/v1/me").status_code == 401


def test_work_account_from_another_tenant(client, universe):
    ms = universe.microsoft
    ms.add_account("m-3", "dee@contoso.com", "Dee", tenant="72f988bf-86f1-41af-91ab-2d7cd011db47")
    assert sign_in(client, ms, "m-3") == "/" and me(client)["email"] == "dee@contoso.com"


def test_apple_form_post_signed_secret_and_one_time_name(client, universe):
    apple = universe.apple
    location = start(client, "apple")
    cy = apple.add_account("a-1", "cy@icloud.com", "Cy Doe", hide_email=True)
    cb = apple.approve(location, cy)
    assert cb.method == "POST" and json.loads(cb.data["user"])["name"] == {"firstName": "Cy", "lastName": "Doe"}
    assert deliver(client, cb) == "/"
    profile = me(client)
    assert profile["name"] == "Cy Doe" and profile["email"].endswith("@privaterelay.appleid.com")

    client.cookies.clear()  # Apple sends the name only the first time; the account keeps it
    cb = apple.approve(start(client, "apple"), "a-1")
    assert "user" not in cb.data
    deliver(client, cb)
    assert me(client)["name"] == "Cy Doe"


def test_apple_refuses_a_client_secret_signed_with_the_wrong_key(tmp_path, universe):
    settings = vault_settings(tmp_path, apple_private_key=new_key())  # not the key registered with Apple
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as c:
        assert sign_in(c, universe.apple, "a-5").startswith("/?signin_error=")
        assert c.get("/api/v1/me").status_code == 401
    assert universe.apple.calls[-1].status == 400


def test_facebook(client, universe):
    fb = universe.facebook
    location = start(client, "facebook")
    assert location.startswith("https://www.facebook.com/v23.0/dialog/oauth")
    assert sign_in(client, fb, fb.add_account("f-1", "dee@fb.test", "Dee")) == "/"
    assert (me(client)["email"], me(client)["name"], me(client)["providers"]) == ("dee@fb.test", "Dee", ["facebook"])


def test_facebook_user_who_declines_the_email_permission(client, universe):
    fb = universe.facebook
    sign_in(client, fb, fb.add_account("f-2", "eve@fb.test", "Eve", share_email=False))
    assert (me(client)["name"], me(client)["email"]) == ("Eve", None)


def test_signing_in_with_a_second_provider_links_it(client, universe):
    sign_in(client, universe.google, universe.google.add_account("g-9", "eve@gmail.com", "Eve"))
    first = me(client)["id"]
    sign_in(client, universe.facebook, universe.facebook.add_account("f-9", "eve@fb.test", "Eve"))
    assert me(client)["id"] == first and me(client)["providers"] == ["facebook", "google"]
    client.cookies.clear()  # either provider now opens the same account
    sign_in(client, universe.facebook, "f-9")
    assert me(client)["id"] == first


def test_same_email_on_another_provider_is_a_separate_account(client, universe):
    sign_in(client, universe.google, universe.google.add_account("g-5", "sam@example.com", "Sam"))
    first = me(client)["id"]
    client.cookies.clear()
    sign_in(client, universe.microsoft, universe.microsoft.add_account("m-5", "sam@example.com", "Sam"))
    assert me(client)["id"] != first  # never merged by e-mail


@pytest.mark.parametrize("provider", ["google", "microsoft", "apple", "facebook"])
def test_user_cancels(client, universe, provider):
    twin = universe.twins[provider]
    location = deliver(client, twin.deny(start(client, provider)))
    assert location == f"/?signin_error=access_denied&provider={provider}"  # the sign-in screen names both
    assert client.get("/api/v1/me").status_code == 401


def test_replayed_or_forged_callbacks_are_rejected(client, universe):
    google = universe.google
    cb = google.approve(start(client, "google"), "g-7")
    assert deliver(client, cb) == "/"
    client.cookies.clear()
    assert deliver(client, cb).startswith("/?signin_error=")  # same answer again
    assert sign_in(client, google, "g-8", claims={"nonce": "not-ours"}).startswith("/?signin_error=")
    assert sign_in(client, google, "g-8", claims={"aud": "someone-else"}).startswith("/?signin_error=")
    assert client.get("/api/v1/me").status_code == 401


def test_code_is_single_use_at_the_provider(client, universe):
    cb = universe.google.approve(start(client, "google"), "g-6")
    deliver(client, cb)
    # the app never redeems a code twice; if it did, the provider would refuse
    replay = universe.client().post("https://oauth2.googleapis.com/token", data={
        "grant_type": "authorization_code", "code": cb.params["code"], "client_id": "google-app",
        "client_secret": "g-secret", "redirect_uri": "http://testserver/api/auth/callback/google"})
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"


def test_wrong_base_url_is_caught_by_the_provider(tmp_path, universe):
    """A deployment whose BASE_URL doesn't match the provider console fails at the provider,
    as it would in production."""
    settings = vault_settings(tmp_path, base_url="http://wrong.example")
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as c:
        location = start(c, "google")
    page = universe.client().get(location)
    assert page.status_code == 400 and "redirect_uri_mismatch" in page.text
    with pytest.raises(AssertionError, match="redirect_uri_mismatch"):
        universe.google.approve(location, "g-1")


def test_provider_outage_and_errors_end_in_a_sign_in_error(client, universe):
    universe.google.outage = True
    res = client.get("/api/auth/login/google", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/?signin_error=temporarily_unavailable&provider=google"
    universe.google.outage = False
    location = start(client, "google")
    universe.google.fail_next("/token", 503)
    assert deliver(client, universe.google.approve(location, "g-4")).startswith("/?signin_error=")
    assert client.get("/api/v1/me").status_code == 401
