"""Sign out everywhere lists, and offers to remove, the sign-in methods added in the last 24 hours (#347).

A method an attacker added with a copied session (their own passkey, their own Google sign-in) must not survive
"Sign out everywhere" unnoticed: the Account page asks the API for the methods added recently, the person removes
the ones they do not recognise, then signs out everywhere. The criteria in the issue, one test each: a method added
2 hours ago is listed and removable; one added 3 days ago is not highlighted; the last sign-in method is never
removed; another person's methods are never listed or removed. The window is the server's (the page only renders it).
"""

import threading
import time
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from vault import tokens
from vault.app import create_app
from vault.auth import IdentityInUse, Profile, find_or_create, remove_identity
from vault.config import Settings
from vault.models import Identity, Passkey, User, utcnow
from vault.passkeys import remove_passkey

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
    # a passkey is held to the same rule: with every method new, nothing is removed until one is a day old
    [phone] = [m for m in listed if m["kind"] == "passkey"]
    assert signed_in.delete(f"{V1}/me/passkeys/{phone['id']}").status_code == 409


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


@pytest.mark.parametrize("path", ["/me/sign-in-methods"])
def test_the_new_routes_are_documented_in_the_api_doc(path):
    from pathlib import Path

    doc = (Path(__file__).parent.parent / "docs" / "api.md").read_text(encoding="utf-8")
    assert path in doc and "/me/identities/{id}" in doc

# -- second round: what an independent review of the first version found (2026-10-09) ---------------------------------------


def drop_dev_sign_in(client, user_id):
    with sessions(client) as db:
        for row in db.scalars(select(Identity).where(Identity.user_id == user_id, Identity.provider == "dev")):
            db.delete(row)
        db.commit()


def test_a_copied_session_cannot_replace_the_owners_passkeys_with_its_own(veteran):
    """Finding 1: the owner has two old passkeys and nothing else. A copied session registers passkey B and deletes the old
    ones: the first goes, the second is refused, because only B (new) would be left. B can never be the only method."""
    me = veteran.get(f"{V1}/me").json()["id"]
    drop_dev_sign_in(veteran, me)
    first = add_passkey(veteran, me, "Owner phone", hours_ago=24 * 30)
    second = add_passkey(veteran, me, "Owner laptop", hours_ago=24 * 20)
    add_passkey(veteran, me, "B (attacker)", hours_ago=0)
    assert veteran.delete(f"{V1}/me/passkeys/{first}").status_code == 200  # the laptop, older than a day, still stands
    res = veteran.delete(f"{V1}/me/passkeys/{second}")
    assert res.status_code == 409 and "older than 24 hours" in res.json()["detail"]
    names = {m["name"] for m in methods(veteran)["items"]}
    assert names == {"Owner laptop", "B (attacker)"}
    # the only-way-to-sign-in refusal is unchanged when really one method is left
    assert veteran.delete(f"{V1}/me/passkeys/{second}").status_code == 409


def test_a_young_account_removes_no_passkey_either(signed_in):
    me = signed_in.get(f"{V1}/me").json()["id"]
    one = add_passkey(signed_in, me, "Mine", hours_ago=1)
    other = add_passkey(signed_in, me, "Theirs", hours_ago=0)
    for key in (one, other):
        assert signed_in.delete(f"{V1}/me/passkeys/{key}").status_code == 409


def test_every_method_says_why_it_cannot_be_removed(veteran):
    """Finding 3: all methods are listed with their dates; where the rules forbid removing one the answer says why."""
    me = veteran.get(f"{V1}/me").json()["id"]
    add_provider(veteran, me, "microsoft", hours_ago=24 * 40)
    add_passkey(veteran, me, "Old laptop", hours_ago=24 * 40)
    add_passkey(veteran, me, "New phone", hours_ago=2)
    listed = {m["name"]: m for m in methods(veteran)["items"]}
    assert set(listed) == {"Local dev sign-in", "Microsoft", "Old laptop", "New phone"}
    assert all(m["created_at"] for m in listed.values())
    assert listed["Microsoft"]["removable_reason"] == "provider_too_old" and listed["Microsoft"]["removable"] is False
    assert listed["Local dev sign-in"]["removable_reason"] == "provider_too_old"
    assert listed["Old laptop"]["removable"] is True and listed["New phone"]["removable"] is True
    assert listed["New phone"]["recently_added"] is True and listed["Old laptop"]["recently_added"] is False

    young = {m["removable_reason"] for m in methods(veteran, recent_only="true")["items"]}
    assert young == {None}
    with sessions(veteran) as db:  # a day-old account whose methods are all new: nothing can be told apart
        db.execute(text("UPDATE identities SET created_at = now() - interval '1 hour'"))
        db.execute(text("UPDATE passkeys SET created_at = now() - interval '1 hour'"))
        db.commit()
    assert {m["removable_reason"] for m in methods(veteran)["items"]} == {"needs_older_method"}


def test_the_only_method_says_so(signed_in):
    [only] = methods(signed_in)["items"]
    assert only["removable"] is False and only["removable_reason"] == "only_method"


def test_remove_everything_added_in_the_last_day(veteran):
    """Finding 4: a flood of recent methods is one request, and never touches what is older or another person's."""
    me = veteran.get(f"{V1}/me").json()["id"]
    for i in range(6):
        add_passkey(veteran, me, f"Flood {i}", hours_ago=i)
    add_provider(veteran, me, "google", hours_ago=2)
    add_passkey(veteran, me, "Old laptop", hours_ago=24 * 10)
    _, (bobs_recent, bobs_old) = make_user(veteran, [("passkey", 1), ("passkey", 24 * 9)])
    res = veteran.delete(f"{V1}/me/sign-in-methods/recent")
    assert res.status_code == 200 and res.json() == {"deleted": 7}
    assert {m["name"] for m in methods(veteran)["items"]} == {"Local dev sign-in", "Old laptop"}
    with sessions(veteran) as db:  # another person's methods are untouched
        assert {db.scalar(select(Passkey.id).where(Passkey.id == i)) for i in (bobs_recent, bobs_old)} == {bobs_recent, bobs_old}


def test_remove_everything_recent_needs_an_older_method_to_stay(signed_in):
    me = signed_in.get(f"{V1}/me").json()["id"]
    add_passkey(signed_in, me, "Flood", hours_ago=1)
    res = signed_in.delete(f"{V1}/me/sign-in-methods/recent")
    assert res.status_code == 409 and "older than 24 hours" in res.json()["detail"]
    assert len(methods(signed_in)["items"]) == 2


def test_removing_the_last_recent_passkey_drops_the_passkey_handle(veteran):
    me = veteran.get(f"{V1}/me").json()["id"]
    add_passkey(veteran, me, "Flood", hours_ago=1)
    assert veteran.delete(f"{V1}/me/sign-in-methods/recent").json() == {"deleted": 1}
    with sessions(veteran) as db:
        assert db.scalar(select(Identity.id).where(Identity.user_id == me, Identity.provider == "passkey")) is None


def test_next_links_keep_the_page_size(veteran):
    """Finding 5: a client that asked for limit=2 follows next links of 2, over more than 3 rows."""
    me = veteran.get(f"{V1}/me").json()["id"]
    for i in range(4):
        add_passkey(veteran, me, f"Key {i}", hours_ago=i + 1)
    page = veteran.get(f"{V1}/me/sign-in-methods", params={"limit": 2}).json()
    seen, pages = [m["name"] for m in page["items"]], 1
    while "next" in page["_links"]:
        href = page["_links"]["next"]["href"]
        assert "limit=2" in href
        page = veteran.get(href).json()
        assert page["count"] <= 2
        seen += [m["name"] for m in page["items"]]
        pages += 1
    assert pages == 3 and sorted(seen) == sorted(["Key 0", "Key 1", "Key 2", "Key 3", "Local dev sign-in"])


def test_signing_out_the_other_browsers_ends_a_copied_cookie_and_keeps_this_one(client):
    """Finding 2. The order matters: the other browsers are ended FIRST, then the methods are looked at, so a live holder of
    the copied cookie cannot add a way back in after the check. This test needs the new route: nothing else keeps this
    browser signed in while rotating the key."""
    me = login(client, "ann@example.com")
    attacker = TestClient(client.app)
    for name, value in dict(client.cookies).items():
        attacker.cookies.set(name, value)
    assert attacker.get(f"{V1}/me").status_code == 200  # the copy works
    planted = add_passkey(client, me, "Attacker", hours_ago=0)

    assert client.post("/api/auth/sign-out-others").json() == {"ok": True}
    assert attacker.get(f"{V1}/me").status_code == 401  # dead from step one
    assert attacker.delete(f"{V1}/me/passkeys/{planted}").status_code == 401
    assert attacker.post("/api/auth/passkey/register/options").status_code in (401, 404)  # (404: passkeys are off on http)
    assert client.get(f"{V1}/me").json()["id"] == me  # this browser stays signed in, on its re-issued cookie
    assert [m["name"] for m in methods(client, recent_only="true")["items"]] == ["Attacker"]  # and look at what is there
    assert client.delete(f"{V1}/me/sign-in-methods/recent").json() == {"deleted": 1}  # and remove what was planted


def test_signing_out_the_other_browsers_needs_a_session(client):
    assert client.post("/api/auth/sign-out-others").status_code == 401


def test_a_reviewer_session_cannot_use_the_new_routes(database_url):
    settings = Settings(database_url=database_url, session_secret="s" * 32, base_url="http://testserver", dev_login=True,
                        reviewer_passphrase="correct horse battery staple")
    with TestClient(create_app(settings, serve_static=False)) as c:
        assert c.post("/api/auth/reviewer-login", json={"passphrase": "correct horse battery staple"}).status_code == 200
        assert c.get(f"{V1}/me").status_code == 200
        assert c.get(f"{V1}/me/sign-in-methods").status_code == 403
        assert c.delete(f"{V1}/me/identities/1").status_code == 403
        assert c.delete(f"{V1}/me/sign-in-methods/recent").status_code == 403
        assert c.post("/api/auth/sign-out-others").status_code == 403
        assert c.get(f"{V1}/me").status_code == 200  # the demo session was not ended


def make_user(client, methods_):
    """A user with the given methods: ("passkey" | provider, hours_ago) pairs. Returns (id, ids of those methods)."""
    with sessions(client) as db:
        user = User(name="Race")
        db.add(user)
        db.flush()
        ids = []
        for kind, hours in methods_:
            at = utcnow() - timedelta(hours=hours)
            if kind == "passkey":
                if not db.scalar(select(Identity.id).where(Identity.user_id == user.id, Identity.provider == "passkey")):
                    db.add(Identity(user_id=user.id, provider="passkey", subject=f"h-{user.id}"))
                row = Passkey(user_id=user.id, credential_id=f"c-{user.id}-{len(ids)}", public_key=b"k", name=f"K{len(ids)}", created_at=at)
            else:
                row = Identity(user_id=user.id, provider=kind, subject=f"{kind}-{user.id}-{len(ids)}", created_at=at)
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
        return user.id, ids


def test_two_unlinks_at_once_never_leave_the_account_without_a_method(client):
    uid, (old_key, g1, g2) = make_user(client, [("passkey", 24 * 5), ("google", 1), ("microsoft", 1)])
    db = client.app.state.db
    results = []

    def unlink(identity_id):
        with db.sessions() as s:
            try:
                remove_identity(s, uid, identity_id)
                s.commit()
                results.append("deleted")
            except HTTPException as exc:
                results.append(exc.status_code)

    threads = [threading.Thread(target=unlink, args=(i,)) for i in (g1, g2)]
    [t.start() for t in threads]
    [t.join(20) for t in threads]
    assert results == ["deleted", "deleted"]  # both recent ones may go: the old passkey stays
    with db.sessions() as s:
        assert s.scalar(select(Passkey.id).where(Passkey.id == old_key)) == old_key


def test_two_removals_of_the_only_two_old_methods_at_once_leave_one(client):
    """The old methods can each be removed only while the other stays: with the account lock and the check inside the DELETE
    the second of two simultaneous removals is refused instead of leaving nothing."""
    uid, (a, b) = make_user(client, [("passkey", 24 * 5), ("passkey", 24 * 6)])
    db = client.app.state.db
    outcome = {}

    def second():
        with db.sessions() as s:
            try:
                remove_passkey(s, uid, b)
                s.commit()
                outcome["racer"] = "deleted"
            except HTTPException as exc:
                outcome["racer"] = exc.status_code

    with db.sessions() as s:
        remove_passkey(s, uid, a)  # holds the account lock until commit
        racer = threading.Thread(target=second)
        racer.start()
        time.sleep(0.5)
        assert racer.is_alive()
        s.commit()
    racer.join(20)
    assert outcome["racer"] == 409
    with db.sessions() as s:
        assert s.scalar(select(func.count(Passkey.id)).where(Passkey.user_id == uid)) == 1


def test_a_claim_racing_an_unlink_ends_cleanly(client):
    """A provider linked an hour ago on an account that has an older passkey: its owner unlinks it while someone else claims
    it for their own account (as an empty one). Whichever commits first, the other gets a clean answer, never a 500."""
    owner = login(client, "owner@example.com")
    donor, (old_key, g) = make_user(client, [("passkey", 24 * 5), ("google", 1)])
    db = client.app.state.db
    outcome = {}

    def claim():
        with db.sessions() as s:
            try:
                find_or_create(s, Profile("google", f"google-{donor}-1", None, None), current=s.get(User, owner))
                outcome["claim"] = "moved"
            except IdentityInUse:
                outcome["claim"] = "identity_in_use"

    with db.sessions() as s:
        remove_identity(s, donor, g)  # the unlink holds the donor's lock, not yet committed
        racer = threading.Thread(target=claim)
        racer.start()
        time.sleep(0.5)
        assert racer.is_alive()
        s.commit()
    racer.join(20)
    assert outcome["claim"] == "identity_in_use"  # it was gone when the claim got the lock
    with db.sessions() as s:
        assert s.scalar(select(Identity.id).where(Identity.id == g)) is None
