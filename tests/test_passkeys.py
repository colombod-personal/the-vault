"""Passkeys end to end with a software authenticator (twins.authenticator): sign-up, sign-in,
adding and removing passkeys, and the attacks WebAuthn must stop."""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from webauthn.helpers import bytes_to_base64url

from twins.authenticator import SoftAuthenticator
from vault.app import create_app
from vault.config import Settings
from vault.db import Base, Database
from vault.models import Identity, Passkey, User
from vault.passkeys import remove_passkey

ORIGIN = "https://vault.test"
V1 = "/api/v1"


@pytest.fixture
def client(database_url, tmp_path):
    settings = Settings(database_url=database_url, session_secret="s" * 32, base_url=ORIGIN)
    with TestClient(create_app(settings, serve_static=False), base_url=ORIGIN) as c:
        yield c


def post(client, path, body=None):
    return client.post(path, json=body or {}, headers={"Origin": ORIGIN})


def signup(client, device, name="Ann"):
    options = post(client, "/api/auth/passkey/signup/options", {"name": name}).json()
    assert options["rp"] == {"id": "vault.test", "name": "The Vault"}
    assert options["authenticatorSelection"]["residentKey"] == "required"
    return post(client, "/api/auth/passkey/signup/verify", {"credential": device.create(options, ORIGIN), "name": "Phone"})


def login(client, device, **kw):
    options = post(client, "/api/auth/passkey/login/options").json()
    return post(client, "/api/auth/passkey/login/verify", {"credential": device.get(options, ORIGIN, **kw)})


def test_available_on_https_and_advertised(client):
    assert client.get("/api/auth/providers").json()["passkeys"] is True


def test_sign_up_and_sign_in_with_a_passkey(client):
    phone = SoftAuthenticator()
    res = signup(client, phone)
    assert res.status_code == 200 and res.json()["signed_in"] is True
    me = client.get(f"{V1}/me").json()
    assert (me["name"], me["providers"], me["email"]) == ("Ann", ["passkey"], None)
    assert client.get(f"{V1}/me/passkeys").json()["items"][0]["name"] == "Phone"

    client.cookies.clear()
    assert client.get(f"{V1}/me").status_code == 401
    assert login(client, phone).json() == {"signed_in": True}
    assert client.get(f"{V1}/me").json()["id"] == me["id"]
    listed = client.get(f"{V1}/me/passkeys").json()["items"][0]
    assert listed["last_used_at"] and listed["synced"] is True


def test_add_a_second_passkey_and_remove_one(client):
    phone, laptop = SoftAuthenticator(), SoftAuthenticator()
    signup(client, phone)
    options = post(client, "/api/auth/passkey/register/options").json()
    assert len(options["excludeCredentials"]) == 1  # the phone's passkey isn't created twice
    with pytest.raises(PermissionError):
        phone.create(options, ORIGIN)
    added = post(client, "/api/auth/passkey/register/verify", {"credential": laptop.create(options, ORIGIN), "name": "Laptop"})
    assert added.json()["added"] is True
    keys = client.get(f"{V1}/me/passkeys").json()["items"]
    assert {k["name"] for k in keys} == {"Phone", "Laptop"}

    client.cookies.clear()
    login(client, laptop)  # either device opens the same account
    phone_id = next(k["id"] for k in keys if k["name"] == "Phone")
    # Both are new, and a passkey is removed only while another method older than a day stays (#347): a day passes.
    with client.app.state.db.sessions() as db:
        db.execute(text("UPDATE passkeys SET created_at = now() - interval '2 days'"))
        db.commit()
    assert client.delete(f"{V1}/me/passkeys/{phone_id}").json() == {"deleted": True}
    last = client.get(f"{V1}/me/passkeys").json()["items"][0]["id"]
    res = client.delete(f"{V1}/me/passkeys/{last}")
    assert res.status_code == 409 and "only way to sign in" in res.json()["detail"]  # no lock-out
    client.cookies.clear()
    assert login(client, phone).status_code == 401  # the removed passkey no longer works


def test_passkey_can_be_added_to_an_existing_account(database_url, tmp_path):
    settings = Settings(database_url=database_url, session_secret="s" * 32,
                        base_url="http://localhost:8000", dev_login=True)
    with TestClient(create_app(settings, serve_static=False), base_url="http://localhost:8000") as c:
        c.post("/api/auth/dev-login")
        first = c.get(f"{V1}/me").json()["id"]
        options = c.post("/api/auth/passkey/register/options").json()
        key = SoftAuthenticator()
        c.post("/api/auth/passkey/register/verify", json={"credential": key.create(options, "http://localhost:8000")})
        c.cookies.clear()
        options = c.post("/api/auth/passkey/login/options").json()
        c.post("/api/auth/passkey/login/verify", json={"credential": key.get(options, "http://localhost:8000")})
        me = c.get(f"{V1}/me").json()
        assert me["id"] == first and me["providers"] == ["dev", "passkey"]


@pytest.mark.parametrize("attack", ["wrong_origin", "replayed", "tampered", "no_ceremony", "wrong_rp"])
def test_webauthn_rejects(client, attack):
    phone = SoftAuthenticator()
    signup(client, phone)
    client.cookies.clear()
    options = post(client, "/api/auth/passkey/login/options").json()
    if attack == "wrong_origin":  # a phishing site relaying the challenge
        credential = phone.get(options, "https://vau1t.test")
    elif attack == "tampered":
        phone.tamper = True
        credential = phone.get(options, ORIGIN)
    elif attack == "wrong_rp":  # the right passkey, but its signature is scoped to another site's rp id
        phone.credentials[0].rp_id = "evil.test"
        credential = phone.get(options | {"rpId": "evil.test"}, ORIGIN)
    else:
        credential = phone.get(options, ORIGIN)
    if attack == "replayed":
        assert post(client, "/api/auth/passkey/login/verify", {"credential": credential}).status_code == 200
        client.cookies.clear()
        post(client, "/api/auth/passkey/login/options")  # a new challenge; the old answer must not work
    if attack == "no_ceremony":
        client.cookies.clear()
    res = post(client, "/api/auth/passkey/login/verify", {"credential": credential})
    expected = {"wrong_origin": "origin", "replayed": "challenge", "tampered": "signature",
                "no_ceremony": "No passkey request", "wrong_rp": "RP ID"}[attack]
    assert expected in res.json()["detail"], res.json()["detail"]
    assert res.status_code in (400, 401), res.text
    assert client.get(f"{V1}/me").status_code == 401


def test_user_verification_is_required(client):
    """A passkey can be an account's only factor, so it must come with a PIN or biometric."""
    options = post(client, "/api/auth/passkey/signup/options", {"name": "Ann"}).json()
    assert options["authenticatorSelection"]["userVerification"] == "required"
    res = post(client, "/api/auth/passkey/signup/verify",
               {"credential": SoftAuthenticator(user_verified=False).create(options, ORIGIN)})
    assert res.status_code == 400 and "verif" in res.json()["detail"].lower()
    assert client.get(f"{V1}/me").status_code == 401

    phone = SoftAuthenticator()
    signup(client, phone)
    client.cookies.clear()
    phone.user_verified = False  # e.g. a security key touched without its PIN
    options = post(client, "/api/auth/passkey/login/options").json()
    assert options["userVerification"] == "required"
    res = post(client, "/api/auth/passkey/login/verify", {"credential": phone.get(options, ORIGIN)})
    assert res.status_code == 401 and "verif" in res.json()["detail"].lower()
    assert client.get(f"{V1}/me").status_code == 401


def test_personal_access_tokens_cannot_add_passkeys(client):
    signup(client, SoftAuthenticator())
    token = post(client, f"{V1}/me/tokens", {"name": "bot", "scopes": ["read", "write"]}).json()["token"]
    with TestClient(client.app, base_url=ORIGIN) as bot:
        res = bot.post("/api/auth/passkey/register/options", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_passkeys_are_erased_and_exported(client):
    signup(client, SoftAuthenticator())
    import io
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(client.get(f"{V1}/me/export").content))
    assert "passkeys.json" in z.namelist() and "Phone" in z.read("passkeys.json").decode()
    removed = client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}, headers={"Origin": ORIGIN}).json()["removed"]
    assert removed["passkeys"] == 1 and removed["identities"] == 1


def test_not_offered_without_https(database_url, tmp_path):
    settings = Settings(database_url=database_url, session_secret="s", base_url="http://vault.example")
    with TestClient(create_app(settings, serve_static=False)) as c:
        assert c.get("/api/auth/providers").json()["passkeys"] is False
        assert c.post("/api/auth/passkey/login/options").status_code == 404


def _replay(client, path, cookie, body):
    """Send `body` again with the session cookie as it was before the first verify."""
    client.cookies.clear()
    client.cookies.set("vault_session", cookie)
    return post(client, path, body)


def test_a_sign_in_challenge_is_used_once_even_with_a_saved_cookie(client):
    """The challenge lives on the server and is consumed atomically: replaying the pre-sign-in
    cookie with the same answer (synced passkeys keep a zero counter) does not sign in again."""
    phone = SoftAuthenticator(counter_step=0)  # counter stays 0, like iCloud and Google passkeys
    signup(client, phone)
    client.cookies.clear()
    options = post(client, "/api/auth/passkey/login/options").json()
    cookie = client.cookies.get("vault_session")
    body = {"credential": phone.get(options, ORIGIN)}
    assert post(client, "/api/auth/passkey/login/verify", body).status_code == 200
    replay = _replay(client, "/api/auth/passkey/login/verify", cookie, body)
    assert replay.status_code == 400 and client.get(f"{V1}/me").status_code == 401


def test_a_sign_up_challenge_is_used_once_even_with_a_saved_cookie(client):
    phone = SoftAuthenticator()
    options = post(client, "/api/auth/passkey/signup/options", {"name": "Ann"}).json()
    cookie = client.cookies.get("vault_session")
    body = {"credential": phone.create(options, ORIGIN), "name": "Phone"}
    assert post(client, "/api/auth/passkey/signup/verify", body).status_code == 200
    replay = _replay(client, "/api/auth/passkey/signup/verify", cookie, body)
    assert replay.status_code == 400


def test_two_removals_at_once_cannot_remove_the_last_passkey(database_url, tmp_path):
    """Two passkeys, no other sign-in: removing both at the same time must leave one."""
    db = Database(database_url)
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.migrate()
    with Session(db.engine) as s:
        user = User(name="Ann")
        s.add(user)
        s.flush()
        s.add(Identity(user_id=user.id, provider="passkey", subject="h"))
        keys = [Passkey(user_id=user.id, credential_id=f"c{i}", public_key=b"k", name=f"Key {i}",
                        created_at=datetime.now(timezone.utc) - timedelta(days=2)) for i in (1, 2)]  # older than a day (#347)
        s.add_all(keys)
        s.commit()
        uid, (first, second) = user.id, [k.id for k in keys]

    outcome = {}

    def remove_the_other_one():
        with Session(db.engine) as s:
            try:
                remove_passkey(s, uid, first, fresh=True)
                s.commit()
                outcome["result"] = "deleted"
            except HTTPException as exc:
                outcome["result"] = exc.status_code

    with Session(db.engine) as s:
        remove_passkey(s, uid, second, fresh=True)  # holds the account lock until commit
        racer = threading.Thread(target=remove_the_other_one)
        racer.start()
        time.sleep(0.5)
        assert racer.is_alive()  # waiting for the lock, not deciding on stale data
        s.commit()
    racer.join(10)
    assert outcome["result"] == 409
    with Session(db.engine) as s:
        assert s.scalar(select(func.count(Passkey.id)).where(Passkey.user_id == uid)) == 1
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.engine.dispose()


def test_passkeys_added_from_two_devices_at_once_both_sign_in(database_url, tmp_path):
    """An account without a passkey yet, registering on two devices at the same time: both
    passkeys must carry the account's one WebAuthn user handle, or the second is refused."""
    origin = "http://localhost:8000"
    settings = Settings(database_url=database_url, session_secret="s" * 32,
                        base_url=origin, dev_login=True)
    app = create_app(settings, serve_static=False)
    with TestClient(app, base_url=origin) as a, TestClient(app, base_url=origin) as b:
        a.post("/api/auth/dev-login")
        b.post("/api/auth/dev-login")  # the same account, a second session
        phone, laptop = SoftAuthenticator(), SoftAuthenticator()
        options_a = a.post("/api/auth/passkey/register/options").json()
        options_b = b.post("/api/auth/passkey/register/options").json()
        assert a.post("/api/auth/passkey/register/verify",
                      json={"credential": phone.create(options_a, origin)}).status_code == 200
        second = b.post("/api/auth/passkey/register/verify", json={"credential": laptop.create(options_b, origin)})
        if second.status_code == 200:  # kept: it must work for signing in
            b.cookies.clear()
            options = b.post("/api/auth/passkey/login/options").json()
            res = b.post("/api/auth/passkey/login/verify", json={"credential": laptop.get(options, origin)})
            assert res.status_code == 200, res.text
        else:
            assert second.status_code == 409


def test_two_sign_ins_at_once_cannot_roll_the_counter_back(database_url):
    """A hardware key counts its uses. Two sign-ins verified against the same stored count
    must not let the lower one be written last."""
    db = Database(database_url)
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    settings = Settings(database_url=database_url, session_secret="s" * 32, base_url=ORIGIN)
    app = create_app(settings, serve_static=False)
    key = SoftAuthenticator()
    outcome = {}
    with TestClient(app, base_url=ORIGIN) as first, TestClient(app, base_url=ORIGIN) as second:
        assert signup(first, key).status_code == 200
        first.cookies.clear()
        earlier = key.get(post(first, "/api/auth/passkey/login/options").json(), ORIGIN)   # count 1
        later_options = post(second, "/api/auth/passkey/login/options").json()
        key.get(later_options, ORIGIN)  # count 2, verified and saved by the other request (below)

        def verify_the_earlier_one():
            outcome["status"] = post(first, "/api/auth/passkey/login/verify", {"credential": earlier}).status_code

        with Session(db.engine) as s:  # the other request is mid-verify and holds the passkey row
            s.execute(select(Passkey).with_for_update()).all()
            racer = threading.Thread(target=verify_the_earlier_one)
            racer.start()
            time.sleep(0.5)
            assert racer.is_alive()  # waits for the row instead of checking against a stale count
            s.execute(text("UPDATE passkeys SET sign_count = 2"))
            s.commit()
        racer.join(10)
    assert outcome["status"] == 401  # count 1 after 2: refused, as a cloned key would be
    with Session(db.engine) as s:
        assert s.scalar(select(Passkey.sign_count)) == 2
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.engine.dispose()


@pytest.mark.parametrize("transports, kept", [
    (5, []), ("usb", []), ({"a": 1}, []),
    (["internal", 7, None, "x" * 33, "hybrid"], ["internal", "hybrid"]),
    (["usb"] * 20, ["usb"] * 8),
])
def test_passkey_transports_keep_only_short_strings(client, transports, kept):
    device = SoftAuthenticator()
    options = post(client, "/api/auth/passkey/signup/options", {"name": "Ann"}).json()
    credential = device.create(options, ORIGIN)
    credential["response"]["transports"] = transports
    res = post(client, "/api/auth/passkey/signup/verify", {"credential": credential})
    assert res.status_code == 200, res.text
    with Session(client.app.state.db.engine) as s:
        assert s.get(Passkey, res.json()["passkey_id"]).transports == kept


def test_a_sign_in_without_the_accounts_user_handle_is_refused(client):
    """Sign-in uses discoverable passkeys, which always return the account's user handle: an
    assertion without it (or with another account's) is refused, not trusted on the key alone."""
    phone = SoftAuthenticator()
    assert signup(client, phone).status_code == 200
    client.cookies.clear()
    assertion = phone.get(post(client, "/api/auth/passkey/login/options").json(), ORIGIN)
    assertion["response"].pop("userHandle", None)
    res = post(client, "/api/auth/passkey/login/verify", {"credential": assertion})
    assert res.status_code == 401 and client.get(f"{V1}/me").status_code == 401


def _fill_passkeys(client, user_id, count, start=0):
    with client.app.state.db.sessions() as db:
        db.add_all([Passkey(user_id=user_id, credential_id=bytes_to_base64url(f"filler-{start + i}".encode()), public_key=b"k", name=f"Filler {start + i}")
                    for i in range(count)])
        db.commit()


def test_an_account_holds_at_most_twenty_passkeys(client):  # #347: a copied session cannot bury the real ones under hundreds
    from vault.passkeys import MAX_PASSKEYS

    assert MAX_PASSKEYS == 20
    signup(client, SoftAuthenticator())
    uid = client.get(f"{V1}/me").json()["id"]
    _fill_passkeys(client, uid, 18)  # 19 now: one more fits
    options = post(client, "/api/auth/passkey/register/options")
    assert options.status_code == 200
    _fill_passkeys(client, uid, 1, start=100)  # another device took the last place while this request was open
    late = post(client, "/api/auth/passkey/register/verify",
                {"credential": SoftAuthenticator().create(options.json(), ORIGIN), "name": "One too many"})
    assert late.status_code == 409 and "20 passkeys" in late.json()["detail"]
    full = post(client, "/api/auth/passkey/register/options")
    assert full.status_code == 409 and "Remove one you no longer use first" in full.json()["detail"]
    with client.app.state.db.sessions() as db:
        assert db.scalar(select(func.count(Passkey.id)).where(Passkey.user_id == uid)) == 20


def test_an_attacker_who_signs_in_through_a_planted_passkey_ends_when_the_owner_removes_it(client):
    """#347: a copied cookie registers the attacker's own passkey; the owner signs out the other browsers; the attacker signs
    in with the planted passkey (a fresh cookie on the account's key); the owner removes it. The key changes with the removal,
    so the attacker's new cookie is dead and the owner's browser still works."""
    owner_phone, attacker_key = SoftAuthenticator(), SoftAuthenticator()
    signup(client, owner_phone)
    with client.app.state.db.sessions() as db:  # the owner's passkey has been there a while
        db.execute(text("UPDATE passkeys SET created_at = now() - interval '5 days'"))
        db.commit()
    attacker = TestClient(client.app, base_url=ORIGIN)
    for name, value in dict(client.cookies).items():
        attacker.cookies.set(name, value)
    options = post(attacker, "/api/auth/passkey/register/options").json()
    assert post(attacker, "/api/auth/passkey/register/verify",
                {"credential": attacker_key.create(options, ORIGIN), "name": "Attacker"}).json()["added"] is True

    assert post(client, "/api/auth/sign-out-others").json()["ok"] is True  # step 1
    assert attacker.get(f"{V1}/me").status_code == 401  # the copied cookie is dead...
    attacker.cookies.clear()
    assert login(attacker, attacker_key).json() == {"signed_in": True}  # ...but the planted passkey signs in again
    assert attacker.get(f"{V1}/me").status_code == 200

    planted = next(k["id"] for k in client.get(f"{V1}/me/passkeys").json()["items"] if k["name"] == "Attacker")
    assert client.delete(f"{V1}/me/passkeys/{planted}").json() == {"deleted": True}  # step 2: the owner removes it
    assert attacker.get(f"{V1}/me").status_code == 401  # the attacker's new session ends with it
    assert client.get(f"{V1}/me").status_code == 200  # the owner stays signed in


def test_a_passkey_request_already_past_authentication_cannot_add_after_the_session_ended(client, monkeypatch):
    """#347: the account's session key is replaced after the request was authenticated and before the write. The key is
    re-read under the account lock, so the request is refused (401) and no passkey is added."""
    from vault import passkeys as passkeys_module
    from vault.models import new_session_key

    signup(client, SoftAuthenticator())
    uid = client.get(f"{V1}/me").json()["id"]
    options = post(client, "/api/auth/passkey/register/options").json()
    real_take = passkeys_module._take

    def take_then_rotate(request, db, kind):
        pending = real_take(request, db, kind)
        with client.app.state.db.sessions() as other:  # sign out everywhere commits while this request is running
            other.get(User, uid).session_key = new_session_key()
            other.commit()
        return pending

    monkeypatch.setattr(passkeys_module, "_take", take_then_rotate)
    res = post(client, "/api/auth/passkey/register/verify", {"credential": SoftAuthenticator().create(options, ORIGIN), "name": "Late"})
    assert res.status_code == 401 and "session ended" in res.json()["detail"]
    with client.app.state.db.sessions() as db:
        assert db.scalar(select(func.count(Passkey.id)).where(Passkey.user_id == uid)) == 1
