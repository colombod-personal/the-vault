"""/api/v1/collection/tags (#127): list, rename, delete; tag and untag cards in bulk; the tag filter on search; provenance of who
wrote a tag; tenancy, scopes, idempotency, limits. A tag is on the card (oracle id), so it survives imports."""

import pytest

from tests.ids import sid
from vault import tags as T
from vault.models import Card, TagAssignment

T_ = "/api/v1/collection/tags"
CARDS = "/api/v1/collection/cards"
SOL, MOUNTAIN, ELF = ("00000000-0000-4000-8000-00000000000" + n for n in "123")

CSV = (
    "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET\n"
    "Binder,3,0,Sol Ring,C21,Commander 2021,263,Mint,Normal,English,1.00,2024-02-17,1.00,2.00,2.30\n"
    "Binder,2,0,Sol Ring,CMM,Commander Masters,400,Mint,Normal,English,1.00,2024-02-17,1.00,2.00,2.30\n"
    "Trade box,4,0,Mountain,C17,Commander 2017,304,Mint,Normal,English,0.05,2024-02-17,0.05,0.05,0.05\n"
    "Trade box,1,0,Unknown Thing,XXX,Nowhere,1,Mint,Normal,English,0.05,2024-02-17,0.05,0.05,0.05\n"
).encode()
ONLY_MOUNTAIN = CSV.split(b"\n")[0] + b"\n" + CSV.split(b"\n")[3] + b"\n"


@pytest.fixture
def stocked(app, signed_in):
    """A signed-in person with Sol Ring in two printings, Mountain and one card the Vault can't match."""
    with app.state.db.sessions() as db:
        db.add_all([Card(scryfall_id=sid("sol-c21"), oracle_id=SOL, name="Sol Ring", set_code="c21", collector_number="263"),
                    Card(scryfall_id=sid("sol-cmm"), oracle_id=SOL, name="Sol Ring", set_code="cmm", collector_number="400"),
                    Card(scryfall_id=sid("mountain"), oracle_id=MOUNTAIN, name="Mountain", set_code="c17", collector_number="304")])
        db.commit()
    assert signed_in.post("/api/v1/imports", files={"file": ("c.csv", CSV, "text/csv")}).status_code in (200, 201)
    return signed_in


def card_ids(client):
    """name -> ids of its printings in the collection."""
    out = {}
    for c in client.get(CARDS, params={"limit": 100}).json()["items"]:
        out.setdefault(c["name"], []).append(c["id"])
    return out


def sign_in_as(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200


def test_tagging_a_card_makes_the_tag_and_lists_it_with_counts(stocked):
    ids = card_ids(stocked)
    res = stocked.post(f"{T_}/trade/cards", json={"card_ids": ids["Sol Ring"] + ids["Mountain"]})
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True and body["added"] == 2 and body["already_tagged"] == 0 and body["written_by"] == "person"
    assert [c["name"] for c in body["cards"]] == ["Sol Ring", "Sol Ring", "Mountain"]  # what was asked, as asked
    page = stocked.get(T_).json()
    assert page["total"] == 1 and page["items"][0]["tag"] == "trade" and page["items"][0]["cards"] == 2
    assert page["items"][0]["by_source"] == {"person": 2, "assistant": 0, "system": 0}
    detail = stocked.get(f"{T_}/trade").json()
    assert detail["owned_cards"] == 2 and detail["assistants"] == []
    again = stocked.post(f"{T_}/trade/cards", json={"card_ids": ids["Mountain"]}).json()
    assert again["added"] == 0 and again["already_tagged"] == 1  # tagging twice is not an error


def test_a_tag_filters_the_collection_and_shows_on_every_printing_of_the_card(stocked):
    ids = card_ids(stocked)
    stocked.post(f"{T_}/staple/cards", json={"card_ids": [ids["Sol Ring"][0]]})  # one printing; the tag is on the card
    found = stocked.get(CARDS, params={"tag": "staple"}).json()
    assert found["total"] == 2 and {c["name"] for c in found["items"]} == {"Sol Ring"}  # both printings
    assert all(c["tags"] == ["staple"] for c in found["items"])
    everything = {c["name"]: c["tags"] for c in stocked.get(CARDS).json()["items"]}
    assert everything["Mountain"] == [] and everything["Sol Ring"] == ["staple"]
    assert stocked.get(f"{CARDS}/{ids['Sol Ring'][1]}").json()["tags"] == ["staple"]
    assert stocked.get(CARDS, params={"tag": "nothing"}).json()["total"] == 0  # no such tag: nothing, not an error
    assert stocked.get(CARDS, params={"tag": "Bad Tag!"}).status_code == 400


def test_the_etag_changes_when_a_tag_does(stocked):
    ids = card_ids(stocked)
    first = stocked.get(CARDS)
    assert stocked.get(CARDS, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    stocked.post(f"{T_}/new/cards", json={"card_ids": ids["Mountain"]})
    changed = stocked.get(CARDS, headers={"If-None-Match": first.headers["etag"]})
    assert changed.status_code == 200 and changed.headers["etag"] != first.headers["etag"]
    stocked.patch(f"{T_}/new", json={"name": "newer"})  # a rename is a change too
    assert stocked.get(CARDS, headers={"If-None-Match": changed.headers["etag"]}).status_code == 200


def test_untag_takes_the_tag_off_only_the_cards_named(stocked):
    ids = card_ids(stocked)
    stocked.post(f"{T_}/trade/cards", json={"card_ids": ids["Sol Ring"] + ids["Mountain"]})
    res = stocked.post(f"{T_}/trade/cards/remove", json={"card_ids": ids["Mountain"] + ids["Sol Ring"][:1]}).json()
    assert res["applied"] is True and res["removed"] == 2 and res["not_tagged"] == 0
    assert stocked.get(f"{T_}/trade").status_code == 404  # no card has it any more: the tag is gone
    assert stocked.post(f"{T_}/trade/cards/remove", json={"card_ids": ids["Mountain"]}).json()["not_tagged"] == 1


def test_rename_moves_the_tag_and_merges_with_a_card_that_already_has_the_new_name(stocked):
    ids = card_ids(stocked)
    stocked.post(f"{T_}/old/cards", json={"card_ids": ids["Sol Ring"][:1] + ids["Mountain"]})
    stocked.post(f"{T_}/new/cards", json={"card_ids": ids["Sol Ring"][:1]})
    renamed = stocked.patch(f"{T_}/old", json={"name": "  NEW "})
    assert renamed.status_code == 200 and renamed.json()["tag"] == "new" and renamed.json()["cards"] == 2  # Sol Ring once, Mountain
    assert stocked.get(f"{T_}/old").status_code == 404
    assert stocked.patch(f"{T_}/new", json={"name": "new"}).status_code == 422
    assert stocked.patch(f"{T_}/new", json={"name": "not valid!"}).status_code == 422
    assert stocked.patch(f"{T_}/missing", json={"name": "x"}).status_code == 404


def test_delete_removes_the_tag_from_every_card_and_keeps_the_cards(stocked):
    ids = card_ids(stocked)
    stocked.post(f"{T_}/gone/cards", json={"card_ids": ids["Mountain"]})
    done = stocked.delete(f"{T_}/gone").json()
    assert done["deleted"] is True and done["cards"] == 1
    assert stocked.get(f"{T_}/gone").status_code == 404 and stocked.delete(f"{T_}/gone").status_code == 404
    assert "Mountain" in card_ids(stocked)


def test_a_tag_survives_a_re_import_and_stays_when_the_card_leaves_the_collection(stocked):
    ids = card_ids(stocked)
    stocked.post(f"{T_}/keep/cards", json={"card_ids": ids["Sol Ring"] + ids["Mountain"]})
    stocked.post("/api/v1/imports", files={"file": ("m.csv", ONLY_MOUNTAIN, "text/csv")})  # Sol Ring is gone from the file
    assert "Sol Ring" not in card_ids(stocked)
    detail = stocked.get(f"{T_}/keep").json()
    assert detail["cards"] == 2 and detail["owned_cards"] == 1  # Sol Ring is tagged but not owned
    assert [c["name"] for c in stocked.get(CARDS, params={"tag": "keep"}).json()["items"]] == ["Mountain"]


def test_a_copy_the_vault_could_not_match_cannot_take_a_tag_and_an_unknown_id_is_404(stocked):
    ids = card_ids(stocked)
    unmatched = stocked.post(f"{T_}/x/cards", json={"card_ids": ids["Unknown Thing"]})
    assert unmatched.status_code == 422 and "not matched to a card yet" in unmatched.json()["detail"]
    assert stocked.post(f"{T_}/x/cards", json={"card_ids": ["0123456789abcdef"]}).status_code == 404
    assert stocked.post(f"{T_}/Upper/cards", json={"card_ids": ids["Mountain"]}).status_code == 422  # not a tag
    assert stocked.post(f"{T_}/x/cards", json={"card_ids": []}).status_code == 422
    assert stocked.get(T_).json()["total"] == 0  # nothing was written by the refused ones


def test_limits_per_card_and_per_person_are_enforced_where_they_are_written(stocked, monkeypatch):
    ids = card_ids(stocked)
    monkeypatch.setattr(T, "MAX_PER_CARD", 2)
    monkeypatch.setattr(T, "MAX_TAGS", 3)
    for tag in ("a", "b"):
        assert stocked.post(f"{T_}/{tag}/cards", json={"card_ids": ids["Mountain"]}).status_code == 200
    full = stocked.post(f"{T_}/c/cards", json={"card_ids": ids["Mountain"]})
    assert full.status_code == 409 and "Mountain has 2 tags already" in full.json()["detail"]
    assert stocked.post(f"{T_}/c/cards", json={"card_ids": ids["Sol Ring"]}).status_code == 200  # a, b, c: 3 different tags
    too_many = stocked.post(f"{T_}/d/cards", json={"card_ids": ids["Sol Ring"]})
    assert too_many.status_code == 409 and "3 different tags" in too_many.json()["detail"]
    assert stocked.post(f"{T_}/a/cards", json={"card_ids": ids["Sol Ring"]}).status_code == 200  # an existing name adds no new tag
    assert stocked.post(f"{T_}/b/cards", json={"card_ids": ids["Sol Ring"]}).status_code == 409  # Sol Ring now has c and a


def test_more_than_the_threshold_is_shown_first_and_applied_only_with_confirm(stocked, monkeypatch):
    ids = card_ids(stocked)
    monkeypatch.setattr(T, "CONFIRM_ABOVE", 1)
    both = ids["Sol Ring"][:1] + ids["Mountain"]
    shown = stocked.post(f"{T_}/bulk/cards", json={"card_ids": both}).json()
    assert shown["applied"] is False and shown["count"] == 2 and "confirm" in shown["note"]
    assert stocked.get(f"{T_}/bulk").status_code == 404  # nothing written
    assert stocked.post(f"{T_}/bulk/cards", json={"card_ids": both, "confirm": True}).json()["applied"] is True
    shown = stocked.post(f"{T_}/bulk/cards/remove", json={"card_ids": both}).json()
    assert shown["applied"] is False and stocked.get(f"{T_}/bulk").json()["cards"] == 2
    assert stocked.post(f"{T_}/bulk/cards/remove", json={"card_ids": both, "confirm": True}).json()["removed"] == 2


def test_another_person_sees_none_of_it(app, stocked):
    ids = card_ids(stocked)
    stocked.post(f"{T_}/mine/cards", json={"card_ids": ids["Mountain"]})
    sign_in_as(stocked, "bob@example.com")
    assert stocked.get(T_).json()["total"] == 0
    assert stocked.get(f"{T_}/mine").status_code == 404
    assert stocked.delete(f"{T_}/mine").status_code == 404
    assert stocked.patch(f"{T_}/mine", json={"name": "bobs"}).status_code == 404
    assert stocked.post(f"{T_}/mine/cards", json={"card_ids": ids["Mountain"]}).status_code == 404  # her card id is not in his collection
    sign_in_as(stocked, "dev@localhost")
    with app.state.db.sessions() as db:
        assert db.query(TagAssignment).filter_by(tag="mine").count() == 1  # untouched


def test_a_shared_collection_shows_no_tags_and_cannot_be_filtered_by_them(client):
    sign_in_as(client, "alice@example.com")
    from tests.test_analytics import upload

    upload(client)
    token = client.post("/api/v1/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    sign_in_as(client, "bob@example.com")
    share_id = client.post("/api/v1/shares/accept", json={"token": token}).json()["id"]
    base = f"/api/v1/shared/{share_id}/collection/cards"
    assert client.get(base).status_code == 200 and all("tags" not in c and "tags_detail" not in c for c in client.get(base).json()["items"])
    assert client.get(base, params={"tag": "x"}).status_code == 404


def test_a_read_only_token_may_read_tags_but_not_change_them_and_an_assistant_is_named(stocked):
    ids = card_ids(stocked)
    ro = stocked.post("/api/v1/me/tokens", json={"name": "ro", "scopes": ["read"]}).json()["token"]
    rw = stocked.post("/api/v1/me/tokens", json={"name": "Claude helper", "scopes": ["read", "write"]}).json()["token"]
    stocked.cookies.clear()
    only = {"Authorization": f"Bearer {ro}"}
    assert stocked.get(T_, headers=only).status_code == 200
    assert stocked.post(f"{T_}/t/cards", json={"card_ids": ids["Mountain"]}, headers=only).status_code == 403
    assert stocked.delete(f"{T_}/t", headers=only).status_code == 403
    wrote = stocked.post(f"{T_}/ai-pick/cards", json={"card_ids": ids["Mountain"]}, headers={"Authorization": f"Bearer {rw}"})
    assert wrote.status_code == 200 and wrote.json()["written_by"] == "assistant"
    detail = stocked.get(f"{T_}/ai-pick", headers=only).json()
    assert detail["by_source"] == {"person": 0, "assistant": 1, "system": 0} and detail["assistants"] == ["token 'Claude helper'"]


def test_the_person_accepts_an_assistants_tag_by_tagging_the_card_themselves(stocked):
    ids = card_ids(stocked)
    rw = stocked.post("/api/v1/me/tokens", json={"name": "Claude helper", "scopes": ["read", "write"]}).json()["token"]
    mine = dict(stocked.cookies.items())
    stocked.cookies.clear()
    robot = {"Authorization": f"Bearer {rw}"}
    stocked.post(f"{T_}/idea/cards", json={"card_ids": ids["Mountain"] + ids["Sol Ring"][:1]}, headers=robot)
    again = stocked.post(f"{T_}/idea/cards", json={"card_ids": ids["Mountain"]}, headers=robot).json()
    assert again["accepted"] == 0 and again["already_tagged"] == 1  # an assistant tagging again accepts nothing
    for name, value in mine.items():
        stocked.cookies.set(name, value)
    assert stocked.get(f"{T_}/idea").json()["by_source"] == {"person": 0, "assistant": 2, "system": 0}
    accepted = stocked.post(f"{T_}/idea/cards", json={"card_ids": ids["Mountain"]}).json()
    assert accepted["added"] == 0 and accepted["accepted"] == 1 and accepted["written_by"] == "person"
    detail = stocked.get(f"{T_}/idea").json()
    assert detail["by_source"] == {"person": 1, "assistant": 1, "system": 0} and detail["assistants"] == ["token 'Claude helper'"]


def test_a_retried_post_with_the_same_key_tags_once(stocked):
    ids = card_ids(stocked)
    headers = {"Idempotency-Key": "tag-mountain-1"}
    first = stocked.post(f"{T_}/retry/cards", json={"card_ids": ids["Mountain"]}, headers=headers)
    again = stocked.post(f"{T_}/retry/cards", json={"card_ids": ids["Mountain"]}, headers=headers)
    assert first.status_code == again.status_code == 200 and first.json() == again.json() and first.json()["added"] == 1
    assert stocked.get(f"{T_}/retry").json()["cards"] == 1


def test_each_card_says_who_wrote_each_tag_on_the_list_and_on_the_detail_and_accepting_changes_the_etag(stocked):
    """#128: the web app marks an assistant's tag as the assistant's on the card; the source is per card, not per tag."""
    ids = card_ids(stocked)
    rw = stocked.post("/api/v1/me/tokens", json={"name": "Claude helper", "scopes": ["read", "write"]}).json()["token"]
    mine = dict(stocked.cookies.items())
    stocked.cookies.clear()
    stocked.post(f"{T_}/idea/cards", json={"card_ids": ids["Mountain"]}, headers={"Authorization": f"Bearer {rw}"})
    for name, value in mine.items():
        stocked.cookies.set(name, value)
    stocked.post(f"{T_}/idea/cards", json={"card_ids": ids["Sol Ring"][:1]})  # the person tags another card with the same tag
    stocked.post(f"{T_}/plain/cards", json={"card_ids": ids["Mountain"]})
    items = {c["name"]: c for c in stocked.get(CARDS).json()["items"]}
    assert items["Mountain"]["tags"] == ["idea", "plain"]
    assert items["Mountain"]["tags_detail"] == [{"tag": "idea", "source": "assistant", "source_detail": "token 'Claude helper'"},
                                                {"tag": "plain", "source": "person", "source_detail": None}]
    assert items["Sol Ring"]["tags_detail"] == [{"tag": "idea", "source": "person", "source_detail": None}]
    assert stocked.get(f"{CARDS}/{ids['Mountain'][0]}").json()["tags_detail"] == items["Mountain"]["tags_detail"]
    before = stocked.get(CARDS)
    assert stocked.post(f"{T_}/idea/cards", json={"card_ids": ids["Mountain"]}).json()["accepted"] == 1
    after = stocked.get(CARDS, headers={"If-None-Match": before.headers["etag"]})  # accepting moves no count and no name: still a change
    assert after.status_code == 200 and after.headers["etag"] != before.headers["etag"]
    assert {c["name"]: c for c in after.json()["items"]}["Mountain"]["tags_detail"][0]["source"] == "person"


def test_the_collection_version_moves_with_the_tags_so_a_client_cache_by_version_never_shows_old_tags(stocked):
    ids = card_ids(stocked)
    first = stocked.get("/api/v1/collection")
    stocked.post(f"{T_}/trade/cards", json={"card_ids": ids["Mountain"]})
    second = stocked.get("/api/v1/collection", headers={"If-None-Match": first.headers["etag"]})
    assert second.status_code == 200 and second.json()["version"] != first.json()["version"]
    assert stocked.get("/api/v1/collection").json()["version"] == second.json()["version"]  # and only when they change
