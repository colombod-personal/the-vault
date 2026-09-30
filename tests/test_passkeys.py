"""Passkeys end to end with a software authenticator (twins.authenticator): sign-up, sign-in,
adding and removing passkeys, and the attacks WebAuthn must stop."""

import pytest
from fastapi.testclient import TestClient

from twins.authenticator import SoftAuthenticator
from vault.app import create_app
from vault.config import Settings

ORIGIN = "https://vault.test"
V1 = "/api/v1"


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/pk.db", session_secret="s" * 32, base_url=ORIGIN)
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
    assert client.delete(f"{V1}/me/passkeys/{phone_id}").json() == {"deleted": True}
    last = client.get(f"{V1}/me/passkeys").json()["items"][0]["id"]
    res = client.delete(f"{V1}/me/passkeys/{last}")
    assert res.status_code == 409 and "only way to sign in" in res.json()["detail"]  # no lock-out
    client.cookies.clear()
    assert login(client, phone).status_code == 401  # the removed passkey no longer works


def test_passkey_can_be_added_to_an_existing_account(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/x.db", session_secret="s" * 32,
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


def test_not_offered_without_https(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/y.db", session_secret="s", base_url="http://vault.example")
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
