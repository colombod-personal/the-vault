import pytest
import base64
import pytest
import hashlib
import hmac
import json
import os
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from joserfc import jwt
from joserfc.jwk import ECKey
from starlette.requests import Request

from vault.auth import Auth, Profile, apple_client_secret, microsoft_issuer_ok, sign_in
from vault.config import Settings
from vault.db import Database
from vault.models import Identity, User
from vault.api.meta import parse_signed_request


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
    db.migrate()
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
                      data={"signed_request": _signed({"algorithm": "HMAC-SHA256", "user_id": "123", "issued_at": int(time.time())}, "fb-secret")})
    assert res.status_code == 200 and set(res.json()) == {"url", "confirmation_code"}
    with app.state.db.sessions() as s:
        assert s.query(User).count() == 0


def test_apple_requests_openid_scope():
    s = Settings(apple_client_id="com.example.vault", apple_team_id="T", apple_key_id="K", apple_private_key="x")
    client = Auth(s).oauth.create_client("apple")
    assert "openid" in client.client_kwargs["scope"].split()  # needed for the id_token / user id


def test_account_marker_cookie_follows_the_signed_in_account(client):
    """The web app keys its offline copy by `vault_account`: it must change with the account,
    disappear on sign-out, and not reveal the user id."""
    assert "vault_account" not in client.cookies
    client.post("/api/auth/dev-login", params={"email": "ann@example.com"})
    ann = client.cookies.get("vault_account")
    ann_id = client.get("/api/v1/me").json()["id"]
    assert ann and len(ann) == 32 and ann != str(ann_id)
    client.get("/api/v1/me")
    assert client.cookies.get("vault_account") == ann  # stable while signed in
    client.post("/api/auth/dev-login", params={"email": "bo@example.com"})
    assert client.cookies.get("vault_account") not in (None, ann)
    client.post("/api/auth/logout")
    client.get("/api/auth/providers")
    assert "vault_account" not in client.cookies


@pytest.mark.parametrize("payload", [[1, 2], "user", 42])
def test_facebook_signed_request_that_is_not_an_object_is_refused(payload):
    assert parse_signed_request(_signed(payload, "fb-secret"), "fb-secret") is None


@pytest.mark.skipif(not os.environ.get("VAULT_TEST_POSTGRES_URL"), reason="needs Postgres (unique index waits)")
def test_two_first_sign_ins_at_once_end_in_one_account():
    """Two callbacks for the same new identity: the one that loses the insert race signs in
    to the account the winner created, instead of failing."""
    from sqlalchemy import func, select, text
    from sqlalchemy.orm import Session
    from vault.auth import find_or_create
    from vault.db import Base
    db = Database(os.environ["VAULT_TEST_POSTGRES_URL"])
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.migrate()
    profile = Profile("google", "g-1", "ann@example.com", "Ann")
    outcome = {}

    def the_other_callback():
        with Session(db.engine) as s:
            try:
                outcome["user"] = find_or_create(s, profile).id
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = repr(exc)

    with Session(db.engine) as s:
        winner = User(email="ann@example.com", name="Ann")
        winner.identities.append(Identity(provider="google", subject="g-1"))
        s.add(winner)
        s.flush()  # inserted, not yet committed
        racer = threading.Thread(target=the_other_callback)
        racer.start()
        time.sleep(0.5)
        s.commit()
        winner_id = winner.id
    racer.join(10)
    assert outcome == {"user": winner_id}
    with Session(db.engine) as s:
        assert s.scalar(select(func.count(User.id))) == 1
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.engine.dispose()


def test_facebook_deletion_status_only_confirms_codes_the_vault_issued(client, app):
    with app.state.db.sessions() as s:
        sign_in(s, _request(), Profile("facebook", "321", None, "Cy"))
    res = client.post("/api/facebook/data-deletion",
                      data={"signed_request": _signed({"algorithm": "HMAC-SHA256", "user_id": "321", "issued_at": int(time.time())}, "fb-secret")})
    issued = res.json()
    status = client.get("/" + issued["url"].split("/", 3)[3])  # the URL Meta shows the user
    assert status.status_code == 200 and "has been deleted" in status.text
    for forged in ("made-up", issued["confirmation_code"][:-2] + "xx", ""):
        res = client.get("/api/facebook/deletion-status", params={"code": forged})
        assert res.status_code == 404 and "deleted" not in res.text, forged


def test_an_old_facebook_deletion_request_cannot_be_replayed(client, app):
    """A captured request replayed later (after the person signed up again) must not delete
    their new account: only fresh requests are honoured."""
    with app.state.db.sessions() as s:
        sign_in(s, _request(), Profile("facebook", "555", None, "Di"))
    for issued in (int(time.time()) - 2 * 3600, None):
        payload = {"algorithm": "HMAC-SHA256", "user_id": "555"} | ({"issued_at": issued} if issued else {})
        res = client.post("/api/facebook/data-deletion", data={"signed_request": _signed(payload, "fb-secret")})
        assert res.status_code == 400, res.text
    with app.state.db.sessions() as s:
        assert s.query(User).count() == 1


def test_facebook_deletion_keeps_an_account_with_other_sign_ins(client, app):
    """Removing the Vault from Facebook deletes what came from Facebook. An account that also
    signs in another way stays, without its Facebook link or the e-mail it brought."""
    with app.state.db.sessions() as s:
        user = sign_in(s, _request(), Profile("facebook", "777", "ed@facebook.example", "Ed"))
        s.add(Identity(user_id=user.id, provider="passkey", subject="handle-ed"))
        s.commit()
    payload = {"algorithm": "HMAC-SHA256", "user_id": "777", "issued_at": int(time.time())}
    assert client.post("/api/facebook/data-deletion", data={"signed_request": _signed(payload, "fb-secret")}).status_code == 200
    with app.state.db.sessions() as s:
        (user,) = s.query(User).all()
        assert [i.provider for i in user.identities] == ["passkey"] and user.email is None


def test_an_address_too_long_to_be_real_is_not_stored(client, app):
    with app.state.db.sessions() as s:
        user = sign_in(s, _request(), Profile("google", "g-long", "a" * 400 + "@example.com", "Lo"))
        assert user.email is None and user.identities[0].email is None
    assert client.post("/api/auth/dev-login", params={"email": "x" * 300}).status_code == 422
