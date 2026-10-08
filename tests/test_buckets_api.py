"""/api/v1/collection/buckets (#123): list, make, rename, delete; tenancy, scopes, idempotency, the sum invariant."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from vault.models import Bucket, Entry

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
B = "/api/v1/collection/buckets"


def sign_in_as(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200


def names(client):
    return [b["name"] for b in client.get(B).json()["items"]]


def test_the_list_shows_the_buckets_with_their_copies_and_they_add_up_to_the_inventory(signed_in):
    signed_in.post("/api/v1/imports", files={"file": ("export.csv", CSV, "text/csv")})
    page = signed_in.get(B).json()
    assert page["total"] == page["count"] == 1 and page["items"][0]["name"] == "my cards" and page["items"][0]["kind"] == "folder"
    summary = signed_in.get("/api/v1/collection").json()
    assert sum(b["copies"] for b in page["items"]) == summary["copies"] > 0  # the inventory is the sum of the buckets
    assert page["items"][0]["_links"]["self"]["href"] == f"{B}/{page['items'][0]['id']}"
    assert signed_in.get(page["items"][0]["_links"]["self"]["href"]).json()["entries"] == page["items"][0]["entries"]


def test_make_rename_and_delete_an_empty_bucket(signed_in):
    made = signed_in.post(B, json={"name": "  Trade binder  "})
    assert made.status_code == 201 and made.json()["name"] == "Trade binder" and made.json()["kind"] == "made"
    bucket = made.json()
    assert signed_in.post(B, json={"name": "trade BINDER"}).status_code == 409  # names ignore case
    assert signed_in.post(B, json={"name": "   "}).status_code == 422
    other = signed_in.post(B, json={"name": "Deck box"}).json()
    renamed = signed_in.patch(f"{B}/{bucket['id']}", json={"name": "Trades", "position": 7})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Trades" and renamed.json()["position"] == 7
    assert signed_in.patch(f"{B}/{bucket['id']}", json={"name": "DECK BOX"}).status_code == 409
    assert signed_in.patch(f"{B}/{bucket['id']}", json={"name": "trades"}).status_code == 200  # its own name, another case
    assert names(signed_in)[-1] == "trades" and names(signed_in)[:1] != ["trades"]  # position 7 sorts it last
    assert signed_in.delete(f"{B}/{other['id']}").json()["deleted"] is True
    assert signed_in.get(f"{B}/{other['id']}").status_code == 404


def test_a_bucket_with_copies_is_only_deleted_when_they_move_and_the_folder_is_not_rewritten(app, signed_in):
    signed_in.post("/api/v1/imports", files={"file": ("export.csv", CSV, "text/csv")})
    export_before = signed_in.get("/api/v1/collection/export.csv").content
    source = signed_in.get(B).json()["items"][0]
    target = signed_in.post(B, json={"name": "Elsewhere"}).json()
    held = signed_in.delete(f"{B}/{source['id']}")
    assert held.status_code == 409 and "move_to" in held.json()["detail"]
    assert signed_in.delete(f"{B}/{source['id']}", params={"move_to": source["id"]}).status_code == 422
    assert signed_in.delete(f"{B}/{source['id']}", params={"move_to": 999999}).status_code == 404
    done = signed_in.delete(f"{B}/{source['id']}", params={"move_to": target["id"]}).json()
    assert done["deleted"] is True and done["moved_rows"] == source["entries"]
    moved = signed_in.get(f"{B}/{target['id']}").json()
    assert moved["copies"] == source["copies"] and moved["entries"] == source["entries"]
    assert signed_in.get("/api/v1/collection/export.csv").content == export_before  # entries.folder is as imported
    with app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Entry).where(Entry.bucket_id.is_(None))) == 0


def test_another_persons_buckets_are_404_and_never_listed(client):
    sign_in_as(client, "bob@example.com")
    bob = client.post(B, json={"name": "Bob's box"}).json()
    sign_in_as(client, "alice@example.com")
    assert "Bob's box" not in names(client)
    assert client.get(f"{B}/{bob['id']}").status_code == 404
    assert client.patch(f"{B}/{bob['id']}", json={"name": "Mine now"}).status_code == 404
    assert client.delete(f"{B}/{bob['id']}").status_code == 404
    mine = client.post(B, json={"name": "Alice's box"}).json()
    assert client.delete(f"{B}/{mine['id']}", params={"move_to": bob["id"]}).status_code == 404  # nor a place to put copies
    sign_in_as(client, "bob@example.com")
    assert client.get(f"{B}/{bob['id']}").json()["name"] == "Bob's box"  # untouched


def test_a_read_only_token_may_list_but_not_change(signed_in):
    token = signed_in.post("/api/v1/me/tokens", json={"name": "ro", "scopes": ["read"]}).json()["token"]
    made = signed_in.post(B, json={"name": "Mine"}).json()
    signed_in.cookies.clear()
    ro = {"Authorization": f"Bearer {token}"}
    assert signed_in.get(B, headers=ro).status_code == 200
    assert signed_in.post(B, json={"name": "No"}, headers=ro).status_code == 403
    assert signed_in.patch(f"{B}/{made['id']}", json={"name": "No"}, headers=ro).status_code == 403
    assert signed_in.delete(f"{B}/{made['id']}", headers=ro).status_code == 403


def test_a_retried_post_with_the_same_key_makes_one_bucket(signed_in):
    headers = {"Idempotency-Key": "make-trade-binder-1"}
    first = signed_in.post(B, json={"name": "Retry"}, headers=headers)
    again = signed_in.post(B, json={"name": "Retry"}, headers=headers)
    assert first.status_code == again.status_code == 201 and first.json()["id"] == again.json()["id"]
    assert names(signed_in).count("Retry") == 1


def test_a_person_makes_at_most_100_buckets_by_hand_and_pages_follow_the_cursor(app, signed_in):
    with app.state.db.sessions() as db:
        uid = db.scalar(select(func.max(Bucket.user_id))) or 1
    for n in range(100):
        assert signed_in.post(B, json={"name": f"Box {n:03}"}).status_code == 201
    over = signed_in.post(B, json={"name": "One too many"})
    assert over.status_code == 409 and "100" in over.json()["detail"]
    first = signed_in.get(B, params={"limit": 40}).json()
    assert first["count"] == 40 and first["total"] == 100 and "next" in first["_links"]
    seen = [b["name"] for b in first["items"]]
    nxt = first["_links"]["next"]["href"]
    while nxt:
        page = signed_in.get(nxt).json()
        seen += [b["name"] for b in page["items"]]
        nxt = page["_links"].get("next", {}).get("href")
    assert len(seen) == len(set(seen)) == 100
    assert signed_in.delete(f"{B}/{first['items'][0]['id']}").status_code == 200
    assert signed_in.post(B, json={"name": "Room again"}).status_code == 201  # deleting one makes room
