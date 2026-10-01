"""Concurrent requests and stale credentials: what one request does while another is running,
and what an old cookie or link can still do afterwards."""

import os
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from vault.models import ApiSession, CollectionValue, Entry, Identity, User

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
OTHER = b"Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET\n" \
        b"Box,1,0,Sol Ring,C21,Commander 2021,263,NearMint,Normal,English,1.00,2024-01-01,1,1,1\n"
V1 = "/api/v1"


def upload(client, content=CSV, name="export.csv"):
    return client.post(f"{V1}/imports", files={"file": (name, content, "text/csv")})


def login(client, email):
    client.cookies.clear()
    res = client.post("/api/auth/dev-login", params={"email": email})
    assert res.status_code == 200
    return res.json()["id"]


def test_two_imports_at_once_do_not_merge_their_files(app, signed_in, monkeypatch):
    """Each import replaces the collection. When another import finishes while one is running,
    the later one is refused (409) rather than leaving both files' cards in the collection."""
    from vault import importer

    real, raced = importer.user_entries, []

    def other_import_meanwhile(db, user):
        rows = real(db, user)
        if not raced:
            raced.append(True)
            with app.state.db.sessions() as other:  # another device's import, finishing first
                importer.import_collection(other, other.get(User, user.id), "other.csv", OTHER)
                other.commit()
        return rows

    monkeypatch.setattr(importer, "user_entries", other_import_meanwhile)
    res = upload(signed_in)
    assert res.status_code == 409, res.text
    names = [c["name"] for c in signed_in.get(f"{V1}/collection/cards").json()["items"]]
    assert names == ["Sol Ring"]  # the file that finished first, alone
    monkeypatch.setattr(importer, "user_entries", real)
    assert upload(signed_in).status_code == 201  # a fresh try works


def test_the_daily_sync_survives_an_import_that_deleted_rows_it_read(app, signed_in, monkeypatch):
    """The sync reads every row, resolves for minutes, then writes the matches. A user who
    re-imported meanwhile deleted some of those rows: the sync skips them instead of failing
    (and losing everyone's prices for the day)."""
    from tests.test_api import BULK
    from vault import sync as sync_module

    assert upload(signed_in).status_code == 201
    real = sync_module.resolve_offline

    def import_meanwhile(entries, cards):
        with app.state.db.sessions() as other:
            other.execute(delete(Entry).where(Entry.name == "Sol Ring"))
            other.commit()
        return real(entries, cards)

    monkeypatch.setattr(sync_module, "resolve_offline", import_meanwhile)
    with app.state.db.sessions() as db:
        stats = sync_module.sync(db, BULK, day=date(2026, 9, 27))
    assert stats["printings_priced"] >= 1
    with app.state.db.sessions() as db:
        assert db.scalar(select(CollectionValue.market_usd).where(CollectionValue.day == date(2026, 9, 27))) > 0


def test_a_token_signed_out_while_in_use_is_just_invalid(app, client):
    """The access token's session is deleted (signed out elsewhere) between the lookup and the
    "last used" write: the request is unauthenticated (401), not a server error."""
    from vault import tokens

    uid = login(client, "t@example.com")
    with app.state.db.sessions() as db:
        issued = tokens.issue(db, db.get(User, uid), "app", "phone")
    with app.state.db.sessions() as db:
        real_scalar = db.scalar

        def signed_out_meanwhile(stmt):
            row = real_scalar(stmt)
            with app.state.db.sessions() as other:
                other.execute(delete(ApiSession).where(ApiSession.user_id == uid))
                other.commit()
            return row

        db.scalar = signed_out_meanwhile
        assert tokens.authenticate(db, issued["access_token"]) is None


def test_an_account_has_one_passkey_identity_even_without_row_locks(app, client):
    """Two devices adding an account's first passkey at once each bring their own WebAuthn user
    handle. The database keeps one, so the other passkey can't end up unable to sign in."""
    uid = login(client, "p@example.com")
    with app.state.db.sessions() as db:
        db.add(Identity(user_id=uid, provider="passkey", subject="handle-1"))
        db.commit()
        db.add(Identity(user_id=uid, provider="passkey", subject="handle-2"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_deleted_accounts_cookie_does_not_sign_in_the_next_account(client):
    """SQLite reuses a deleted row's id. A cookie left from a deleted account must not sign in
    whoever gets that id next."""
    first = login(client, "gone@example.com")
    stale = dict(client.cookies)
    assert client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}).status_code == 200
    second = login(client, "new@example.com")
    client.cookies.clear()
    client.cookies.update(stale)
    me = client.get(f"{V1}/me")
    assert me.status_code == 401, (first, second, me.json())


def test_signing_out_everywhere_ends_copied_cookies(client):
    login(client, "everywhere@example.com")
    copied = dict(client.cookies)
    assert client.post("/api/auth/logout", params={"everywhere": "true"}).status_code == 200
    client.cookies.update(copied)
    assert client.get(f"{V1}/me").status_code == 401
    login(client, "everywhere@example.com")  # signing in again works
    assert client.get(f"{V1}/me").status_code == 200


def test_a_revoked_invite_link_does_not_come_back_with_a_reused_id(client):
    login(client, "owner@example.com")
    first = client.post(f"{V1}/shares", json={"kind": "collection"}).json()
    assert client.delete(f"{V1}/shares/{first['id']}").status_code == 200
    second = client.post(f"{V1}/shares", json={"kind": "collection"}).json()
    assert second["url"] != first["url"]
    login(client, "guest@example.com")
    old = first["url"].split("invite=")[1]
    assert client.post(f"{V1}/shares/accept", json={"token": old}).status_code in (404, 410)


def test_an_agent_token_cannot_hand_out_access(signed_in):
    """A write-scoped personal access token can't create invite links: a grant made that way
    would outlive the token."""
    pat = signed_in.post(f"{V1}/me/tokens", json={"name": "agent", "scopes": ["read", "write"]}).json()["token"]
    signed_in.cookies.clear()
    res = signed_in.post(f"{V1}/shares", json={"kind": "collection"}, headers={"Authorization": f"Bearer {pat}"})
    assert res.status_code == 403


@pytest.mark.skipif(not os.environ.get("VAULT_TEST_POSTGRES_URL"), reason="needs Postgres (concurrent writers)")
def test_two_imports_at_once_on_postgres_leave_one_file():
    """On Postgres two imports really run side by side: the second waits for the first's claim
    on the collection and is then refused, instead of deleting nothing and adding its cards."""
    from sqlalchemy import func, text
    from sqlalchemy.orm import Session

    from vault.db import Base, Database
    from vault.importer import ImportConflict, import_collection

    db = Database(os.environ["VAULT_TEST_POSTGRES_URL"])

    def reset():
        with db.engine.begin() as conn:
            Base.metadata.drop_all(conn)
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    reset()
    db.migrate()
    with Session(db.engine) as s:
        user = User(name="Importer")
        s.add(user)
        s.commit()
        uid = user.id
    outcome = {}

    def second_import():
        with Session(db.engine) as s:
            try:
                import_collection(s, s.get(User, uid), "other.csv", OTHER)
                s.commit()
                outcome["second"] = "imported"
            except ImportConflict:
                s.rollback()
                outcome["second"] = "refused"

    with Session(db.engine) as s:
        import_collection(s, s.get(User, uid), "export.csv", CSV)  # half-way: not committed yet
        racer = threading.Thread(target=second_import)
        racer.start()
        time.sleep(0.5)
        assert racer.is_alive()  # waits on the first import's claim
        s.commit()
    racer.join(10)
    assert outcome == {"second": "refused"}
    with Session(db.engine) as s:
        assert s.scalar(select(func.count(Entry.id)).where(Entry.name == "Sol Ring")) == 1
    reset()
    db.engine.dispose()


def test_the_sync_does_not_write_a_stale_match_into_a_row_that_reused_an_id(app, signed_in, monkeypatch):
    """SQLite reuses a deleted row's id. If an import replaced the collection while the sync
    ran, a new row can carry an old row's id: the old row's match must not land on it."""
    from tests.test_api import BULK
    from vault import sync as sync_module
    from vault.models import Import

    assert upload(signed_in).status_code == 201
    real = sync_module.resolve_offline
    with app.state.db.sessions() as db:
        sol = db.scalar(select(Entry).where(Entry.name == "Sol Ring"))
        sol_id, user_id = sol.id, sol.user_id

    def reimport_meanwhile(entries, cards):
        with app.state.db.sessions() as other:  # a new import whose row got Sol Ring's old id
            imp = Import(user_id=user_id, filename="new.csv", source="generic", rows=1, copies=1, summary={})
            other.add(imp)
            other.flush()
            other.execute(delete(Entry).where(Entry.user_id == user_id))
            other.add(Entry(id=sol_id, user_id=user_id, import_id=imp.id, position=0, name="Island", quantity=1,
                            finish="nonfoil"))
            other.commit()
        return real(entries, cards)

    monkeypatch.setattr(sync_module, "resolve_offline", reimport_meanwhile)
    with app.state.db.sessions() as db:
        sync_module.sync(db, BULK, day=date(2026, 9, 27))
    with app.state.db.sessions() as db:
        island = db.get(Entry, sol_id)
        assert (island.name, island.scryfall_id, island.match_method) == ("Island", None, None)
