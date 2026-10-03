"""Multi-tenancy: nothing crosses between users unless the owner shares it."""

import threading
import time
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()


def login(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200


def test_private_by_default(client):
    login(client, "alice@example.com")
    client.post("/api/v1/imports", files={"file": ("a.csv", CSV, "text/csv")})
    deck_id = client.post("/api/v1/decks", json={"name": "A's deck", "text": "1 Sol Ring"}).json()["id"]
    share_id = client.post("/api/v1/shares", json={"kind": "collection"}).json()["id"]

    login(client, "bob@example.com")
    assert client.get("/api/v1/collection").json()["copies"] == 0  # Bob's own, empty
    for method, path in [("GET", f"/api/v1/decks/{deck_id}"), ("PUT", f"/api/v1/decks/{deck_id}"),
                         ("DELETE", f"/api/v1/decks/{deck_id}"), ("DELETE", f"/api/v1/shares/{share_id}"),
                         ("GET", f"/api/v1/shared/{share_id}/collection"), ("GET", f"/api/v1/shared/{share_id}/deck")]:
        kwargs = {"json": {"name": "x", "text": "1 Island"}} if method == "PUT" else {}
        assert client.request(method, path, **kwargs).status_code == 404, (method, path)
    assert client.get("/api/v1/decks").json()["items"] == [] and client.get("/api/v1/shared").json()["items"] == []
    assert client.get("/api/v1/imports").json()["items"] == []
    # share a deck Bob doesn't own
    assert client.post("/api/v1/shares", json={"kind": "deck", "deck_id": deck_id}).status_code == 404


def test_collection_share_lifecycle(client):
    login(client, "alice@example.com")
    client.patch("/api/v1/me", json={"name": "Alice"})
    client.post("/api/v1/imports", files={"file": ("a.csv", CSV, "text/csv")})
    invite = client.post("/api/v1/shares", json={"kind": "collection"}).json()
    token = invite["url"].split("invite=")[1]
    assert client.post("/api/v1/shares/accept", json={"token": token}).status_code == 400  # own invite
    assert client.get("/api/v1/shares").json()["items"][0]["status"] == "pending"

    login(client, "bob@example.com")
    client.patch("/api/v1/me", json={"name": "Bob"})
    accepted = client.post("/api/v1/shares/accept", json={"token": token}).json()
    assert accepted["from"] == "Alice" and accepted["kind"] == "collection"
    # single use: nobody else can reuse the link
    login(client, "carol@example.com")
    assert client.post("/api/v1/shares/accept", json={"token": token}).status_code == 404

    login(client, "bob@example.com")
    [incoming] = client.get("/api/v1/shared").json()["items"]
    data = client.get(f"/api/v1/shared/{incoming['id']}/collection").json()
    assert data["copies"] == 7 and data["owner"] == "Alice"
    # prices paid are hidden unless the owner opted in
    assert data["costs_hidden"] and data["paid"] is None and "imports" not in data["_links"]
    cards = client.get(data["_links"]["cards"]["href"]).json()["items"]
    assert len(cards) == 4 and all(c["paid"] is None for c in cards)
    detail = client.get(cards[0]["_links"]["self"]["href"]).json()
    assert all(c["purchase_price"] is None for c in detail["copies"])
    assert all(m["paid"] is None for m in client.get(data["_links"]["timeline"]["href"]).json()["months"])
    assert "biggest_gains" not in client.get(data["_links"]["stats"]["href"]).json()
    # read-only: Bob's own endpoints still show Bob's (empty) data
    assert client.get("/api/v1/collection").json()["copies"] == 0

    login(client, "alice@example.com")
    [out] = client.get("/api/v1/shares").json()["items"]
    assert out["status"] == "active" and out["with"] == "Bob"
    assert client.delete(f"/api/v1/shares/{out['id']}").json() == {"deleted": True}  # revoke

    login(client, "bob@example.com")
    assert client.get(f"/api/v1/shared/{incoming['id']}/collection").status_code == 404
    assert client.get("/api/v1/shared").json()["items"] == []


def test_deck_share_uses_viewers_collection_and_recipient_can_leave(client):
    login(client, "alice@example.com")
    deck_id = client.post("/api/v1/decks", json={"name": "Brago", "text": "1 Sol Ring\n1 Rhystic Study"}).json()["id"]
    token = client.post("/api/v1/shares", json={"kind": "deck", "deck_id": deck_id, "show_costs": True}).json()["url"].split("invite=")[1]

    login(client, "bob@example.com")
    client.post("/api/v1/imports", files={"file": ("b.csv", CSV, "text/csv")})  # Bob owns a Sol Ring
    share_id = client.post("/api/v1/shares/accept", json={"token": token}).json()["id"]
    deck = client.get(f"/api/v1/shared/{share_id}/deck").json()
    status = {c["name"]: c["status"] for c in deck["coverage"]["cards"]}
    assert deck["name"] == "Brago" and status == {"Sol Ring": "owned", "Rhystic Study": "missing"}
    # a deck share gives no access to the owner's collection
    assert client.get(f"/api/v1/shared/{share_id}/collection").status_code == 404
    assert client.delete(f"/api/v1/shares/{share_id}").json() == {"deleted": True}  # leave
    assert client.get("/api/v1/shared").json()["items"] == []


def test_expired_invite(client, app):
    from datetime import datetime, timedelta, timezone

    from vault.models import Share

    login(client, "alice@example.com")
    invite = client.post("/api/v1/shares", json={"kind": "collection"}).json()
    token = invite["url"].split("invite=")[1]
    with app.state.db.sessions() as db:
        db.get(Share, invite["id"]).expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    login(client, "bob@example.com")
    assert client.post("/api/v1/shares/accept", json={"token": token}).status_code == 410


def test_deleting_a_deck_removes_its_shares(client):
    login(client, "alice@example.com")
    deck_id = client.post("/api/v1/decks", json={"name": "D", "text": "1 Sol Ring"}).json()["id"]
    token = client.post("/api/v1/shares", json={"kind": "deck", "deck_id": deck_id}).json()["url"].split("invite=")[1]
    login(client, "bob@example.com")
    client.post("/api/v1/shares/accept", json={"token": token})
    login(client, "alice@example.com")
    client.delete(f"/api/v1/decks/{deck_id}")
    login(client, "bob@example.com")
    assert client.get("/api/v1/shared").json()["items"] == []


def test_cross_site_writes_are_refused(client):
    login(client, "alice@example.com")
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/v1/imports", files={"file": ("a.csv", CSV, "text/csv")}, headers=evil).status_code == 403
    assert client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"}, headers=evil).status_code == 403
    assert client.post("/api/auth/logout", headers=evil).status_code == 403
    # same origin (settings.base_url is http://testserver) and non-browser clients work
    same = {"Origin": "http://testserver"}
    assert client.post("/api/v1/imports", files={"file": ("a.csv", CSV, "text/csv")}, headers=same).status_code == 201
    assert client.get("/api/v1/collection", headers=evil).status_code == 200  # reads aren't state-changing
    # OAuth callbacks and Meta's deletion callback are cross-site by design
    assert client.post("/api/facebook/data-deletion", data={"signed_request": "x.y"}, headers=evil).status_code == 400


def test_vercel_without_database_explains_itself(monkeypatch):
    import importlib
    import sys

    from fastapi.testclient import TestClient

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sys.modules.pop("api.index", None)
    index = importlib.import_module("api.index")
    res = TestClient(index.app).get("/api/v1/collection")
    assert res.status_code == 503 and "DATABASE_URL is not set" in res.json()["detail"]


def test_racing_accepts_claim_an_invite_once(client, monkeypatch):
    from vault import sharing

    login(client, "alice@example.com")
    token = client.post("/api/v1/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    real_aware, raced = sharing._aware, []

    def carol_accepts_meanwhile(dt):  # runs after Bob's request has read the unused invite
        if not raced:
            raced.append(True)
            with client.app.state.db.sessions() as db:
                carol = sharing.User(email="carol@example.com")
                db.add(carol)
                db.commit()
                sharing.accept_invite(db, carol, token)
                db.commit()
        return real_aware(dt)

    login(client, "bob@example.com")
    monkeypatch.setattr(sharing, "_aware", carol_accepts_meanwhile)
    assert client.post("/api/v1/shares/accept", json={"token": token}).status_code == 404
    assert raced and client.get("/api/v1/shared").json()["items"] == []


def test_accepting_an_invite_can_be_retried_with_its_idempotency_key(client):
    """If the answer is lost after the invite was accepted, a retry with the same key gets the
    same answer instead of "invalid or already used"."""
    login(client, "alice@example.com")
    token = client.post("/api/v1/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    login(client, "bob@example.com")
    headers = {"Idempotency-Key": "accept-1"}
    first = client.post("/api/v1/shares/accept", json={"token": token}, headers=headers)
    again = client.post("/api/v1/shares/accept", json={"token": token}, headers=headers)
    assert first.status_code == again.status_code == 200, again.text
    assert again.json() == first.json() and again.headers.get("idempotent-replayed") == "true"
    assert client.post("/api/v1/shares/accept", json={"token": token}).status_code == 404  # still single use


def test_sharing_never_reveals_an_email_address(client, settings):
    """A sign-in provider may give an e-mail but no name: the people you share with still see
    only a name, never the address."""
    from sqlalchemy import create_engine, text

    login(client, "alice@example.com")
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET name = NULL WHERE email = 'alice@example.com'"))
    token = client.post("/api/v1/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    login(client, "bob@example.com")
    accepted = client.post("/api/v1/shares/accept", json={"token": token}).json()
    [incoming] = client.get("/api/v1/shared").json()["items"]
    data = client.get(f"/api/v1/shared/{incoming['id']}/collection").json()
    shown = [accepted["from"], incoming["from"], data["owner"]]
    assert all("alice@example.com" not in (s or "") for s in shown), shown
    login(client, "alice@example.com")
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET name = NULL WHERE email = 'bob@example.com'"))
    assert "bob@example.com" not in (client.get("/api/v1/shares").json()["items"][0]["with"] or "")


def test_two_invites_from_one_owner_accepted_at_once_give_one_grant(database_url):
    """Accepting two invites from the same owner at the same time leaves one grant, so revoking
    it really ends access."""
    from vault.db import Base, Database
    from vault.models import Share, User
    from vault.sharing import accept_invite, create_invite

    db = Database(database_url)
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.migrate()
    with Session(db.engine) as s:
        owner, guest = User(name="Owner"), User(name="Guest")
        s.add_all([owner, guest])
        s.commit()
        (_, first), (_, second) = create_invite(s, owner, "collection", None, False, "s" * 32), create_invite(s, owner, "collection", None, False, "s" * 32)
        s.commit()
        guest_id = guest.id

    done = {}

    def accept_second():
        with Session(db.engine) as s:
            accept_invite(s, s.get(User, guest_id), second)
            s.commit()
            done["ok"] = True

    with Session(db.engine) as s:
        # the first acceptance is half-way: it holds the guest's lock and hasn't committed
        s.execute(select(User.id).where(User.id == guest_id).with_for_update())
        accept_invite_no_commit = s.get(User, guest_id)
        share = s.scalar(select(Share).where(Share.grantee_id.is_(None)).order_by(Share.id))
        share.grantee_id, share.token_hash = accept_invite_no_commit.id, None
        s.flush()
        racer = threading.Thread(target=accept_second)
        racer.start()
        time.sleep(0.5)
        assert racer.is_alive()  # waits for the first acceptance instead of deciding on stale data
        s.commit()
    racer.join(10)
    with Session(db.engine) as s:
        assert s.scalar(select(func.count(Share.id)).where(Share.grantee_id == guest_id)) == 1
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.engine.dispose()


def test_shared_analytics_answer_only_the_grantee(client):
    login(client, "alice@example.com")
    client.post("/api/v1/imports", files={"file": ("a.csv", CSV, "text/csv")})
    token = client.post("/api/v1/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    login(client, "bob@example.com")
    share_id = client.post("/api/v1/shares/accept", json={"token": token}).json()["id"]
    paths = [f"/api/v1/shared/{share_id}/collection/{p}" for p in ("breakdowns", "valuation", "names")]
    assert all(client.get(p).status_code == 200 for p in paths)
    assert client.get(paths[2]).json()["total"] == 4
    login(client, "carol@example.com")
    assert all(client.get(p).status_code == 404 for p in paths)
    assert client.get("/api/v1/collection/names").json()["total"] == 0  # Carol's own, empty
    # the refresh is for one's own collection only: there is no shared variant
    assert client.post(f"/api/v1/shared/{share_id}/collection/refresh").status_code in (404, 405)
