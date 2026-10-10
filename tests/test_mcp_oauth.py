"""OAuth for MCP: the happy path and the abuse cases (docs/mcp-oauth-threat-model.md).

Every test drives the Vault the way ChatGPT or Claude would, with the twin MCP client
(``twins/mcp_client.py``) and the twin universe, so nothing reaches a real service.
"""

import hashlib
import html
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from twins import Universe
from twins.mcp_client import McpClient
from vault import oauth_server
from vault.api.mcp import _marked_as_mcp
from vault.app import create_app
from vault.config import Settings
from vault.models import OAuthCode, OAuthGrant, User

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
V1 = "/api/v1"
BASE = "http://testserver"


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, session_secret="test", dev_login=True, base_url=BASE,
                    google_client_id="google-web", google_client_secret="x",
                    oauth_rate_limit=1000, oauth_register_rate_limit=1000,
                    oauth_fetch_limit=100_000, oauth_fetch_ip_limit=100_000)


@pytest.fixture
def universe(settings):
    u = Universe()
    u.register_vault(settings)
    yield u
    assert not u.escapes  # nothing tried to leave the twin universe


@pytest.fixture
def app(settings, universe):
    app = create_app(settings, serve_static=False, transport=universe.transport, resolver=universe.resolve)
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def make_client(app, universe):
    """A twin MCP client with its metadata document published (what Claude or ChatGPT would host)."""
    def make(**publish) -> McpClient:
        return McpClient(lambda: TestClient(app), universe.client_hosts.publish(**publish))

    return make


@pytest.fixture
def client(make_client):
    return make_client()


def db_do(app, fn):
    with app.state.db.sessions() as db:
        return fn(db)


def grant_of(app):
    return db_do(app, lambda db: db.scalar(select(OAuthGrant)))


def location(res):
    return urlsplit(res.headers["location"])


def query(res):
    return {k: v[-1] for k, v in parse_qs(location(res).query).items()}


def query_of(url):
    return {k: v[-1] for k, v in parse_qs(urlsplit(url).query).items()}


def returned(res):
    """What a request error sends back to the app, now as the link behind the page's "Return to ..." button (#339): the page is
    an error page without a redirect, and its link carries the same ``error`` and ``state``."""
    assert res.status_code == 400 and "location" not in res.headers
    return query_of(html.unescape(re.search(r'<a class="button" href="([^"]+)"', res.text).group(1)))


def import_csv(client):
    assert client.browser.post(f"{V1}/imports", files={"file": ("a.csv", CSV, "text/csv")}).status_code == 201


# -- discovery ---------------------------------------------------------------------------------

def test_unauthenticated_mcp_answers_401_pointing_at_the_resource_metadata(client):
    res = client.api.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert res.status_code == 401
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/api/mcp"' in res.headers["www-authenticate"]
    assert res.headers["www-authenticate"].startswith("Bearer ")


def test_resource_and_authorization_server_metadata(client):
    resource, server = client.discover()
    assert resource["resource"] == f"{BASE}/api/mcp" and resource["authorization_servers"] == [BASE]
    assert resource["scopes_supported"] == ["read", "write"]
    root = client.api.get("/.well-known/oauth-protected-resource").json()
    assert root == resource
    assert client.api.get("/.well-known/oauth-protected-resource/other").status_code == 404
    assert server["issuer"] == BASE and server["code_challenge_methods_supported"] == ["S256"]
    assert server["client_id_metadata_document_supported"] is True
    assert server["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert server["token_endpoint_auth_methods_supported"] == ["none", "private_key_jwt"]
    assert server["token_endpoint_auth_signing_alg_values_supported"] == ["RS256", "PS256", "ES256"]
    assert server["authorization_endpoint"] == f"{BASE}/oauth/authorize" and server["registration_endpoint"]
    cors = client.api.get("/.well-known/oauth-authorization-server").headers["access-control-allow-origin"]
    assert cors == "*"
    assert client.api.options("/oauth/token").status_code == 204


def test_the_openai_domain_check_answers_the_exact_token_as_plain_text(client):
    """OpenAI's plugin portal (#240): only the token, not JSON, at the origin-root well-known URL."""
    r = client.api.get("/.well-known/openai-apps-challenge")
    assert r.status_code == 200 and r.text == "0x5MnvfLbnis5EFcH-Ub17kVBdb435wzrN1b5tYCYyM"
    assert r.headers["content-type"].startswith("text/plain")


def test_invalid_and_expired_tokens_get_a_challenge_too(client):
    res = client.mcp("ping", token="vault_oat_nope")
    assert res.status_code == 401 and 'error="invalid_token"' in res.headers["www-authenticate"]
    assert "resource_metadata=" in res.headers["www-authenticate"]


# -- the happy path ----------------------------------------------------------------------------

def test_a_client_connects_and_reads_the_collection(app, client):
    client.sign_in()
    import_csv(client)
    tokens = client.connect()
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600 and tokens["scope"] == "read"
    assert tokens["access_token"].startswith("vault_oat_") and tokens["refresh_token"].startswith("vault_ort_")
    assert client.mcp("initialize", {"protocolVersion": "2025-06-18"}).status_code == 200
    names = {t["name"] for t in client.mcp("tools/list").json()["result"]["tools"]}
    assert "get_collection_summary" in names and "save_deck" not in names  # read only: no write tools listed
    summary = client.tool("get_collection_summary")["result"]
    assert summary["isError"] is False and summary["structuredContent"]["copies"] == 7


def test_the_state_and_issuer_come_back_and_the_response_is_not_cached(client):
    client.sign_in()
    page = client.authorize()
    res = client.answer(page)
    assert res.status_code == 303 and res.headers["cache-control"] == "no-store"
    back = query(res)
    assert back["state"] == client.state and back["iss"] == BASE and back["code"]
    assert location(res).geturl().startswith("https://app.example/callback?")


def test_tokens_are_stored_only_as_hashes(app, client):
    tokens = client.connect()
    grant = grant_of(app)
    stored = {grant.access_hash, grant.refresh_hash}
    assert hashlib.sha256(tokens["access_token"].encode()).hexdigest() in stored
    assert hashlib.sha256(tokens["refresh_token"].encode()).hexdigest() in stored
    with app.state.db.sessions() as db:
        code = db.scalar(select(OAuthCode))
        assert code.code_hash != "" and len(code.code_hash) == 64


def test_the_token_response_is_not_cacheable(client):
    client.sign_in()
    res = client.redeem(client.approve()["code"])
    assert res.headers["cache-control"] == "no-store" and res.headers["pragma"] == "no-cache"


def test_the_consent_screen_names_the_app_the_scopes_and_what_is_never_allowed(client):
    client.sign_in()
    page = client.authorize(scope="read write")
    text = page.text
    assert "Twin Agent" in text and "app.example" in text
    assert "Read your collection" in text and "Make changes" in text
    assert 'name="write"' in text and "checked" not in text.split('name="write"')[1].split(">")[0]  # write: opt in
    for never in ("Delete your account", "Export all your data", "Create or revoke access tokens"):
        assert never in text
    assert "will receive the answer" not in text  # an https redirect: only its host is shown
    assert "app.example" in text.split("sent back to")[1]


def test_the_consent_form_can_post_back_from_a_browser(client):
    """A browser sends ``Origin: null`` on a form POST when the page's referrer policy is
    no-referrer, and the CSRF guard refuses it: consent then fails for every real user (found by
    connecting claude.ai to production). The page must keep a policy that still sends Origin."""
    client.sign_in()
    page = client.authorize(scope="read")
    assert page.headers["referrer-policy"] == "same-origin"


def test_read_only_request_shows_no_write_option(client):
    client.sign_in()
    assert 'name="write"' not in client.authorize(scope="read").text
    assert 'name="write"' not in client.authorize(scope=None).text  # no scope asked: read only


def test_write_is_never_granted_without_the_person_choosing_it(app, client):
    client.sign_in()
    assert client.approve(scope="read write")  # allowed, write box left unticked
    tokens = client.redeem(client.approve(scope="read write")["code"]).json()
    assert tokens["scope"] == "read"
    client.tokens = tokens
    names = {t["name"] for t in client.mcp("tools/list").json()["result"]["tools"]}
    assert "save_deck" not in names
    res = client.tool("save_deck", name="x", text="1 Sol Ring")
    assert "error" in res or res["result"]["isError"]


def test_write_when_the_person_ticks_it(client):
    client.sign_in()
    code = client.approve(write=True, scope="read write")["code"]
    client.tokens = client.redeem(code).json()
    assert client.tokens["scope"] == "read write"
    names = {t["name"] for t in client.mcp("tools/list").json()["result"]["tools"]}
    assert "save_deck" in names
    saved = client.tool("save_deck", name="Ramp", text="1 Sol Ring")["result"]
    assert saved["isError"] is False


def test_denying_sends_access_denied_with_the_state(client):
    client.sign_in()
    res = client.answer(client.authorize(), decision="deny")
    assert query(res) == {"error": "access_denied", "state": client.state, "iss": BASE}


def test_personal_access_tokens_still_work_on_mcp(client):
    client.sign_in()
    made = client.browser.post(f"{V1}/me/tokens", json={"name": "bot", "scopes": ["read"]}).json()
    res = client.mcp("tools/list", token=made["token"])
    assert res.status_code == 200 and res.json()["result"]["tools"]


# -- sign-in -----------------------------------------------------------------------------------

def test_signed_out_people_get_a_sign_in_page_then_the_consent_screen(client):
    page = client.authorize()
    assert page.status_code == 200 and "Sign in to connect Twin Agent" in page.text
    assert "Google" in page.text and "continue=oauth" in page.text
    client.sign_in()  # (a passkey or provider sign-in ends the same way: a session)
    assert "Allow" in client.authorize().text


def test_a_session_that_has_not_signed_in_recently_signs_in_again_before_connecting_an_app(app, client, go_stale):
    """Connecting an app mints a 30-day credential, the same power as a personal access token, so it needs a recent sign-in (#347):
    a stale session (a copied cookie) is shown the sign-in page, and a consent screen shown earlier cannot be answered."""
    client.sign_in()
    shown_while_fresh = client.authorize()
    assert "Allow" in shown_while_fresh.text
    go_stale()
    stale = client.authorize()
    assert stale.status_code == 200 and "Sign in to connect Twin Agent" in stale.text and "Allow" not in stale.text
    answered = client.answer(shown_while_fresh)
    assert answered.status_code == 400 and "sign-in from the last few minutes" in answered.text
    assert grant_of(app) is None and db_do(app, lambda db: db.scalar(select(OAuthCode))) is None
    client.sign_in()  # signing in with a method the account has makes it recent again
    assert "Allow" in client.authorize().text


def test_a_provider_sign_in_returns_to_the_pending_request(client, universe):
    client.authorize()  # signed out: the request waits in the session
    start = client.browser.get("/api/auth/login/google?continue=oauth", follow_redirects=False)
    ann = universe.google.add_account("g-1", "ann@gmail.com", "Ann")
    callback = universe.google.approve(start.headers["location"], ann)
    back = client.browser.get(callback.url.replace(BASE, ""), follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"].startswith("/oauth/authorize?")
    page = client.browser.get(back.headers["location"], follow_redirects=False)
    assert "Connect Twin Agent (app.example) to your Vault?" in page.text and "Ann" in page.text


def test_an_ordinary_sign_in_does_not_resume_an_old_request(client, universe):
    client.authorize()
    start = client.browser.get("/api/auth/login/google", follow_redirects=False)  # not from the consent page
    ann = universe.google.add_account("g-1", "ann@gmail.com", "Ann")
    callback = universe.google.approve(start.headers["location"], ann)
    back = client.browser.get(callback.url.replace(BASE, ""), follow_redirects=False)
    assert back.headers["location"] == "/"


def test_use_a_different_account_signs_out_and_returns_to_sign_in(client):
    client.sign_in("a@example.com")
    res = client.answer(client.authorize(), decision="switch")
    assert res.status_code == 303 and res.headers["location"].startswith("/oauth/authorize?")
    assert "Sign in to connect" in client.browser.get(res.headers["location"]).text


# -- consent: CSRF, clickjacking, replay -------------------------------------------------------

def test_pages_cannot_be_framed(client):
    client.sign_in()
    for res in (client.authorize(), client.authorize(redirect_uri="https://evil.example/x")):  # consent, error
        assert res.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
        assert res.headers["cache-control"] == "no-store"
    client.browser.cookies.clear()
    assert client.authorize().headers["x-frame-options"] == "DENY"  # the sign-in page too


def test_consent_needs_the_nonce_made_for_this_browser(client, make_client):
    client.sign_in()
    page = client.authorize()
    nonce = client.nonce(page)
    for data in ({"decision": "allow"}, {"decision": "allow", "nonce": "guess"}, {"decision": "allow", "nonce": ""}):
        # (each failed answer also uses up the pending consent, so make a fresh one)
        res = client.browser.post("/oauth/authorize", data=data, follow_redirects=False)
        assert res.status_code == 400 and "location" not in res.headers
        page = client.authorize()
    # another browser (the attacker's page can't read the nonce, but even with it the session differs)
    other = make_client()
    other.sign_in("mallory@example.com")
    res = other.browser.post("/oauth/authorize", data={"decision": "allow", "nonce": client.nonce(page)})
    assert res.status_code == 400
    assert nonce  # the legitimate form is untouched by the above
    assert client.answer(client.authorize()).status_code == 303


def test_a_consent_answer_works_once(client):
    client.sign_in()
    page = client.authorize()
    assert client.answer(page).status_code == 303
    replay = client.answer(page)  # the same form again: the nonce is spent
    assert replay.status_code == 400 and "location" not in replay.headers


def test_a_consent_form_posted_from_another_site_is_refused(client):
    client.sign_in()
    res = client.answer(client.authorize(), Origin="https://evil.example")
    assert res.status_code == 403


def test_consent_expires(app, client):
    from vault.models import OAuthConsent

    client.sign_in()
    page = client.authorize()
    db_do(app, lambda db: (db.execute(update(OAuthConsent).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))),
                           db.commit()))
    assert client.answer(page).status_code == 400


def test_the_consent_belongs_to_the_account_that_saw_it(client):
    client.sign_in("a@example.com")
    page = client.authorize()
    client.sign_in("b@example.com")  # the browser's account changed meanwhile
    assert client.answer(page).status_code == 400


# -- the authorization request: PKCE, redirect, resource, state --------------------------------

@pytest.mark.parametrize("override", [
    {"code_challenge": None}, {"code_challenge_method": "plain"}, {"code_challenge_method": None},
    {"code_challenge_method": "s256"}, {"code_challenge": "short"}, {"code_challenge": "!" * 43},
])
def test_pkce_s256_is_required(client, override):
    client.sign_in()
    sent = returned(client.authorize(**override))
    assert sent["error"] == "invalid_request" and sent["state"] == client.state


@pytest.mark.parametrize("redirect", [
    "https://evil.example/callback", "https://app.example/callback/", "https://app.example/callbackx",
    "https://app.example:8443/callback", "http://app.example/callback", "https://app.example/callback?x=1",
    "https://app.example.evil.example/callback", "https://app.example@evil.example/callback",
    "//evil.example/callback", "javascript:alert(1)", "https://app.example/callback#frag", "", "/relative",
])
def test_redirect_uri_must_match_exactly_and_is_never_followed_otherwise(client, redirect):
    client.sign_in()
    res = client.authorize(redirect_uri=redirect or None)
    assert res.status_code == 400 and "location" not in res.headers  # an error page, never a redirect


def test_a_missing_redirect_uri_is_an_error_page(client):
    client.sign_in()
    res = client.authorize(redirect_uri=None)
    assert res.status_code == 400 and "location" not in res.headers


def test_loopback_redirects_may_use_any_port_but_not_other_hosts_or_paths(make_client):
    c = make_client(redirect_uris=("http://127.0.0.1:3000/callback", "http://localhost:3000/cb"))
    c.sign_in()
    for uri in ("http://127.0.0.1:51234/callback", "http://127.0.0.1/callback", "http://localhost:9/cb"):
        c.redirect_uri = uri
        page = c.authorize()
        assert page.status_code == 200 and "this computer" in page.text, uri  # warned about
        assert query(c.answer(page))["code"]
    for uri in ("http://127.0.0.1:3000/other", "http://localhost:3000/callback", "http://127.0.0.2:3000/callback",
                "http://127.0.0.1.evil.example:3000/callback", "https://127.0.0.1:3000/callback"):
        c.redirect_uri = uri
        assert c.authorize().status_code == 400, uri


def test_a_non_loopback_port_difference_is_not_tolerated(make_client):
    c = make_client(redirect_uris=("https://app.example:8443/callback",))
    c.redirect_uri = "https://app.example:9443/callback"
    c.sign_in()
    assert c.authorize().status_code == 400


@pytest.mark.parametrize("override,error", [
    ({"resource": None}, "invalid_target"), ({"resource": "http://testserver/other"}, "invalid_target"),
    ({"resource": "http://testserver"}, "invalid_target"), ({"resource": "https://evil.example/api/mcp"}, "invalid_target"),
    ({"scope": "read admin"}, "invalid_scope"), ({"scope": "account"}, "invalid_scope"),
    ({"response_type": "token"}, "unsupported_response_type"), ({"response_type": None}, "unsupported_response_type"),
    ({"state": "x" * 1600}, "invalid_request"),
])
def test_other_request_problems_are_shown_with_a_return_button_not_redirected(client, override, error):  # #339
    client.sign_in()
    res = client.authorize(**override)
    assert res.status_code == 400 and "location" not in res.headers
    assert "app.example" in res.text and "Return to app.example" in res.text and "Nothing was shared" in res.text
    sent = returned(res)
    assert sent["error"] == error and sent["iss"] == "http://testserver"


def test_a_request_error_never_redirects_a_stranger_even_when_nobody_is_signed_in(client):  # #339
    res = client.authorize(response_type="token")  # not signed in
    assert res.status_code == 400 and "location" not in res.headers and "Return to app.example" in res.text


def test_the_sign_in_page_offers_a_passkey_and_its_script_calls_routes_that_exist():  # #43
    from types import SimpleNamespace

    from vault import oauth_routes

    req = SimpleNamespace(client=SimpleNamespace(kind="cimd", name="App", client_id="https://app.example/c.json"))
    on = oauth_routes.sign_in_page(req, [], True, False)
    text = on.body.decode()
    assert 'id="passkey"' in text and "Sign in with a passkey" in text and "<script nonce=" in text
    assert "script-src 'nonce-" in on.headers["content-security-policy"]  # the one inline script is allowed by its nonce only
    assert 'id="passkey"' not in oauth_routes.sign_in_page(req, [], False, False).body.decode()  # off where WebAuthn can not run
    called = re.findall(r"post\('(/api/[^']+)'", oauth_routes.PASSKEY_SCRIPT)
    assert "/api/auth/passkey/login/options" in called and "/api/auth/passkey/login/verify" in called
    # (the routes answer 404 while WebAuthn is off, as on this http test site, so they are read from their definition)
    source = (Path(__file__).parent.parent / "vault" / "passkeys.py").read_text(encoding="utf-8")
    assert 'prefix="/api/auth/passkey"' in source and '"/login/options"' in source and '"/login/verify"' in source


def test_a_repeated_parameter_is_refused(client):
    client.sign_in()
    url = "/oauth/authorize?" + urlencode(client.authorize_params()) + "&redirect_uri=https%3A%2F%2Fevil.example%2F"
    res = client.browser.get(url, follow_redirects=False)
    assert res.status_code == 400 and "location" not in res.headers
    url = "/oauth/authorize?" + urlencode(client.authorize_params()) + "&resource=http%3A%2F%2Ftestserver%2Fother"
    assert client.browser.get(url, follow_redirects=False).status_code == 400


def test_an_unknown_client_is_an_error_page(client):
    client.sign_in()
    for client_id in ("nobody", "vault_client_unknown", "http://app.example/oauth/client.json", ""):
        res = client.authorize(client_id=client_id or None)
        assert res.status_code == 400 and "location" not in res.headers


def test_hostile_text_in_names_cannot_inject_html(make_client):
    c = make_client(name='<script>alert(1)</script>"><b>x')
    c.sign_in()
    page = c.authorize().text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page


def test_errors_to_the_redirect_encode_the_state(client):
    client.sign_in()
    sent = returned(client.authorize(state="a b&error=evil#x", code_challenge_method="plain"))
    assert sent["state"] == "a b&error=evil#x" and sent["error"] == "invalid_request"


# -- the token endpoint ------------------------------------------------------------------------

def test_a_code_works_once_and_a_replay_revokes_what_it_made(app, client):
    client.sign_in()
    code = client.approve()["code"]
    first = client.redeem(code)
    assert first.status_code == 200
    client.tokens = first.json()
    assert client.mcp("ping").status_code == 200
    replay = client.redeem(code)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert client.mcp("ping").status_code == 401  # the first redemption's tokens are gone
    assert grant_of(app) is None
    assert client.refresh().status_code == 400


def test_a_code_is_bound_to_the_pkce_verifier(client):
    client.sign_in()
    code = client.approve()["code"]
    res = client.redeem(code, code_verifier=secrets.token_urlsafe(48))
    assert res.status_code == 400 and res.json()["error"] == "invalid_grant"
    assert client.redeem(code).status_code == 400  # and the code is burnt, even for the real client


@pytest.mark.parametrize("verifier", ["short", "", "x" * 200, "!" * 50])
def test_a_malformed_verifier_is_refused(client, verifier):
    client.sign_in()
    res = client.redeem(client.approve()["code"], code_verifier=verifier)
    assert res.status_code == 400 and res.json()["error"] in ("invalid_grant", "invalid_request")


def test_a_code_is_bound_to_its_redirect_uri_client_and_resource(make_client, universe):
    c = make_client(redirect_uris=("https://app.example/callback", "https://app.example/other"))
    c.sign_in()
    code = c.approve()["code"]
    assert c.redeem(code, redirect_uri="https://app.example/other").json()["error"] == "invalid_grant"
    code = c.approve()["code"]
    other = universe.client_hosts.publish("other.example")
    assert c.redeem(code, client_id=other).json()["error"] == "invalid_grant"
    code = c.approve()["code"]
    assert c.redeem(code, resource="http://testserver/other").json()["error"] == "invalid_target"
    code = c.approve()["code"]
    assert c.redeem(code, redirect_uri=None).status_code == 400


def test_a_code_expires_after_a_minute(app, client):
    client.sign_in()
    code = client.approve()["code"]
    db_do(app, lambda db: (db.execute(update(OAuthCode).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))),
                           db.commit()))
    assert client.redeem(code).json()["error"] == "invalid_grant"
    ttl = oauth_server.CODE_TTL
    assert ttl <= timedelta(seconds=60)


def test_a_code_cannot_be_redeemed_by_another_client_even_with_the_verifier(make_client, universe):
    victim = make_client()
    victim.sign_in()
    code = victim.approve()["code"]
    thief_id = universe.client_hosts.publish("other.example", redirect_uris=("https://app.example/callback",))
    thief = McpClient(lambda: victim.api, thief_id)
    thief.verifier = victim.verifier  # even if the verifier leaked
    assert thief.redeem(code).json()["error"] == "invalid_grant"


@pytest.mark.parametrize("form,error", [
    ({"grant_type": "password", "client_id": "x"}, "unsupported_grant_type"),
    ({"grant_type": "client_credentials", "client_id": "x"}, "unsupported_grant_type"),
    ({"client_id": "x"}, "unsupported_grant_type"),
    ({"grant_type": "authorization_code"}, "invalid_client"),
    ({"grant_type": "authorization_code", "client_id": "x"}, "invalid_request"),
    ({"grant_type": "refresh_token", "client_id": "x"}, "invalid_request"),
    ({"grant_type": "refresh_token", "client_id": "x", "refresh_token": "vault_ort_nope"}, "invalid_grant"),
    ({"grant_type": "authorization_code", "client_id": "x", "code": "nope", "redirect_uri": "https://a/b",
      "code_verifier": "v" * 50}, "invalid_grant"),
])
def test_token_errors_follow_rfc_6749(client, form, error):
    res = client.api.post("/oauth/token", data=form)
    assert res.json()["error"] == error and res.status_code == (401 if error == "invalid_client" else 400)
    assert "error_description" in res.json() and res.headers["cache-control"] == "no-store"


def test_unknown_and_known_codes_fail_the_same_way(client):
    client.sign_in()
    real = client.approve()["code"]
    wrong = client.redeem("x" * 43, code_verifier="y" * 50).json()
    spent_verifier = client.redeem(real, code_verifier="y" * 50).json()
    assert wrong["error"] == "invalid_grant" and spent_verifier["error"] == "invalid_grant"
    assert set(wrong) == set(spent_verifier)


def test_json_bodies_are_not_token_requests(client):
    res = client.api.post("/oauth/token", json={"grant_type": "refresh_token"})
    assert res.status_code in (400, 401, 422) and "access_token" not in res.text


# -- refresh -----------------------------------------------------------------------------------

def test_refresh_rotates_and_the_old_refresh_token_stops_working(client):
    tokens = client.connect()
    again = client.refresh()
    assert again.status_code == 200
    new = again.json()
    assert new["refresh_token"] != tokens["refresh_token"] and new["access_token"] != tokens["access_token"]
    client.tokens = new
    assert client.mcp("ping").status_code == 200
    old_access = tokens["access_token"]
    assert client.mcp("ping", token=old_access).status_code == 401  # the old access token was replaced


def test_a_reused_refresh_token_revokes_the_whole_grant(app, client):
    first = client.connect()
    second = client.refresh().json()
    client.tokens = second
    third = client.refresh().json()
    client.tokens = third
    stolen = client.refresh(first["refresh_token"])  # a rotated token comes back: it was copied
    assert stolen.status_code == 400 and stolen.json()["error"] == "invalid_grant"
    assert grant_of(app) is None
    for token in (second["access_token"], third["access_token"]):
        assert client.mcp("ping", token=token).status_code == 401
    assert client.refresh(third["refresh_token"]).status_code == 400  # the legitimate one is revoked too


def test_a_stolen_refresh_token_cannot_be_used_by_another_client(app, client, universe):
    tokens = client.connect()
    thief_id = universe.client_hosts.publish("other.example")
    res = client.refresh(tokens["refresh_token"], client_id=thief_id)
    assert res.status_code == 400 and res.json()["error"] == "invalid_grant"
    assert grant_of(app) is None  # presenting it elsewhere counts as copying


def test_refresh_cannot_widen_the_scopes(client):
    client.sign_in()
    client.tokens = client.redeem(client.approve(scope="read write", write=False)["code"]).json()
    assert client.tokens["scope"] == "read"
    res = client.refresh(scope="read write")
    assert res.status_code == 400 and res.json()["error"] == "invalid_scope"
    assert client.refresh(scope="write admin").json()["error"] == "invalid_scope"
    assert client.refresh(scope="read").status_code == 200  # (the failed ones did not rotate the token)


def test_refresh_may_narrow_the_scopes(client):
    client.sign_in()
    client.tokens = client.redeem(client.approve(write=True, scope="read write")["code"]).json()
    narrowed = client.refresh(scope="read").json()
    assert narrowed["scope"] == "read"
    client.tokens = narrowed
    assert "save_deck" not in {t["name"] for t in client.mcp("tools/list").json()["result"]["tools"]}
    wide = client.refresh().json()  # the person's consent still covers write
    assert wide["scope"] == "read write"


def test_refresh_resource_must_match(client):
    client.connect()
    assert client.refresh(resource="http://testserver/other").json()["error"] == "invalid_target"


def test_expired_access_and_refresh_tokens_are_refused(app, client):
    tokens = client.connect()
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    db_do(app, lambda db: (db.execute(update(OAuthGrant).values(access_expires=past)), db.commit()))
    res = client.mcp("ping", token=tokens["access_token"])
    assert res.status_code == 401 and 'error="invalid_token"' in res.headers["www-authenticate"]
    db_do(app, lambda db: (db.execute(update(OAuthGrant).values(refresh_expires=past)), db.commit()))
    assert client.refresh().json()["error"] == "invalid_grant"
    assert grant_of(app) is None


def test_lifetimes():
    assert oauth_server.ACCESS_TTL == timedelta(hours=1) and oauth_server.CODE_TTL <= timedelta(seconds=60)


def test_revoking_a_token_with_rfc_7009_disconnects_the_app(app, client):
    tokens = client.connect()
    assert client.api.post("/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": "https://x.example/c"}
                           ).status_code == 200
    assert grant_of(app) is not None  # another client's revocation does nothing
    assert client.api.post("/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": client.client_id}
                           ).status_code == 200
    assert grant_of(app) is None
    assert client.api.post("/oauth/revoke", data={"token": "nonsense"}).status_code == 200  # same answer


# -- audience, scopes and what a token can reach -----------------------------------------------

def test_a_token_for_another_resource_is_refused(app, client):
    tokens = client.connect()
    db_do(app, lambda db: (db.execute(update(OAuthGrant).values(resource="http://testserver/other")), db.commit()))
    assert client.mcp("ping", token=tokens["access_token"]).status_code == 401


def test_oauth_tokens_are_for_the_mcp_server_only(client):
    client.sign_in()
    import_csv(client)
    client.connect()
    bearer = {"Authorization": f"Bearer {client.tokens['access_token']}"}
    for method, path in (("GET", f"{V1}/collection"), ("GET", f"{V1}/me"), ("POST", f"{V1}/me/tokens"),
                         ("DELETE", f"{V1}/me"), ("GET", f"{V1}/me/export"), ("GET", f"{V1}/me/apps"),
                         ("GET", f"{V1}/me/sessions")):
        res = client.api.request(method, path, headers=bearer)
        assert res.status_code in (401, 403) and res.status_code != 200, (method, path)
    assert client.api.get(f"{V1}/collection", headers=bearer).status_code == 401


def test_even_on_the_mcp_servers_own_calls_a_token_never_gets_account_powers(app, client):
    """The in-process calls a tool makes accept OAuth tokens, so the account rule is checked there too."""
    client.sign_in()
    client.connect()
    bearer = {"Authorization": f"Bearer {client.tokens['access_token']}"}
    import anyio

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_marked_as_mcp(app)), base_url=BASE) as c:
            assert (await c.get(f"{V1}/collection", headers=bearer)).status_code == 200  # reachable here
            for method, path, kw in (("POST", f"{V1}/me/tokens", {"json": {"name": "x"}}), ("DELETE", f"{V1}/me", {"json": {"confirm": "DELETE"}}),
                                     ("GET", f"{V1}/me/export", {}), ("GET", f"{V1}/me/apps", {}), ("GET", f"{V1}/me/sessions", {}),
                                     ("DELETE", f"{V1}/me/apps/1", {}), ("PATCH", f"{V1}/me", {"json": {"name": "x"}})):
                res = await c.request(method, path, headers=bearer, **kw)
                assert res.status_code == 403, (method, path, res.status_code)

    anyio.run(run)


def test_authenticate_never_returns_the_account_scope(app, client):
    tokens = client.connect()
    with app.state.db.sessions() as db:
        user, scopes = oauth_server.authenticate(db, tokens["access_token"], f"{BASE}/api/mcp")
        assert scopes == {"read"} and "account" not in scopes
        assert oauth_server.authenticate(db, tokens["access_token"], f"{BASE}/other") is None


def test_a_token_only_reaches_its_own_owners_data(client, make_client):
    # Alice's token, asking for a collection shared with someone else (Bob's), finds nothing.
    bob = make_client()
    bob.sign_in("bob@example.com")
    share = bob.browser.post(f"{V1}/shares", json={"kind": "collection"}).json()
    client.sign_in("alice@example.com")
    import_csv(client)
    client.connect(email="alice@example.com")
    res = client.tool("get_collection_summary", share_id=share["id"])["result"]
    assert res["isError"] is True and res["structuredContent"]["status"] == 404
    assert client.tool("get_collection_summary")["result"]["structuredContent"]["copies"] == 7  # her own
    bob.connect(email="bob@example.com")
    own = bob.tool("get_collection_summary")["result"]["structuredContent"]
    assert own["copies"] == 0  # Bob's token sees Bob's (empty) collection, never Alice's


def test_two_people_connecting_the_same_app_get_separate_grants(app, client, make_client):
    client.connect()
    other = make_client()
    other.sign_in("b@example.com")
    other.tokens = other.redeem(other.approve()["code"]).json()
    with app.state.db.sessions() as db:
        grants = list(db.scalars(select(OAuthGrant)))
        assert len(grants) == 2 and grants[0].user_id != grants[1].user_id
    assert client.mcp("ping").status_code == 200 and other.mcp("ping").status_code == 200


# -- connected apps ----------------------------------------------------------------------------

def test_connected_apps_are_listed_and_can_be_revoked(app, client):
    client.sign_in()
    client.connect()
    client.mcp("ping")
    listed = client.browser.get(f"{V1}/me/apps").json()
    [item] = listed["items"]
    assert item["name"] == "Twin Agent" and item["domain"] == "app.example" and item["scopes"] == ["read"]
    assert item["verified_by_address"] is True and item["created_at"] and item["last_used_at"]
    assert "token" not in str(item) and "hash" not in str(item)
    assert client.browser.get(f"{V1}/me").json()["_links"]["apps"]["href"] == f"{V1}/me/apps"
    assert client.browser.delete(item["_links"]["self"]["href"]).json() == {"deleted": True, "connections": 1}
    assert client.mcp("ping").status_code == 401  # revoked: the tokens stop working at once
    assert client.refresh().status_code == 400
    assert client.browser.get(f"{V1}/me/apps").json()["items"] == []
    assert client.browser.delete(f"{V1}/me/apps/{item['id']}").status_code == 404


def test_one_apps_revocation_leaves_the_others(app, client, make_client):
    client.sign_in()
    client.connect()
    second = make_client(host="other.example", redirect_uris=("https://other.example/cb",))
    second.redirect_uri = "https://other.example/cb"
    second.browser = client.browser
    second.tokens = second.redeem(second.approve()["code"]).json()
    items = client.browser.get(f"{V1}/me/apps").json()["items"]
    assert len(items) == 2
    client.browser.delete(f"{V1}/me/apps/{items[0]['id']}")
    survivors = {c.mcp("ping").status_code for c in (client, second)}
    assert survivors == {200, 401}


def test_signing_out_everywhere_does_not_disconnect_apps_but_revoking_does(client):
    client.sign_in()
    client.connect()
    client.browser.post("/api/auth/logout?everywhere=true")
    assert client.mcp("ping").status_code == 200  # a connected app is its own credential
    client.sign_in()
    assert client.browser.get(f"{V1}/me/apps").json()["total"] == 1


def test_the_export_lists_connected_apps_without_tokens_and_erasure_removes_them(app, client):
    import io
    import json
    import zipfile

    client.sign_in()
    tokens = client.connect()
    archive = zipfile.ZipFile(io.BytesIO(client.browser.get(f"{V1}/me/export").content))
    text = archive.read("connected_apps.json").decode()
    [row] = json.loads(text)
    assert row["app"] == "Twin Agent" and row["allowed"] == ["read"] and row["connected_at"]
    assert tokens["access_token"] not in text and tokens["refresh_token"] not in text and "hash" not in text
    assert "connected_apps.json" in archive.read("README.txt").decode()
    removed = client.browser.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}).json()["removed"]
    assert removed["oauth_grants"] == 1 and removed["oauth_codes"] == 1
    assert grant_of(app) is None
    assert client.mcp("ping").status_code == 401


def test_the_grant_cap_keeps_a_person_from_piling_up_apps(app, client, monkeypatch):
    monkeypatch.setattr(oauth_server, "MAX_APPS", 2)
    client.sign_in()
    for _ in range(4):
        client.tokens = client.redeem(client.approve()["code"]).json()
    assert db_do(app, lambda db: len(oauth_server.user_grants(db, grant_of(app).user_id))) == 2  # connections; the list groups them by app


# -- rate limits, logging ----------------------------------------------------------------------

def test_the_authorize_token_and_registration_endpoints_are_rate_limited(database_url, universe):
    settings = Settings(database_url=database_url, session_secret="test", dev_login=True, base_url=BASE,
                        oauth_rate_limit=3, oauth_register_rate_limit=2)
    app = create_app(settings, serve_static=False, transport=universe.transport, resolver=universe.resolve)
    with TestClient(app) as c:
        statuses = [c.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "x"}).status_code
                    for _ in range(5)]
        assert statuses[:3] == [400] * 3 and statuses[3:] == [429, 429]
        assert 429 in [c.get("/oauth/authorize?client_id=x").status_code for _ in range(5)]
        regs = [c.post("/oauth/register", json={"redirect_uris": ["https://a.example/cb"]}).status_code for _ in range(4)]
        assert regs == [201, 201, 429, 429]
        assert c.get("/.well-known/oauth-authorization-server").status_code == 200  # metadata is not limited
    app.state.db.engine.dispose()


def test_secrets_never_reach_the_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.sign_in()
    code = client.approve()["code"]
    tokens = client.redeem(code).json()
    client.tokens = tokens
    client.mcp("ping")
    client.refresh()
    client.redeem(code)  # a replay
    client.refresh(tokens["refresh_token"])  # a reuse
    for secret in (code, tokens["access_token"], tokens["refresh_token"], client.verifier):
        assert secret not in caplog.text


def test_tokens_are_not_accepted_in_the_query_string(client):
    client.connect()
    res = client.api.post(f"/api/mcp?access_token={client.tokens['access_token']}",
                          json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert res.status_code == 401


def test_the_wire_format_of_a_pkce_challenge():
    from vault.tokens import s256
    assert s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # RFC 7636 appendix B


def test_state_is_optional_but_echoed_when_present(client):
    client.sign_in()
    res = client.answer(client.authorize(state=None))
    assert "state" not in query(res) and query(res)["code"]


def test_a_long_state_like_openais_plugin_portal_sends_is_accepted_and_echoed(client):
    """OpenAI's plugin portal sent a 693-character state on 2026-10-10 and was refused at 500 (#240)."""
    client.sign_in()
    long_state = "s" * 693
    res = client.answer(client.authorize(state=long_state))
    assert query(res)["state"] == long_state and query(res)["code"]
    too_long = client.authorize(state="x" * 1501)
    assert too_long.status_code == 400 and "state is too long" in too_long.text  # refused on a page, never echoed


# -- review fixes: redirect rebuilding, one-time consent, grant age, marker, races -------------

BAD_LOOPBACK = [
    "http://evil.com\\@127.0.0.1:9/cb", "http://evil.com%5c@127.0.0.1:9/cb", "http://evil.com%5C@127.0.0.1:9/cb",
    "http://127.0.0.1:9/cb\t", "http://127.0.0.1:9/c\nb", "http://127.0.0.1:9/cb\r", "http://127.0.0.1:9/cb ",
    "http://user@127.0.0.1:9/cb", "http://evil.com@127.0.0.1:9/cb", "http://127.0.0.1:9/cb#frag",
    "http://127.0.0.1:9/cb%5c", "http://127.0.0.1:080/cb", "http://127.0.0.1:0/cb", "http://127.0.0.1:99999/cb",
    "http://127.0.0.1:9/cb\u00e9", "http://127.0.0.1./cb", "http://127.1:9/cb", "http://0x7f.0.0.1:9/cb",
    "http://[::ffff:127.0.0.1]:9/cb", "http://127.0.0.1:9\\@evil.com/cb", "http://127.0.0.1:9/cb?x=1",
]


@pytest.mark.parametrize("given", BAD_LOOPBACK)
def test_loopback_redirects_are_checked_strictly_and_never_sent_as_given(make_client, given):
    c = make_client(redirect_uris=("http://127.0.0.1:8080/cb",))
    c.sign_in()
    c.redirect_uri = given
    res = c.authorize()
    assert res.status_code == 400 and "location" not in res.headers


@pytest.mark.parametrize("given", [
    "http://[0:0:0:0:0:0:0:1]:9/cb", "http://[::1]./cb", "http://[::1]:9/other", "http://[::2]:9/cb", "http://[::1:9/cb",
    "http://LOCALHOST:9/cb", "http://Localhost:9/cb", "http://localhost.:9/cb", "http://127.0.0.1:9/cb",
])
def test_other_loopback_spellings_are_refused(make_client, given):
    c = make_client(redirect_uris=("http://localhost:8080/cb", "http://[::1]:8080/cb"))
    c.sign_in()
    c.redirect_uri = given
    res = c.authorize()
    assert res.status_code == 400 and "location" not in res.headers


def test_a_good_loopback_redirect_is_sent_to_exactly_the_registered_host(make_client):
    c = make_client(redirect_uris=("http://127.0.0.1:8080/cb?x=1", "http://[::1]:8080/cb", "http://localhost/cb"))
    c.sign_in()
    for given, netloc in (("http://127.0.0.1:9/cb?x=1", "127.0.0.1:9"), ("http://[::1]:5555/cb", "[::1]:5555"),
                          ("http://localhost:7/cb", "localhost:7")):
        c.redirect_uri = given
        res = c.answer(c.authorize())
        where = location(res)
        assert (where.scheme, where.netloc) == ("http", netloc) and res.headers["location"].startswith(given.split("?")[0])
        assert query(res)["code"]


def test_the_review_exploit_string_is_refused():
    from vault.oauth_clients import match_redirect, redirect_matches

    assert not redirect_matches(["http://127.0.0.1:8080/cb"], "http://evil.com\\@127.0.0.1:9/cb")
    assert match_redirect(["http://127.0.0.1:8080/cb"], "http://127.0.0.1:9/cb") == "http://127.0.0.1:9/cb"
    assert match_redirect(["http://127.0.0.1:8080/cb"], "http://127.0.0.1:8080/cb") == "http://127.0.0.1:8080/cb"


@pytest.mark.parametrize("given", BAD_LOOPBACK[:12] + ["http://127.0.0.1:8/cb", "http://127.0.0.1/cb"])
def test_the_token_step_refuses_redirect_variants_too(make_client, given):
    c = make_client(redirect_uris=("http://127.0.0.1:8080/cb",))
    c.sign_in()
    c.redirect_uri = "http://127.0.0.1:9/cb"
    code = c.approve()["code"]
    res = c.redeem(code, redirect_uri=given)
    assert res.status_code == 400 and res.json()["error"] == "invalid_grant"


def test_a_consent_cookie_and_form_cannot_be_replayed(app, client):
    from vault.models import OAuthConsent

    client.sign_in()
    page = client.authorize()
    nonce = client.nonce(page)
    saved = dict(client.browser.cookies)
    with app.state.db.sessions() as db:
        [row] = list(db.scalars(select(OAuthConsent)))
        assert row.nonce_hash != nonce and len(row.nonce_hash) == 64  # only a hash is stored
    first = client.answer(page)
    assert first.status_code == 303 and query(first)["code"]
    replay = TestClient(app)
    for k, v in saved.items():  # the copied cookie, from before the answer
        replay.cookies.set(k, v)
    res = replay.post("/oauth/authorize", data={"nonce": nonce, "decision": "allow"}, follow_redirects=False)
    assert res.status_code == 400 and "location" not in res.headers
    assert db_do(app, lambda db: db.scalar(select(OAuthConsent))) is None
    assert db_do(app, lambda db: len(list(db.scalars(select(OAuthCode))))) == 1  # only the first answer made a code


def test_racing_consent_answers_make_one_code(app, client):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client.sign_in()
    page = client.authorize()
    nonce, saved = client.nonce(page), dict(client.browser.cookies)
    barrier = threading.Barrier(2)

    def answer():
        c = TestClient(app)
        for k, v in saved.items():
            c.cookies.set(k, v)
        barrier.wait()
        return c.post("/oauth/authorize", data={"nonce": nonce, "decision": "allow"}, follow_redirects=False).status_code

    with ThreadPoolExecutor(2) as pool:
        statuses = sorted(f.result() for f in [pool.submit(answer), pool.submit(answer)])
    assert statuses == [303, 400]
    assert db_do(app, lambda db: len(list(db.scalars(select(OAuthCode))))) == 1


def test_unanswered_consent_screens_are_capped_per_person(app, client):
    from vault.models import OAuthConsent

    client.sign_in()
    for _ in range(9):
        client.authorize()
    assert db_do(app, lambda db: len(list(db.scalars(select(OAuthConsent))))) == oauth_server.MAX_PENDING_CONSENTS


def test_a_code_that_fails_a_check_is_burnt(app, client):
    client.sign_in()
    code = client.approve()["code"]
    assert client.redeem(code, code_verifier="v" * 50).status_code == 400
    assert client.redeem(code).status_code == 400  # the right verifier no longer helps
    assert grant_of(app) is None


@pytest.mark.parametrize("round_", range(6))
def test_two_parallel_redemptions_of_one_code(app, client, round_):
    """Exactly one wins; the other is a replay, which revokes what the winner got."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client.sign_in()
    code = client.approve()["code"]
    barrier = threading.Barrier(2)
    form = client.token_form({"grant_type": "authorization_code", "code": code, "redirect_uri": client.redirect_uri,
                              "code_verifier": client.verifier}, {})

    def redeem():
        c = TestClient(app)
        barrier.wait()
        return c.post("/oauth/token", data=form)

    with ThreadPoolExecutor(2) as pool:
        results = [f.result() for f in [pool.submit(redeem), pool.submit(redeem)]]
    assert sorted(r.status_code for r in results) == [200, 400]
    assert next(r for r in results if r.status_code == 400).json()["error"] == "invalid_grant"
    winner = next(r for r in results if r.status_code == 200).json()
    assert grant_of(app) is None  # the replay found the grant (same transaction as the claim) and revoked it
    assert client.mcp("ping", token=winner["access_token"]).status_code == 401


@pytest.mark.parametrize("round_", range(6))
def test_two_parallel_refreshes_with_one_token(app, client, round_):
    """Exactly one rotates the token. A loser that lost the swap is not treated as theft (it may be a
    retry), one that arrives after the rotation is a reuse and revokes the grant; either way the old
    token never works again, and the state is consistent."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client.connect()
    old = client.tokens["refresh_token"]
    form = client.token_form({"grant_type": "refresh_token", "refresh_token": old}, {})
    barrier = threading.Barrier(2)

    def refresh():
        c = TestClient(app)
        barrier.wait()
        return c.post("/oauth/token", data=form)

    with ThreadPoolExecutor(2) as pool:
        results = [f.result() for f in [pool.submit(refresh), pool.submit(refresh)]]
    assert sorted(r.status_code for r in results) == [200, 400]
    winner = next(r for r in results if r.status_code == 200).json()
    alive = grant_of(app) is not None
    assert (client.mcp("ping", token=winner["access_token"]).status_code == 200) is alive
    assert client.refresh(old).status_code == 400  # the old token is dead for good
    assert grant_of(app) is None  # and presenting it again is reuse: the grant is gone
    assert client.mcp("ping", token=winner["access_token"]).status_code == 401


def test_a_grant_has_an_absolute_maximum_age(app, client):
    client.connect()
    now = datetime.now(timezone.utc)
    db_do(app, lambda db: (db.execute(update(OAuthGrant).values(created_at=now - timedelta(days=89))), db.commit()))
    assert client.refresh().status_code == 200
    grant = grant_of(app)
    assert grant.refresh_expires <= grant.created_at + oauth_server.GRANT_MAX_AGE + timedelta(seconds=1)  # not a full 30 days
    assert grant.refresh_expires < now + timedelta(days=2)
    db_do(app, lambda db: (db.execute(update(OAuthGrant).values(created_at=now - timedelta(days=91))), db.commit()))
    res = client.refresh()
    assert res.status_code == 400 and res.json()["error"] == "invalid_grant" and grant_of(app) is None
    assert oauth_server.GRANT_MAX_AGE == timedelta(days=90)


def test_the_mcp_marker_only_applies_to_v1_paths(app, client):
    """On the tools' own calls (marked, /api/v1) an OAuth token is accepted, and refused only for account
    powers (403). On any other path it is unknown (401): with the marker applied everywhere the account
    route below would answer 403 instead, and this test fails."""
    import anyio

    client.connect()
    bearer = {"Authorization": f"Bearer {client.tokens['access_token']}"}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_marked_as_mcp(app)), base_url=BASE) as c:
            assert (await c.get(f"{V1}/collection", headers=bearer)).status_code == 200
            assert (await c.delete(f"{V1}/me/apps/1", headers=bearer)).status_code == 403  # known, but not the person
            res = await c.post("/api/auth/passkey/register/options", headers=bearer)
            assert res.status_code == 401 and 'error="invalid_token"' in res.headers["www-authenticate"]

    anyio.run(run)


def test_a_second_authorize_request_does_not_break_an_open_consent_screen(app, client):
    from vault.models import OAuthConsent

    client.sign_in()
    first = client.authorize()
    nonce_first = client.nonce(first)
    client.authorize()  # another tab, or a cross-site GET to /oauth/authorize
    res = client.answer(first)
    assert res.status_code == 303 and query(res)["code"]
    assert nonce_first and db_do(app, lambda db: len(list(db.scalars(select(OAuthConsent))))) == 1  # the second screen is still open


def test_open_consent_screens_are_evicted_oldest_first(client):
    client.sign_in()
    pages = [client.authorize() for _ in range(oauth_server.MAX_PENDING_CONSENTS + 2)]
    assert client.answer(pages[0]).status_code == 400 and client.answer(pages[1]).status_code == 400  # the oldest two are gone
    assert client.answer(pages[-1]).status_code == 303 and client.answer(pages[-2]).status_code == 303


def test_a_non_ascii_nonce_is_an_error_page_not_a_crash(client):
    client.sign_in()
    client.authorize()
    res = client.browser.post("/oauth/authorize", data={"nonce": "caf\u00e9", "decision": "allow"}, follow_redirects=False)
    assert res.status_code == 400


def test_sign_out_everywhere_is_browser_only_like_tokens_and_app_sessions(client):
    """Found: 'sign out everywhere' rotates only the account's browser session key (docs/api.md: apps are
    signed out under /me/sessions, tokens are revoked one by one). OAuth grants behave the same: their own
    credentials, revoked under Connected apps. The 90 day grant age bounds how long one can last."""
    client.sign_in()
    client.connect()
    client.browser.post("/api/auth/logout?everywhere=true")
    assert client.mcp("ping").status_code == 200
