"""Moving copies between buckets (#123): split a stack, rewrite the folder, record a change, keep the totals."""

import pytest
from sqlalchemy import select

from vault.collection_view import entry_group_id
from vault.models import Entry

B = "/api/v1/collection/buckets"
HEAD = ("Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,Price Bought,"
        "Date Bought,LOW,MID,MARKET\n")
FILE = (HEAD +
        "Binder,5,2,Sol Ring,C21,Commander 2021,263,Mint,Normal,English,1.00,2024-02-17,1.00,2.00,2.30\n"
        "Binder,3,0,Counterspell,STA,Strixhaven Mystical Archive,15,Mint,Normal,English,1.10,2024-02-17,1.00,1.00,1.00\n"
        "Binder,40,0,Mountain,C17,Commander 2017,304,Mint,Normal,English,0.05,2024-02-17,0.05,0.05,0.05\n"
        "Trade box,1,0,Sol Ring,C21,Commander 2021,263,Mint,Normal,English,1.00,2024-02-17,1.00,2.00,2.30\n").encode()


@pytest.fixture
def stocked(signed_in):
    assert signed_in.post("/api/v1/imports", files={"file": ("f.csv", FILE, "text/csv")}).status_code in (200, 201)
    buckets = {b["name"]: b for b in signed_in.get(B).json()["items"]}
    return signed_in, buckets["Binder"], buckets["Trade box"]


def card_id(client, bucket, name):
    items = client.get("/api/v1/collection/cards", params={"bucket": bucket["id"], "name": name}).json()["items"]
    assert len(items) == 1, (name, items)
    return items[0]["id"]


def copies(client, bucket):
    return client.get(f"{B}/{bucket['id']}").json()["copies"]


def test_moving_part_of_a_stack_splits_it_merges_into_the_identical_row_and_rewrites_the_folder(stocked):
    client, binder, trade = stocked
    ring = card_id(client, binder, "Sol Ring")
    total = client.get("/api/v1/collection").json()["copies"]
    res = client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "lines": [{"card_id": ring, "quantity": 2}]})
    body = res.json()
    assert res.status_code == 200 and body["applied"] is True and body["copies"] == 2 and body["change_set"]
    assert copies(client, binder) == 3 + 3 + 40 and copies(client, trade) == 1 + 2  # 5 - 2 stay; 1 + 2 there
    assert client.get("/api/v1/collection").json()["copies"] == total  # the inventory is the sum of the buckets, unchanged
    ring_there = client.get("/api/v1/collection/cards", params={"bucket": trade["id"], "name": "Sol Ring"}).json()["items"]
    assert [c["quantity"] for c in ring_there] == [3]  # merged into the row that was already there, not a second row
    export = client.get("/api/v1/collection/export.csv", params={"bucket": trade["id"]}).text
    assert export.count("Trade box") >= 1 and "Binder" not in export  # the folder of the moved rows is the target's name
    last = client.get("/api/v1/imports").json()["items"][0]
    assert last["kind"] == "move" and last["changes"]["moved"] == 2 and "from Binder to Trade box" in last["filename"]


def test_moving_a_whole_row_moves_the_row_and_its_trade_copies_stay_unless_they_have_to_go(stocked, app):
    client, binder, trade = stocked
    ring = card_id(client, binder, "Sol Ring")  # 5 copies, 2 marked for trade
    client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "lines": [{"card_id": ring, "quantity": 3}]})
    with app.state.db.sessions() as db:
        rows = db.scalars(select(Entry).where(Entry.name == "Sol Ring")).all()
        by_folder = {r.folder: (r.quantity, r.trade_quantity) for r in rows}
    assert by_folder == {"Binder": (2, 2), "Trade box": (4, 0)}  # the 3 plain copies went; the 2 for trade stayed
    client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "lines": [{"card_id": ring, "quantity": 2}]})
    with app.state.db.sessions() as db:
        rows = db.scalars(select(Entry).where(Entry.name == "Sol Ring")).all()
    assert {r.folder: (r.quantity, r.trade_quantity) for r in rows} == {"Trade box": (6, 2)}  # the whole row went, trade marks with it


def test_a_move_that_asks_for_too_much_or_for_nothing_there_changes_nothing(stocked):
    client, binder, trade = stocked
    ring = card_id(client, binder, "Sol Ring")
    before = (copies(client, binder), copies(client, trade))
    over = client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "lines": [{"card_id": ring, "quantity": 6}]})
    assert over.status_code == 422 and "holds 5" in over.json()["detail"]
    twice = client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "lines": [{"card_id": ring, "quantity": 3},
                                                                                       {"card_id": ring, "quantity": 3}]})
    assert twice.status_code == 422  # the lines add up
    unknown = client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "lines": [{"card_id": "nope", "quantity": 1}]})
    assert unknown.status_code == 404 and "not in the bucket" in unknown.json()["detail"]
    same = client.post(f"{B}/{binder['id']}/move", json={"to": binder["id"], "lines": [{"card_id": ring, "quantity": 1}]})
    assert same.status_code == 422
    assert client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "lines": []}).status_code == 422
    assert (copies(client, binder), copies(client, trade)) == before


def test_a_big_move_is_shown_first_and_applied_only_when_confirmed(stocked):
    client, binder, trade = stocked
    mountain = card_id(client, binder, "Mountain")
    shown = client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "lines": [{"card_id": mountain, "quantity": 30}]}).json()
    assert shown["applied"] is False and shown["copies"] == 30 and "confirm true" in shown["note"]
    assert copies(client, binder) == 5 + 3 + 40  # nothing moved
    done = client.post(f"{B}/{binder['id']}/move", json={"to": trade["id"], "confirm": True,
                                                         "lines": [{"card_id": mountain, "quantity": 30}]}).json()
    assert done["applied"] is True and copies(client, trade) == 1 + 30


def test_a_move_is_recorded_once_per_idempotency_key_and_a_read_only_token_cannot_move(stocked):
    client, binder, trade = stocked
    counterspell = card_id(client, binder, "Counterspell")
    token = client.post("/api/v1/me/tokens", json={"name": "ro", "scopes": ["read"]}).json()["token"]
    headers = {"Idempotency-Key": "move-1"}
    payload = {"to": trade["id"], "lines": [{"card_id": counterspell, "quantity": 1}]}
    first = client.post(f"{B}/{binder['id']}/move", json=payload, headers=headers)
    again = client.post(f"{B}/{binder['id']}/move", json=payload, headers=headers)
    assert first.json()["change_set"] == again.json()["change_set"] and copies(client, trade) == 1 + 1
    ro = {"Authorization": f"Bearer {token}"}
    client.cookies.clear()
    assert client.post(f"{B}/{binder['id']}/move", json=payload, headers=ro).status_code == 403


def test_another_persons_buckets_cannot_take_or_give_copies(stocked):
    client, binder, trade = stocked
    ring = card_id(client, binder, "Sol Ring")
    client.cookies.clear()
    client.post("/api/auth/dev-login", params={"email": "mallory@example.com"})
    theirs = client.post(B, json={"name": "Mallory's"}).json()
    assert client.post(f"{B}/{binder['id']}/move", json={"to": theirs["id"], "lines": [{"card_id": ring, "quantity": 1}]}).status_code == 404
    assert client.post(f"{B}/{theirs['id']}/move", json={"to": binder["id"], "lines": [{"card_id": ring, "quantity": 1}]}).status_code == 404


def test_the_row_group_id_is_the_one_the_collection_view_uses(stocked, app):
    client, binder, trade = stocked
    shown = {c["id"] for c in client.get("/api/v1/collection/cards").json()["items"]}
    with app.state.db.sessions() as db:
        assert {entry_group_id(e) for e in db.scalars(select(Entry))} == shown
