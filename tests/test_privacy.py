"""GDPR: export everything, erase everything."""

import io
import json
import zipfile
from pathlib import Path

from sqlalchemy import select

from vault.models import CollectionValue, Deck, Entry, Identity, Import, Share, User

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
DECK = "1 Sol Ring\n1 Rhystic Study\n"


def setup_user(client, email):
    client.cookies.clear()
    client.post("/api/auth/dev-login", params={"email": email})
    client.post("/api/v1/imports", files={"file": ("export.csv", CSV, "text/csv")})
    return client.post("/api/v1/decks", json={"name": "My deck", "text": DECK}).json()["id"]


def test_export_contains_all_personal_data(signed_in):
    signed_in.post("/api/v1/imports", files={"file": ("export.csv", CSV, "text/csv")})
    signed_in.post("/api/v1/decks", json={"name": "Kenrith / EDH", "text": DECK})
    signed_in.patch("/api/v1/me", json={"name": "Alice"})
    res = signed_in.get("/api/v1/me/export")
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(z.namelist())
    assert {"README.txt", "account.json", "collection.csv", "collection.json", "imports.json",
            "value_history.json", "decks.json", "shares.json", "app_sessions.json"} <= names
    assert any(n.startswith("decks/") and n.endswith(".txt") for n in names)
    assert z.read("collection.csv") == CSV  # re-importable, byte-identical
    account = json.loads(z.read("account.json"))
    assert account["name"] == "Alice" and account["sign_in_methods"][0]["provider"] == "dev"
    assert json.loads(z.read("imports.json"))[0]["changes"]["added"] == 4


def test_delete_requires_confirmation_and_removes_everything(client, app):
    setup_user(client, "bob@example.com")
    bob_share = client.post("/api/v1/shares", json={"kind": "collection"}).json()
    alice_deck = setup_user(client, "alice@example.com")
    alice_share = client.post("/api/v1/shares", json={"kind": "deck", "deck_id": alice_deck}).json()
    client.post("/api/v1/shares/accept", json={"token": bob_share["url"].split("invite=")[1]})
    with app.state.db.sessions() as db:
        alice_id = db.scalar(select(Identity).where(Identity.subject == "alice@example.com")).user_id
        db.add(CollectionValue(user_id=alice_id, day=__import__("datetime").date(2026, 9, 1),
                               market_usd=1, cost_usd=1, copies=1, priced_copies=1))
        db.commit()

    assert client.request("DELETE", "/api/v1/me", json={"confirm": "yes"}).status_code == 400
    res = client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"}).json()
    assert res["deleted"] and res["removed"]["entries"] == 5 and res["removed"]["users"] == 1
    assert client.get("/api/v1/me").status_code == 401

    with app.state.db.sessions() as db:
        for model, col in [(Entry, Entry.user_id), (Import, Import.user_id), (Deck, Deck.user_id),
                           (CollectionValue, CollectionValue.user_id), (Identity, Identity.user_id)]:
            assert db.scalar(select(model).where(col == alice_id)) is None, model.__name__
        # Alice's own share and the one she received from Bob are gone; Bob's data is untouched
        assert db.scalar(select(Share).where((Share.owner_id == alice_id) | (Share.grantee_id == alice_id))) is None
        assert db.get(User, alice_id) is None
        assert db.query(Entry).count() == 5 and db.query(User).count() == 1
    assert alice_share  # (created before deletion)


def test_every_table_referencing_users_is_purged():
    from vault.db import Base
    from vault.privacy import personal_data

    referencing = {
        t.name for t in Base.metadata.tables.values()
        if any(fk.column.table.name == "users" for fk in t.foreign_keys)
    } | {"users"}
    assert referencing == set(personal_data(0)), "add new per-user tables to vault.privacy.personal_data"


def test_export_never_contains_someone_elses_email(client, settings):
    """shares.json names the people you share with by display name, never by e-mail."""
    from sqlalchemy import create_engine, text

    def login(email):
        client.cookies.clear()
        client.post("/api/auth/dev-login", params={"email": email})

    login("alice@example.com")
    token = client.post("/api/v1/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    login("bob@example.com")
    client.post("/api/v1/shares/accept", json={"token": token})
    with create_engine(settings.database_url).begin() as conn:
        conn.execute(text("UPDATE users SET name = NULL"))
    for me, other in (("alice@example.com", "bob@example.com"), ("bob@example.com", "alice@example.com")):
        login(me)
        shares = zipfile.ZipFile(io.BytesIO(client.get("/api/v1/me/export").content)).read("shares.json").decode()
        assert other not in shares, shares
