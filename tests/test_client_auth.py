"""private_key_jwt at the token endpoint (vault/client_auth.py, #210): a client whose metadata document publishes keys
(ChatGPT does) must sign each token request; the Vault checks the signature, who signed, for whom, for how long, and
that each assertion is used once. Public clients (Claude) are unchanged."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import ECKey, OctKey, RSAKey

from tests.test_mcp_oauth import BASE, app, settings, universe  # noqa: F401 - fixtures
from twins.mcp_client import GOOD_HOST, McpClient
from vault import client_auth

TOKEN_URL = f"{BASE}/oauth/token"
JWKS_PATH = "/oauth/jwks.json"


def rsa(kid="k1"):
    return RSAKey.generate_key(2048, parameters={"kid": kid}, private=True)


def publish_keys(universe, *keys, host=GOOD_HOST, path=JWKS_PATH):
    universe.client_hosts.documents[(host, path)] = {"keys": [k.as_dict(private=False) for k in keys]}
    return f"https://{host}{path}"


@pytest.fixture
def key():
    return rsa()


@pytest.fixture
def signer(app, universe, key):
    """A ChatGPT-like client: its document says private_key_jwt, RS256, keys at a jwks_uri on its own host."""
    jwks_uri = publish_keys(universe, key)
    client_id = universe.client_hosts.publish(token_endpoint_auth_method="private_key_jwt",
                                              token_endpoint_auth_signing_alg="RS256", jwks_uri=jwks_uri)
    return McpClient(lambda: TestClient(app), client_id)


def assertion(client, key, *, alg="RS256", kid="k1", lifetime=300, jti=None, **claims):
    now = int(time.time())
    body = {"iss": client.client_id, "sub": client.client_id, "aud": TOKEN_URL, "iat": now, "exp": now + lifetime,
            "jti": jti or uuid.uuid4().hex, **claims}
    return jwt.encode({"alg": alg, "kid": kid}, {k: v for k, v in body.items() if v is not None}, key)


def signed(client, key, **kw):
    return {"client_assertion_type": client_auth.JWT_BEARER, "client_assertion": assertion(client, key, **kw)}


def code_for(client):
    client.sign_in()
    return client.approve()["code"]


def test_a_signing_client_connects_and_refreshes(signer, key):
    res = signer.redeem(code_for(signer), **signed(signer, key))
    assert res.status_code == 200, res.text
    signer.tokens = res.json()
    assert signer.mcp("tools/list").status_code == 200
    again = signer.refresh(**signed(signer, key))
    assert again.status_code == 200, again.text


def test_the_client_id_may_come_from_the_assertion_alone(signer, key):
    res = signer.redeem(code_for(signer), client_id=None, **signed(signer, key))
    assert res.status_code == 200, res.text


def test_a_signing_client_without_an_assertion_is_refused(signer, key):
    code = code_for(signer)
    res = signer.redeem(code)
    assert res.status_code == 401 and res.json()["error"] == "invalid_client"
    assert signer.redeem(code, client_assertion_type="urn:other", client_assertion=assertion(signer, key)).status_code == 401


@pytest.mark.parametrize("change", [
    {"iss": "https://evil.example/oauth/client.json"}, {"sub": "someone-else"}, {"aud": "https://evil.example/token"},
    {"lifetime": -120},  # expired
    {"lifetime": 3600},  # lives far too long
    {"iat": int(time.time()) + 3600},  # issued in the future
    {"jti": "short"},
])
def test_assertions_with_the_wrong_claims_are_refused(signer, key, change):
    res = signer.redeem(code_for(signer), **signed(signer, key, **change))
    assert res.status_code == 401 and res.json()["error"] == "invalid_client", res.text


def test_an_assertion_works_once(signer, key):
    once = signed(signer, key, jti="the-same-jti-twice")
    assert signer.redeem(code_for(signer), **once).status_code == 200
    replay = signer.redeem(code_for(signer), **once)
    assert replay.status_code == 401 and "already used" in replay.json()["error_description"]


def test_a_signature_from_another_key_is_refused(signer):
    res = signer.redeem(code_for(signer), **signed(signer, rsa("k1")))  # same kid, different key
    assert res.status_code == 401 and "signature" in res.json()["error_description"]


@pytest.mark.parametrize("make_key,alg", [(lambda: OctKey.generate_key(256), "HS256"),
                                          (lambda: ECKey.generate_key("P-256", parameters={"kid": "k1"}, private=True), "ES256")])
def test_only_the_declared_algorithm_is_accepted(signer, make_key, alg):
    res = signer.redeem(code_for(signer), **signed(signer, make_key(), alg=alg))
    assert res.status_code == 401 and res.json()["error"] == "invalid_client"


def test_an_unsigned_assertion_is_refused(signer):
    import base64
    import json
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    now = int(time.time())
    none = part({"alg": "none"}) + "." + part({"iss": signer.client_id, "sub": signer.client_id, "aud": TOKEN_URL,
                                                "exp": now + 60, "jti": uuid.uuid4().hex}) + "."
    res = signer.redeem(code_for(signer), client_assertion_type=client_auth.JWT_BEARER, client_assertion=none)
    assert res.status_code == 401


def test_keys_rotate_without_a_restart(app, universe, signer, key):
    assert signer.redeem(code_for(signer), **signed(signer, key)).status_code == 200
    new = rsa("k2")
    publish_keys(universe, key, new)
    import vault.client_auth as ca
    ca.REFETCH_AFTER, old = 0, ca.REFETCH_AFTER  # an unknown kid fetches the keys again (normally at most once a minute)
    try:
        res = signer.redeem(code_for(signer), **signed(signer, new, kid="k2"))
    finally:
        ca.REFETCH_AFTER = old
    assert res.status_code == 200, res.text


def test_a_public_client_is_unchanged_and_may_not_send_an_assertion(app, universe, key):
    public = McpClient(lambda: TestClient(app), universe.client_hosts.publish())
    code = code_for(public)
    res = public.redeem(code, **signed(public, key))
    assert res.status_code == 401 and "public" in res.json()["error_description"]
    assert public.redeem(code).status_code == 200  # PKCE alone, as for claude.ai


@pytest.mark.parametrize("fields,why", [
    ({"jwks_uri": "https://other.example/oauth/jwks.json"}, "same host"),
    ({"jwks_uri": None}, "jwks_uri"),
    ({"jwks_uri": "http://app.example/oauth/jwks.json"}, ""),
    ({"token_endpoint_auth_signing_alg": "HS256"}, "token_endpoint_auth_signing_alg"),
    ({"token_endpoint_auth_signing_alg": "none"}, "token_endpoint_auth_signing_alg"),
])
def test_documents_with_unsafe_key_settings_are_refused(app, universe, key, fields, why):
    jwks_uri = publish_keys(universe, key)
    doc = {"token_endpoint_auth_method": "private_key_jwt", "token_endpoint_auth_signing_alg": "RS256", "jwks_uri": jwks_uri,
           **fields}
    client = McpClient(lambda: TestClient(app), universe.client_hosts.publish(**doc))
    client.sign_in()
    page = client.authorize()
    assert page.status_code == 400 and why in page.text


def test_a_refused_token_request_is_logged_with_the_reason_and_no_secret(signer, key, caplog):
    code = code_for(signer)
    secret = assertion(signer, rsa("k1"))  # a wrong key: refused
    with caplog.at_level("WARNING", logger="vault.oauth_routes"):
        signer.redeem(code, client_assertion_type=client_auth.JWT_BEARER, client_assertion=secret)
    line = next(r.getMessage() for r in caplog.records if "token request refused" in r.getMessage())
    assert signer.client_id in line and "assertion=True" in line and "signature" in line
    assert secret not in line and code not in line  # neither the assertion nor the code is ever logged
