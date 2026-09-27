import base64
import hashlib
import hmac
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from joserfc import jwt
from joserfc.jwk import ECKey
from starlette.requests import Request

from vault.auth import Auth, Profile, apple_client_secret, microsoft_issuer_ok, sign_in
from vault.config import Settings
from vault.db import Database
from vault.models import Identity, User
from vault.routes.api import parse_signed_request


def test_microsoft_issuer_must_match_tenant():
    tid = "9188040d-6c67-4c5b-b112-36a304b66dad"
    assert microsoft_issuer_ok({"tid": tid}, f"https://login.microsoftonline.com/{tid}/v2.0")
    assert not microsoft_issuer_ok({"tid": tid}, "https://login.microsoftonline.com/other/v2.0")
    assert not microsoft_issuer_ok({}, "https://login.microsoftonline.com/None/v2.0")


def test_apple_client_secret_is_a_valid_es256_jwt():
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    s = Settings(apple_client_id="com.example.vault", apple_team_id="TEAM123", apple_key_id="KEY123",
                 apple_private_key=pem)
    token = jwt.decode(apple_client_secret(s, now=1_700_000_000), ECKey.import_key(pem))
    assert token.header["alg"] == "ES256" and token.header["kid"] == "KEY123"
    assert token.claims["iss"] == "TEAM123" and token.claims["sub"] == "com.example.vault"
    assert token.claims["aud"] == "https://appleid.apple.com" and token.claims["exp"] - token.claims["iat"] == 3600


def test_only_configured_providers_are_enabled():
    assert Auth(Settings(google_client_id="g", google_client_secret="x")).enabled == ["google"]
    assert Auth(Settings()).enabled == []


def _request():
    scope = {"type": "http", "session": {}, "method": "GET", "headers": []}
    return Request(scope)


def test_sign_in_links_providers_but_never_merges_by_email(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/a.db")
    db.create_all()
    with db.sessions() as s:
        req = _request()
        alice = sign_in(s, req, Profile("google", "g-1", "alice@example.com", "Alice"))
        # signed in -> another provider is linked to the same account
        linked = sign_in(s, req, Profile("microsoft", "m-1", "alice@work.example", None))
        assert linked.id == alice.id and {i.provider for i in linked.identities} == {"google", "microsoft"}
        # fresh session, same e-mail on a new provider -> separate account (no silent merge)
        other = sign_in(s, _request(), Profile("facebook", "f-1", "alice@example.com", "Alice"))
        assert other.id != alice.id
        # returning user
        again = sign_in(s, _request(), Profile("google", "g-1", "alice@example.com", "Alice"))
        assert again.id == alice.id
        assert s.query(User).count() == 2 and s.query(Identity).count() == 3


def _signed(payload: dict, secret: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode()
    return sig.rstrip("=") + "." + body


def test_facebook_signed_request():
    good = _signed({"algorithm": "HMAC-SHA256", "user_id": "123"}, "fb-secret")
    assert parse_signed_request(good, "fb-secret")["user_id"] == "123"
    assert parse_signed_request(good, "wrong") is None
    assert parse_signed_request("garbage", "fb-secret") is None


def test_facebook_data_deletion_endpoint(client, app):
    with app.state.db.sessions() as s:
        sign_in(s, _request(), Profile("facebook", "123", None, "Bob"))
    res = client.post("/api/facebook/data-deletion",
                      data={"signed_request": _signed({"algorithm": "HMAC-SHA256", "user_id": "123"}, "fb-secret")})
    assert res.status_code == 200 and set(res.json()) == {"url", "confirmation_code"}
    with app.state.db.sessions() as s:
        assert s.query(User).count() == 0


def test_apple_requests_openid_scope():
    s = Settings(apple_client_id="com.example.vault", apple_team_id="T", apple_key_id="K", apple_private_key="x")
    client = Auth(s).oauth.create_client("apple")
    assert "openid" in client.client_kwargs["scope"].split()  # needed for the id_token / user id
