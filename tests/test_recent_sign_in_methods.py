"""Sign out everywhere lists, and offers to remove, the sign-in methods added in the last 24 hours (#347).

A method an attacker added with a copied session (their own passkey, their own Google sign-in) must not survive
"Sign out everywhere" unnoticed: the Account page asks the API for the methods added recently, the person removes
the ones they do not recognise, then signs out everywhere. The criteria in the issue, one test each: a method added
2 hours ago is listed and removable; one added 3 days ago is not highlighted; the last sign-in method is never
removed; another person's methods are never listed or removed. The window is the server's (the page only renders it).
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from vault import tokens
from vault.auth import Profile, find_or_create
from vault.models import Identity, Passkey, User, utcnow

V1 = "/api/v1"


def sessions(client):
    return client.app.state.db.sessions()


def age_sign_ins(client, user_id, days=10):
    """The account has been there a while: the dev sign-in the test client makes is not 'added in the last 24 hours'."""
    with sessions(client) as db:
        for row in db.scalars(select(Identity).where(Identity.user_id == user_id)):
            row.created_at = utcnow() - timedelta(days=days)
        db.commit()


def login(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200
    user_id = client.get(f"{V1}/me").json()["id"]
    age_sign_ins(client, user_id)
    return user_id


@pytest.fixture
def veteran(signed_in):
    age_sign_ins(signed_in, signed_in.get(f"{V1}/me").json()["id"])
    return signed_in


def add_provider(client, user_id, provider, hours_ago, subject=None):
    with sessions(client) as db:
        row = Identity(user_id=user_id, provider=provider, subject=subject or f"{provider}-{user_id}-{hours_ago}",
                       created_at=utcnow() - timedelta(hours=hours_ago))
        db.add(row)
        db.commit()
        return row.id


def add_passkey(client, user_id, name, hours_ago):
    with sessions(client) as db:
        if not db.scalar(select(Identity.id).where(Identity.user_id == user_id, Identity.provider == "passkey")):
            db.add(Identity(user_id=user_id, provider="passkey", subject=f"handle-{user_id}"))
        row = Passkey(user_id=user_id, credential_id=f"cred-{user_id}-{name}", public_key=b"k", name=name,
                      created_at=utcnow() - timedelta(hours=hours_ago))
        db.add(row)
        db.commit()
        return row.id


def methods(client, **params):
    res = client.get(f"{V1}/me/sign-in-methods", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def test_a_method_added_two_hours_ago_is_listed_and_removable(veteran):
    me = veteran.get(f"{V1}/me").json()["id"]
    google = add_provider(veteran, me, "google", hours_ago=2)
    phone = add_passkey(veteran, me, "Attacker phone", hours_ago=2)

    recent = methods(veteran, recent_only="true")
    assert recent["recent_hours"] == 24 and recent["total"] == 2
    by_kind = {m["kind"]: m for m in recent["items"]}
    assert by_kind["provider"]["name"] == "Google" and by_kind["provider"]["recently_added"] is True
    assert by_kind["passkey"]["name"] == "Attacker phone" and 119 <= by_kind["passkey"]["added_minutes_ago"] <= 121
    assert all(m["removable"] for m in recent["items"])
    assert by_kind["provider"]["_links"]["self"]["href"] == f"{V1}/me/identities/{google}"
    assert by_kind["passkey"]["_links"]["self"]["href"] == f"{V1}/me/passkeys/{phone}"

    # the sign-out-everywhere flow: remove what is not yours, then sign out everywhere
    assert veteran.delete(f"{V1}/me/identities/{google}").json() == {"deleted": True}
    assert veteran.delete(f"{V1}/me/passkeys/{phone}").json() == {"deleted": True}
    assert methods(veteran, recent_only="true")["items"] == []
    with sessions(veteran) as db:
        assert db.scalar(select(Identity.id).where(Identity.id == google)) is None
        assert db.scalar(select(Passkey.id).where(Passkey.id == phone)) is None
        # the account's passkey handle goes with its last passkey, as before
        assert db.scalar(select(Identity.id).where(Identity.user_id == me, Identity.provider == "passkey")) is None


def test_a_method_added_three_days_ago_is_not_highlighted(veteran):
    me = veteran.get(f"{V1}/me").json()["id"]
    add_provider(veteran, me, "microsoft", hours_ago=72)
    add_passkey(veteran, me, "Old laptop", hours_ago=72)
    add_passkey(veteran, me, "New phone", hours_ago=1)

    recent = methods(veteran, recent_only="true")
    assert [m["name"] for m in recent["items"]] == ["New phone"]
    everything = methods(veteran)
    assert everything["total"] == 4  # the sign-in the account was made with, two old methods and the new one
    assert {m["name"]: m["recently_added"] for m in everything["items"]} == {
        "Local dev sign-in": False, "Microsoft": False, "Old laptop": False, "New phone": True}
    assert everything["items"][0]["name"] == "New phone"  # newest first


def test_the_window_edge_is_24_hours(veteran):
    me = veteran.get(f"{V1}/me").json()["id"]
    add_provider(veteran, me, "google", hours_ago=23)
    add_provider(veteran, me, "apple", hours_ago=25)
    names = {m["name"] for m in methods(veteran, recent_only="true")["items"]}
    assert "Google" in names and "Apple" not in names


def test_the_last_sign_in_method_is_never_removed(signed_in):
    veteran = signed_in  # a brand-new account: its one sign-in was added just now
    me = veteran.get(f"{V1}/me").json()["id"]
    [only] = methods(veteran)["items"]
    assert only["removable"] is False  # the page shows "your only way to sign in", no Remove button
    res = veteran.delete(f"{V1}/me/identities/{only['id']}")
    assert res.status_code == 409 and "only way to sign in" in res.json()["detail"]

    add_provider(veteran, me, "google", hours_ago=1)
    assert veteran.delete(f"{V1}/me/identities/{only['id']}").status_code == 409  # two new methods: nothing is unlinked yet (below)


def test_a_young_account_cannot_unlink_anything_a_copied_session_could_have_swapped(signed_in):
    """Signed up this morning with one sign-in; a copied session adds its own passkey and would unlink the owner's only provider.
    With every method new, nothing tells whose is whose, so the unlink is refused until a method older than the window remains."""
    me = signed_in.get(f"{V1}/me").json()["id"]
    [owner] = methods(signed_in)["items"]
    add_passkey(signed_in, me, "Attacker phone", hours_ago=1)
    listed = methods(signed_in)["items"]
    assert not any(m["removable"] for m in listed if m["kind"] == "provider")
    res = signed_in.delete(f"{V1}/me/identities/{owner['id']}")
    assert res.status_code == 409 and "older than 24 hours" in res.json()["detail"]
    with sessions(signed_in) as db:
        assert db.scalar(select(Identity.id).where(Identity.id == owner["id"])) == owner["id"]
    # the attacker's passkey itself is removable, as before, and signing out everywhere still works
    [phone] = [m for m in listed if m["kind"] == "passkey"]
    assert signed_in.delete(f"{V1}/me/passkeys/{phone['id']}").status_code == 200


def test_a_passkey_counts_as_a_way_to_sign_in_when_a_provider_is_removed(signed_in):
    veteran = signed_in
    me = veteran.get(f"{V1}/me").json()["id"]
    [dev] = methods(veteran)["items"]
    add_passkey(veteran, me, "Phone", hours_ago=48)
    assert veteran.delete(f"{V1}/me/identities/{dev['id']}").status_code == 200  # the older passkey remains
    [phone] = methods(veteran)["items"]
    assert phone["kind"] == "passkey" and phone["removable"] is False
    assert veteran.delete(f"{V1}/me/passkeys/{phone['id']}").status_code == 409


def test_only_a_provider_linked_in_the_last_day_can_be_unlinked(veteran):
    """Unlinking is for what a copied session linked. An older provider is never removable through this route, so a copied
    session cannot link its own sign-in and then unlink every one the owner has used for longer."""
    me = veteran.get(f"{V1}/me").json()["id"]
    old = add_provider(veteran, me, "microsoft", hours_ago=72)
    assert {m["name"]: m["removable"] for m in methods(veteran)["items"]} == {"Local dev sign-in": False, "Microsoft": False}
    res = veteran.delete(f"{V1}/me/identities/{old}")
    assert res.status_code == 409 and "last 24 hours" in res.json()["detail"]
    with sessions(veteran) as db:
        assert db.scalar(select(Identity.id).where(Identity.id == old)) == old


def test_the_passkey_handle_is_not_a_method_of_its_own(veteran):
    me = veteran.get(f"{V1}/me").json()["id"]
    add_passkey(veteran, me, "Phone", hours_ago=1)
    with sessions(veteran) as db:
        handle = db.scalar(select(Identity.id).where(Identity.user_id == me, Identity.provider == "passkey"))
    assert {m["kind"] for m in methods(veteran)["items"]} == {"provider", "passkey"}
    assert veteran.delete(f"{V1}/me/identities/{handle}").status_code == 404  # removed only with its last passkey
    with sessions(veteran) as db:
        assert db.scalar(select(Identity.id).where(Identity.id == handle)) == handle


def test_another_persons_methods_are_never_listed_or_removed(client):
    alice = login(client, "alice@example.com")
    theirs = add_provider(client, alice, "google", hours_ago=1)
    their_key = add_passkey(client, alice, "Alice phone", hours_ago=1)
    bob = login(client, "bob@example.com")
    add_provider(client, bob, "microsoft", hours_ago=1)

    for params in ({}, {"recent_only": "true"}):
        listed = methods(client, **params)
        assert {m["name"] for m in listed["items"]} <= {"Local dev sign-in", "Microsoft"}
        assert listed["total"] == len(listed["items"])
    assert client.delete(f"{V1}/me/identities/{theirs}").status_code == 404
    assert client.delete(f"{V1}/me/passkeys/{their_key}").status_code == 404
    with sessions(client) as db:  # nothing of Alice's changed
        assert db.scalar(select(Identity.id).where(Identity.id == theirs)) == theirs
        assert db.scalar(select(Passkey.id).where(Passkey.id == their_key)) == their_key
    assert bob != alice


def test_ids_too_big_for_the_database_are_invalid(veteran):
    assert veteran.delete(f"{V1}/me/identities/{2**31}").status_code == 422
    assert veteran.delete(f"{V1}/me/identities/0").status_code == 422


def test_a_sign_in_moved_over_from_an_older_empty_account_counts_from_the_move(client):
    """Linking someone's own Google sign-in, which they signed in with a week ago (so it has an empty account of its
    own), moves it to the account that links it. It must show as added now, not a week ago: that is how a method
    linked with a copied session could otherwise hide behind an old date."""
    owner = login(client, "owner@example.com")
    with sessions(client) as db:
        stranger = User(email="stranger@example.com")
        stranger.identities.append(Identity(provider="google", subject="stranger-sub", email="stranger@example.com",
                                            created_at=utcnow() - timedelta(days=7)))
        db.add(stranger)
        db.commit()
        user = db.get(User, owner)
        find_or_create(db, Profile("google", "stranger-sub", "stranger@example.com", None), current=user)
    recent = methods(client, recent_only="true")
    assert [m["name"] for m in recent["items"] if m["provider"] == "google"] == ["Google"]
    assert recent["items"][0]["added_minutes_ago"] <= 1


def test_the_list_is_paged(veteran):
    me = veteran.get(f"{V1}/me").json()["id"]
    for i in range(3):
        add_passkey(veteran, me, f"Key {i}", hours_ago=i + 1)
    first = veteran.get(f"{V1}/me/sign-in-methods", params={"limit": 2}).json()
    assert first["count"] == 2 and first["total"] == 4 and "next" in first["_links"]
    second = veteran.get(first["_links"]["next"]["href"]).json()
    assert second["count"] == 2 and not {m["name"] for m in first["items"]} & {m["name"] for m in second["items"]}


def test_only_the_person_can_manage_sign_in_methods(client):
    assert client.get(f"{V1}/me/sign-in-methods").status_code == 401
    assert client.delete(f"{V1}/me/identities/1").status_code == 401
    user_id = login(client, "ann@example.com")
    google = add_provider(client, user_id, "google", hours_ago=1)
    with sessions(client) as db:
        _, secret = tokens.create_pat(db, db.get(User, user_id), "script", ["read"], 30)
        db.commit()
    bearer = {"Authorization": f"Bearer {secret}"}
    client.cookies.clear()
    assert client.get(f"{V1}/me/sign-in-methods", headers=bearer).status_code == 403  # a token has no account-level powers
    assert client.delete(f"{V1}/me/identities/{google}", headers=bearer).status_code == 403


def test_removing_a_method_then_signing_out_everywhere_ends_the_copied_session(client):
    """The whole flow the page runs, with a second browser holding a copy of the session."""
    me = login(client, "ann@example.com")
    attacker_key = add_passkey(client, me, "Attacker", hours_ago=1)
    copied = dict(client.cookies)
    assert client.delete(f"{V1}/me/passkeys/{attacker_key}").status_code == 200
    assert client.post("/api/auth/logout", params={"everywhere": "true"}).status_code == 200
    client.cookies.clear()
    for name, value in copied.items():
        client.cookies.set(name, value)
    assert client.get(f"{V1}/me").status_code == 401  # the copied cookie no longer works


@pytest.mark.parametrize("path", ["/me/sign-in-methods"])
def test_the_new_routes_are_documented_in_the_api_doc(path):
    from pathlib import Path

    doc = (Path(__file__).parent.parent / "docs" / "api.md").read_text(encoding="utf-8")
    assert path in doc and "/me/identities/{id}" in doc
