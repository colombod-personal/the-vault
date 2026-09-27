"""Multi-tenancy: nothing crosses between users unless the owner shares it."""

from pathlib import Path

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()


def login(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200


def test_private_by_default(client):
    login(client, "alice@example.com")
    client.post("/api/imports", files={"file": ("a.csv", CSV, "text/csv")})
    deck_id = client.post("/api/decks", json={"name": "A's deck", "text": "1 Sol Ring"}).json()["id"]
    share_id = client.post("/api/shares", json={"kind": "collection"}).json()["id"]

    login(client, "bob@example.com")
    assert client.get("/api/collection").json()["meta"]["totalQty"] == 0  # Bob's own, empty
    for method, path in [("GET", f"/api/decks/{deck_id}"), ("PUT", f"/api/decks/{deck_id}"),
                         ("DELETE", f"/api/decks/{deck_id}"), ("DELETE", f"/api/shares/{share_id}"),
                         ("GET", f"/api/shared/{share_id}/collection"), ("GET", f"/api/shared/{share_id}/deck")]:
        kwargs = {"json": {"name": "x", "text": "1 Island"}} if method == "PUT" else {}
        assert client.request(method, path, **kwargs).status_code == 404, (method, path)
    assert client.get("/api/decks").json() == [] and client.get("/api/shared").json() == []
    assert client.get("/api/imports").json() == []
    # share a deck Bob doesn't own
    assert client.post("/api/shares", json={"kind": "deck", "deck_id": deck_id}).status_code == 404


def test_collection_share_lifecycle(client):
    login(client, "alice@example.com")
    client.patch("/api/me", json={"name": "Alice"})
    client.post("/api/imports", files={"file": ("a.csv", CSV, "text/csv")})
    invite = client.post("/api/shares", json={"kind": "collection"}).json()
    token = invite["url"].split("invite=")[1]
    assert client.post("/api/shares/accept", json={"token": token}).status_code == 400  # own invite
    assert client.get("/api/shares").json()[0]["status"] == "pending"

    login(client, "bob@example.com")
    client.patch("/api/me", json={"name": "Bob"})
    accepted = client.post("/api/shares/accept", json={"token": token}).json()
    assert accepted["from"] == "Alice" and accepted["kind"] == "collection"
    # single use: nobody else can reuse the link
    login(client, "carol@example.com")
    assert client.post("/api/shares/accept", json={"token": token}).status_code == 404

    login(client, "bob@example.com")
    [incoming] = client.get("/api/shared").json()
    data = client.get(f"/api/shared/{incoming['id']}/collection").json()
    assert data["meta"]["totalQty"] == 7 and data["meta"]["sharedBy"] == "Alice"
    # prices paid are hidden unless the owner opted in
    assert data["meta"]["costsHidden"] and data["meta"]["totalPaid"] == 0
    assert all(c["pd"] == 0 for c in data["cards"])
    # read-only: Bob's own endpoints still show Bob's (empty) data
    assert client.get("/api/collection").json()["meta"]["totalQty"] == 0

    login(client, "alice@example.com")
    [out] = client.get("/api/shares").json()
    assert out["status"] == "active" and out["with"] == "Bob"
    assert client.delete(f"/api/shares/{out['id']}").json() == {"deleted": True}  # revoke

    login(client, "bob@example.com")
    assert client.get(f"/api/shared/{incoming['id']}/collection").status_code == 404
    assert client.get("/api/shared").json() == []


def test_deck_share_uses_viewers_collection_and_recipient_can_leave(client):
    login(client, "alice@example.com")
    deck_id = client.post("/api/decks", json={"name": "Brago", "text": "1 Sol Ring\n1 Rhystic Study"}).json()["id"]
    token = client.post("/api/shares", json={"kind": "deck", "deck_id": deck_id, "show_costs": True}).json()["url"].split("invite=")[1]

    login(client, "bob@example.com")
    client.post("/api/imports", files={"file": ("b.csv", CSV, "text/csv")})  # Bob owns a Sol Ring
    share_id = client.post("/api/shares/accept", json={"token": token}).json()["id"]
    deck = client.get(f"/api/shared/{share_id}/deck").json()
    status = {c["name"]: c["status"] for c in deck["coverage"]["cards"]}
    assert deck["name"] == "Brago" and status == {"Sol Ring": "owned", "Rhystic Study": "missing"}
    # a deck share gives no access to the owner's collection
    assert client.get(f"/api/shared/{share_id}/collection").status_code == 404
    assert client.delete(f"/api/shares/{share_id}").json() == {"deleted": True}  # leave
    assert client.get("/api/shared").json() == []


def test_expired_invite(client, app):
    from datetime import datetime, timedelta, timezone

    from vault.models import Share

    login(client, "alice@example.com")
    invite = client.post("/api/shares", json={"kind": "collection"}).json()
    token = invite["url"].split("invite=")[1]
    with app.state.db.sessions() as db:
        db.get(Share, invite["id"]).expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
    login(client, "bob@example.com")
    assert client.post("/api/shares/accept", json={"token": token}).status_code == 410


def test_deleting_a_deck_removes_its_shares(client):
    login(client, "alice@example.com")
    deck_id = client.post("/api/decks", json={"name": "D", "text": "1 Sol Ring"}).json()["id"]
    token = client.post("/api/shares", json={"kind": "deck", "deck_id": deck_id}).json()["url"].split("invite=")[1]
    login(client, "bob@example.com")
    client.post("/api/shares/accept", json={"token": token})
    login(client, "alice@example.com")
    client.delete(f"/api/decks/{deck_id}")
    login(client, "bob@example.com")
    assert client.get("/api/shared").json() == []


def test_cross_site_writes_are_refused(client):
    login(client, "alice@example.com")
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/imports", files={"file": ("a.csv", CSV, "text/csv")}, headers=evil).status_code == 403
    assert client.request("DELETE", "/api/me", json={"confirm": "DELETE"}, headers=evil).status_code == 403
    assert client.post("/api/auth/logout", headers=evil).status_code == 403
    # same origin (settings.base_url is http://testserver) and non-browser clients work
    same = {"Origin": "http://testserver"}
    assert client.post("/api/imports", files={"file": ("a.csv", CSV, "text/csv")}, headers=same).status_code == 200
    assert client.get("/api/collection", headers=evil).status_code == 200  # reads aren't state-changing
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
    res = TestClient(index.app).get("/api/collection")
    assert res.status_code == 503 and "DATABASE_URL is not set" in res.json()["detail"]
