"""Multi-tenancy: nothing crosses between users unless the owner shares it."""

from pathlib import Path

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
        return real_aware(dt)

    login(client, "bob@example.com")
    monkeypatch.setattr(sharing, "_aware", carol_accepts_meanwhile)
    assert client.post("/api/v1/shares/accept", json={"token": token}).status_code == 404
    assert raced and client.get("/api/v1/shared").json()["items"] == []
