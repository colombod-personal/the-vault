"""Reset the collection (#129, docs/collections.md decision 6): the whole inventory or one bucket, with a preview, a backup link and
an undo. A reset is an import with an empty target in replace mode for its scope: a card added only in the Vault goes too. Through
the API and the MCP tools: tenancy, preview == effect, an exact undo (whole and one bucket), tags and notes kept by default,
scopes, idempotency, the 7 days and the size cap, the import history."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, inspect, select

from test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from test_tags_api import CARDS, CSV, MOUNTAIN, SOL, card_ids, sign_in_as, stocked  # noqa: F401 - fixtures
from vault import collection_reset, retention
from vault.models import (Bucket, BucketBaseline, CardAnnotation, CollectionBaseline, Entry, Import, ResetSnapshot, TagAssignment, User)

RESET = f"{V1}/collection/reset"
UNDO = f"{RESET}/undo"
BUCKETS = f"{V1}/collection/buckets"
TAGS = f"{V1}/collection/tags"
COLUMNS = [c.key for c in inspect(Entry).mapper.column_attrs]


def me(db, email="dev@localhost"):
    return db.scalar(select(User).where(User.email == email))


def dump(app, email="dev@localhost"):
    """Everything a reset touches, as stored: every column of every row, the baselines, tags and notes, the history's ids."""
    with app.state.db.sessions() as db:
        user = me(db, email)
        entries = [tuple(getattr(e, c) for c in COLUMNS) for e in db.scalars(select(Entry).where(Entry.user_id == user.id).order_by(Entry.id))]
        whole = db.get(CollectionBaseline, user.id)
        buckets = [(b.bucket_id, b.import_id, b.cards, b.created_at) for b in
                   db.scalars(select(BucketBaseline).where(BucketBaseline.user_id == user.id).order_by(BucketBaseline.bucket_id))]
        tags = [(t.oracle_id, t.tag, t.source) for t in db.scalars(select(TagAssignment).where(TagAssignment.user_id == user.id).order_by(TagAssignment.id))]
        notes = [(n.oracle_id, n.vault_metadata) for n in db.scalars(select(CardAnnotation).where(CardAnnotation.user_id == user.id).order_by(CardAnnotation.id))]
        return {"entries": entries, "whole": None if whole is None else (whole.import_id, whole.cards, whole.created_at),
                "buckets": buckets, "tags": tags, "notes": notes,
                "imports": [i.id for i in db.scalars(select(Import).where(Import.user_id == user.id).order_by(Import.id))]}


def preview(client, **body):
    res = client.post(RESET, json=body)
    assert res.status_code == 200, res.text
    return res.json()


def reset(client, body=None, **headers):
    """Preview, then confirm with the preview's confirmation: the way the web app and an assistant do it."""
    body = body or {}
    seen = preview(client, **body)
    assert seen["ready"], seen
    return client.post(RESET, json={**body, "confirmation": seen["confirmation"]}, headers=headers)


def buckets_of(client):
    return {b["name"]: b["id"] for b in client.get(BUCKETS).json()["items"]}


def summary(client, **params):
    return client.get(f"{V1}/collection", params=params).json()


def vault_adds(app, bucket_name, name="Added Here", set_code="zzz", number="1", quantity=2):
    """A card added only in the Vault, in a bucket: the end state of a move or an assistant's add (it is in no file)."""
    with app.state.db.sessions() as db:
        user = me(db)
        bucket = db.scalar(select(Bucket).where(Bucket.user_id == user.id, func.lower(Bucket.name) == bucket_name.lower()))
        top = db.scalar(select(func.max(Entry.position)).where(Entry.user_id == user.id)) or 0
        db.add(Entry(user_id=user.id, position=top + 1, name=name, set_code=set_code, collector_number=number, finish="nonfoil",
                     quantity=quantity, bucket_id=bucket.id, folder=bucket.name))
        user.collection_version += 1
        db.commit()


# -- 1. tenancy -----------------------------------------------------------------------------------------------------------

def test_another_persons_bucket_is_a_404_and_so_is_their_snapshot(app, stocked):
    mine = buckets_of(stocked)["Trade box"]
    assert reset(stocked, {"bucket_id": mine}).status_code == 200  # person A has a snapshot now
    sign_in_as(stocked, "other@localhost")
    stocked.post("/api/v1/imports", files={"file": ("c.csv", CSV, "text/csv")})
    assert stocked.post(RESET, json={"bucket_id": mine}).status_code == 404  # A's bucket id, as B: no 404 vs 403 difference
    assert stocked.post(RESET, json={"bucket_id": 99999}).status_code == 404
    assert stocked.get(RESET).status_code == 404  # B has no reset to undo, whatever A has
    assert stocked.post(UNDO, json={"confirm": True}).status_code == 404
    sign_in_as(stocked, "dev@localhost")
    assert stocked.get(RESET).status_code == 200  # A's snapshot is untouched
    assert dump(app, "other@localhost")["entries"]  # and B's copies were never touched


# -- 2. preview == effect -------------------------------------------------------------------------------------------------

def test_the_preview_numbers_are_what_the_reset_removes_whole_inventory(app, stocked):
    before = summary(stocked)
    seen = preview(stocked)
    removes = seen["removes"]
    assert seen["applied"] is False and seen["scope"] == {"whole_inventory": True, "bucket": None}
    assert (removes["copies"], removes["rows"]) == (before["copies"], 4)
    assert removes["printings"] == before["printings"] and removes["market_value_usd"] == before["market_value"]
    assert removes["unmatched_rows"] == 1 and seen["backup"]["download"] == f"{V1}/collection/export.csv"
    assert dump(app)["entries"], "a preview changes nothing"
    done = stocked.post(RESET, json={"confirmation": seen["confirmation"]}).json()
    assert done["applied"] is True and done["removed"]["copies"] == removes["copies"] and done["removed"]["rows"] == removes["rows"]
    after = summary(stocked)
    assert after["copies"] == 0 and after["printings"] == 0 and after["market_value"] == 0
    assert dump(app)["entries"] == []


def test_the_preview_numbers_are_what_the_reset_removes_one_bucket(app, stocked):
    ids = buckets_of(stocked)
    trade = summary(stocked, bucket=ids["Trade box"])
    binder = summary(stocked, bucket=ids["Binder"])
    seen = preview(stocked, bucket_id=ids["Trade box"])
    assert seen["scope"]["bucket"] == {"id": ids["Trade box"], "name": "Trade box"}
    assert seen["removes"]["copies"] == trade["copies"] == 5 and seen["removes"]["market_value_usd"] == trade["market_value"]
    assert seen["leaves"]["copies"] == binder["copies"] == 5 and seen["leaves"]["buckets"] >= 1  # the other buckets, as an import says
    assert stocked.post(RESET, json={"bucket_id": ids["Trade box"], "confirmation": seen["confirmation"]}).status_code == 200
    assert summary(stocked, bucket=ids["Trade box"])["copies"] == 0
    still = summary(stocked, bucket=ids["Binder"])  # the other bucket is as it was (but the last entry of the history is the reset)
    assert {k: v for k, v in still.items() if k not in ("source", "imported_at", "version")} ==         {k: v for k, v in binder.items() if k not in ("source", "imported_at", "version")}
    assert buckets_of(stocked) == ids  # buckets stay, empty


def test_a_card_added_only_in_the_vault_is_removed_by_a_whole_reset_and_the_preview_says_so(app, stocked):
    vault_adds(app, "Binder", quantity=2)
    seen = preview(stocked)
    only = seen["removes"]["added_in_the_vault_only"]
    assert only["known"] is True and (only["cards"], only["copies"]) == (1, 2)
    assert stocked.post(RESET, json={"confirmation": seen["confirmation"]}).status_code == 200
    assert dump(app)["entries"] == []  # replace mode: a merge would have kept the card that is in neither the baseline nor the empty file
    # and from here the person's file brings everything back, with no leftovers of the Vault-only card
    assert stocked.post("/api/v1/imports", files={"file": ("c.csv", CSV, "text/csv")}).status_code == 201
    assert summary(stocked)["copies"] == 10


def test_the_preview_says_when_it_cannot_tell_what_was_added_only_in_the_vault(app, stocked):
    with app.state.db.sessions() as db:
        db.delete(db.get(CollectionBaseline, me(db).id))
        db.commit()
    only = preview(stocked)["removes"]["added_in_the_vault_only"]
    assert only["known"] is False and "cannot say" in only["note"]


def test_an_empty_scope_has_nothing_to_reset(stocked):
    bucket = stocked.post(BUCKETS, json={"name": "Empty box"}).json()["id"]
    seen = preview(stocked, bucket_id=bucket)
    assert seen["ready"] is False and "nothing to reset" in seen["refused"].lower() and "confirmation" not in seen


# -- 3. the undo restores exactly --------------------------------------------------------------------------------------------

def test_undo_restores_the_whole_inventory_exactly(app, stocked):
    vault_adds(app, "Binder")
    before, export = dump(app), stocked.get(f"{V1}/collection/export.csv").text
    assert reset(stocked).status_code == 200
    assert dump(app)["entries"] == [] and dump(app)["whole"][1] == []  # the empty file is the new baseline
    shown = stocked.post(UNDO, json={}).json()
    assert shown["applied"] is False and shown["restores"]["rows"] == len(before["entries"]) and shown["can_undo"] is True
    assert dump(app)["entries"] == []  # a preview of the undo restores nothing
    done = stocked.post(UNDO, json={"confirm": True})
    assert done.status_code == 200 and done.json()["applied"] is True
    after = dump(app)
    assert after["entries"] == before["entries"]  # ids, positions, buckets, folders, prices, every column
    assert after["whole"] == before["whole"] and after["buckets"] == before["buckets"]
    assert stocked.get(f"{V1}/collection/export.csv").text == export
    assert stocked.get(RESET).status_code == 404  # the snapshot is used once


def test_undo_restores_one_bucket_exactly_and_leaves_the_others_alone(app, stocked):
    ids = buckets_of(stocked)
    stocked.post("/api/v1/imports", params={"bucket_id": ids["Trade box"]},
                 files={"file": ("t.csv", CSV.split(b"\n")[0] + b"\n" + CSV.split(b"\n")[3] + b"\n", "text/csv")})  # the bucket has its own base
    before = dump(app)
    assert before["buckets"], "the bucket has a baseline of its own"
    assert reset(stocked, {"bucket_id": ids["Trade box"]}).status_code == 200
    mid = dump(app)
    assert all(e[COLUMNS.index("bucket_id")] != ids["Trade box"] for e in mid["entries"])
    assert [e for e in mid["entries"] if e[COLUMNS.index("bucket_id")] == ids["Binder"]] == \
        [e for e in before["entries"] if e[COLUMNS.index("bucket_id")] == ids["Binder"]]
    assert stocked.post(UNDO, json={"confirm": True}).status_code == 200
    assert dump(app) == {**before, "imports": dump(app)["imports"]}  # the undo itself is an extra entry in the history


def test_undo_is_refused_when_the_collection_changed_since_and_says_why(app, stocked):
    assert reset(stocked).status_code == 200
    assert stocked.post("/api/v1/imports", files={"file": ("c.csv", CSV, "text/csv")}).status_code == 201
    info = stocked.get(RESET).json()
    assert info["can_undo"] is False and "changed since the reset" in info["why_not"]
    refused = stocked.post(UNDO, json={"confirm": True})
    assert refused.status_code == 409 and "ambiguous" in refused.json()["detail"]
    assert summary(stocked)["copies"] == 10  # nothing was mixed in


def test_undo_is_refused_when_a_bucket_it_needs_was_deleted(app, stocked):
    ids = buckets_of(stocked)
    assert reset(stocked, {"bucket_id": ids["Trade box"]}).status_code == 200
    assert stocked.delete(f"{BUCKETS}/{ids['Trade box']}").status_code == 200  # empty, so it can go
    refused = stocked.post(UNDO, json={"confirm": True})
    assert refused.status_code == 409 and "deleted" in refused.json()["detail"]


# -- 4. tags and notes ---------------------------------------------------------------------------------------------------------

def tag_and_note(client):
    ids = card_ids(client)
    client.post(f"{TAGS}/staple/cards", json={"card_ids": ids["Sol Ring"][:1] + ids["Mountain"]})
    client.put(f"{CARDS}/{ids['Mountain'][0]}/metadata", json={"data": {"note": "keep"}})
    return ids


def test_tags_and_notes_survive_a_reset_by_default_as_not_owned(app, stocked):
    tag_and_note(stocked)
    before = dump(app)
    seen = preview(stocked)
    assert seen["tags"]["keep"] is True and seen["tags"]["removed"] == 0 and seen["tags"]["cards_left_not_owned"] == 2
    assert reset(stocked).status_code == 200
    after = dump(app)
    assert after["tags"] == before["tags"] and after["notes"] == before["notes"] and after["entries"] == []
    assert stocked.get(f"{TAGS}/staple").json()["owned_cards"] == 0  # still tagged, shown as not owned


def test_clearing_tags_and_notes_is_an_option_and_the_undo_puts_them_back(app, stocked):
    tag_and_note(stocked)
    before = dump(app)
    seen = preview(stocked, keep_tags=False)
    assert seen["tags"]["removed"] == 2 and seen["notes"]["removed"] == 1
    assert reset(stocked, {"keep_tags": False}).status_code == 200
    assert dump(app)["tags"] == [] and dump(app)["notes"] == []
    assert stocked.post(UNDO, json={"confirm": True}).status_code == 200
    after = dump(app)
    assert sorted(after["tags"]) == sorted(before["tags"]) and after["notes"] == before["notes"]


def test_a_bucket_reset_clears_only_the_tags_of_cards_that_leave_the_inventory(app, stocked):
    ids = card_ids(stocked)
    stocked.post(f"{TAGS}/staple/cards", json={"card_ids": ids["Sol Ring"][:1] + ids["Mountain"]})  # Sol (Binder) and Mountain (Trade box)
    trade = buckets_of(stocked)["Trade box"]
    assert reset(stocked, {"bucket_id": trade, "keep_tags": False}).status_code == 200
    tagged = {t[0] for t in dump(app)["tags"]}
    assert tagged == {SOL}, "Mountain left the inventory with its tag; Sol Ring is still owned in the Binder"


# -- 5. the history ---------------------------------------------------------------------------------------------------------

def test_the_reset_and_its_undo_are_entries_of_their_own_kind_in_the_history(app, stocked):
    done = reset(stocked).json()
    entry = stocked.get(f"{V1}/imports/{done['reset']}").json()
    assert entry["kind"] == "reset" and entry["reset"]["copies"] == 10 and entry["rows"] == 0 and entry["undone"] is False
    stocked.post(UNDO, json={"confirm": True})
    listed = stocked.get(f"{V1}/imports").json()["items"]
    assert [i["kind"] for i in listed][:3] == ["reset_undo", "reset", "import"]
    assert listed[0]["reset"]["undoes"] == done["reset"] and listed[1]["undone"] is True


def test_clearing_the_history_is_an_option_of_the_whole_reset_and_the_undo_restores_it(app, stocked):
    stocked.post("/api/v1/imports", files={"file": ("c.csv", CSV, "text/csv")})
    before = dump(app)["imports"]
    assert len(before) == 2
    assert preview(stocked, keep_history=False)["history"]["removed"] == 2
    done = reset(stocked, {"keep_history": False}).json()
    assert dump(app)["imports"] == [done["reset"]]  # the reset itself is the one entry
    assert stocked.post(UNDO, json={"confirm": True}).status_code == 200
    assert dump(app)["imports"][:2] == before
    one = stocked.post(RESET, json={"bucket_id": buckets_of(stocked)["Binder"], "keep_history": False})
    assert one.status_code == 422 and "whole inventory" in one.json()["detail"]


# -- 6. the confirmation is bound to the preview --------------------------------------------------------------------------------

def test_a_confirmation_is_for_exactly_the_preview_it_came_from(app, stocked):
    seen = preview(stocked)
    other = stocked.post(RESET, json={"keep_tags": False, "confirmation": seen["confirmation"]})
    assert other.status_code == 409 and "not the reset the person saw" in other.json()["detail"]  # other options
    bucket = stocked.post(RESET, json={"bucket_id": buckets_of(stocked)["Binder"], "confirmation": seen["confirmation"]})
    assert bucket.status_code == 409  # another scope
    vault_adds(app, "Binder")  # the collection changed after the preview
    stale = stocked.post(RESET, json={"confirmation": seen["confirmation"]})
    assert stale.status_code == 409 and dump(app)["entries"], "stale: nothing was removed"
    garbage = stocked.post(RESET, json={"confirmation": "!!!!!!!!!!"})
    assert garbage.status_code == 400


def test_a_confirmation_expires(app, stocked):
    seen = preview(stocked)
    with app.state.db.sessions() as db:
        user = me(db)
        expired = collection_reset._token(app.state.settings.session_secret, user, collection_reset.ResetOptions(), 0, {}, 1)
    res = stocked.post(RESET, json={"confirmation": expired})
    assert res.status_code == 409 and "expired" in res.json()["detail"]
    assert seen["expires_in_seconds"] == 900


def test_tags_changing_after_the_preview_make_a_clearing_reset_stale(app, stocked):
    ids = card_ids(stocked)
    stocked.post(f"{TAGS}/a/cards", json={"card_ids": ids["Mountain"]})
    seen = preview(stocked, keep_tags=False)
    stocked.post(f"{TAGS}/b/cards", json={"card_ids": ids["Mountain"]})  # a tag change does not move the collection version
    stale = stocked.post(RESET, json={"keep_tags": False, "confirmation": seen["confirmation"]})
    assert stale.status_code == 409 and dump(app)["tags"]


# -- 7. scopes, idempotency, limits ----------------------------------------------------------------------------------------------

def test_a_read_only_token_cannot_reset_or_even_preview_but_can_read_the_snapshot(agent, bot):
    read = make_token(agent, scopes=["read"])
    h = {"Authorization": f"Bearer {read['token']}"}
    assert bot.post(RESET, json={}, headers=h).status_code == 403  # the preview is step one of a destructive action
    assert bot.post(RESET, json={"confirmation": "x" * 10}, headers=h).status_code == 403
    assert bot.post(UNDO, json={}, headers=h).status_code == 403
    assert bot.get(RESET, headers=h).status_code == 404  # reading is allowed (there is just nothing yet)
    write = make_token(agent, scopes=["read", "write"])
    assert bot.post(RESET, json={}, headers={"Authorization": f"Bearer {write['token']}"}).status_code == 200


def test_a_retried_reset_with_the_same_key_resets_once_and_answers_the_same(app, stocked):
    seen = preview(stocked)
    body = {"confirmation": seen["confirmation"]}
    first = stocked.post(RESET, json=body, headers={"Idempotency-Key": "reset-1"})
    again = stocked.post(RESET, json=body, headers={"Idempotency-Key": "reset-1"})
    assert first.status_code == again.status_code == 200 and again.headers.get("idempotent-replayed") == "true"
    assert first.json() == again.json()
    with app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Import).where(Import.kind == "reset")) == 1
    third = stocked.post(RESET, json=body, headers={"Idempotency-Key": "reset-2"})
    assert third.status_code == 409  # a new key is a new request: the confirmation no longer fits


def test_the_reset_routes_are_rate_limited(stocked):
    codes = [stocked.post(RESET, json={}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]


# -- 8. the snapshot: 7 days, one per person, the size cap ----------------------------------------------------------------------------

def test_a_second_reset_supersedes_the_snapshot(app, stocked):
    ids = buckets_of(stocked)
    assert reset(stocked, {"bucket_id": ids["Trade box"]}).status_code == 200
    assert reset(stocked, {"bucket_id": ids["Binder"]}).status_code == 200
    info = stocked.get(RESET).json()
    assert info["scope"] == "Binder" and info["removed"]["copies"] == 5
    with app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResetSnapshot)) == 1
    assert stocked.post(UNDO, json={"confirm": True}).status_code == 200  # the Binder comes back; the Trade box reset is not undoable any more
    assert summary(stocked)["copies"] == 5


def test_the_preview_of_a_second_reset_says_it_replaces_the_earlier_snapshot(stocked):
    ids = buckets_of(stocked)
    reset(stocked, {"bucket_id": ids["Trade box"]})
    again = preview(stocked, bucket_id=ids["Binder"])
    assert again["undo"]["replaces_earlier_snapshot"]["scope"] == "Trade box"


def test_the_snapshot_lasts_seven_days_and_the_daily_job_deletes_it(app, stocked):
    reset(stocked)
    info = stocked.get(RESET).json()
    expires = datetime.fromisoformat(info["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(timezone.utc) <= timedelta(days=7)
    with app.state.db.sessions() as db:
        assert retention.apply(db)["reset_snapshots_deleted"] == 0  # not yet
        snap = db.get(ResetSnapshot, me(db).id)
        snap.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert stocked.get(RESET).status_code == 404  # expired: gone for the person even before the job runs
    assert stocked.post(UNDO, json={"confirm": True}).status_code == 404
    with app.state.db.sessions() as db:
        assert retention.apply(db)["reset_snapshots_deleted"] == 1
        assert db.scalar(select(func.count()).select_from(ResetSnapshot)) == 0


def test_a_snapshot_over_the_cap_is_refused_with_the_way_out_and_no_undo_resets_anyway(app, stocked, monkeypatch):
    monkeypatch.setattr(collection_reset, "MAX_SNAPSHOT_BYTES", 500)
    seen = preview(stocked)
    assert seen["ready"] is False and "confirmation" not in seen
    assert "no_undo" in seen["refused"] and "export" in seen["refused"] and seen["backup"]["download"] in seen["refused"]
    assert stocked.post(RESET, json={"confirmation": "x" * 40}).status_code in (400, 409)
    free = preview(stocked, no_undo=True)
    assert free["ready"] is True and free["undo"]["available"] is False
    done = stocked.post(RESET, json={"no_undo": True, "confirmation": free["confirmation"]}).json()
    assert done["applied"] is True and done["undo"]["available"] is False
    assert stocked.get(RESET).status_code == 404 and dump(app)["entries"] == []


def test_the_snapshot_is_small_and_compressed(app, stocked):
    reset(stocked)
    with app.state.db.sessions() as db:
        snap = db.get(ResetSnapshot, me(db).id)
        assert 0 < len(snap.payload) < snap.raw_bytes
    info = stocked.get(RESET).json()
    assert info["snapshot_bytes"] == len(snap.payload) and info["snapshot_json_bytes"] == snap.raw_bytes


# -- 9. an account's erasure and export ------------------------------------------------------------------------------------------

def test_the_snapshot_is_exported_with_the_account_and_erased_with_it(app, stocked):
    import io
    import json
    import zipfile

    from vault.privacy import export_archive, purge_user

    reset(stocked)
    with app.state.db.sessions() as db:
        user = me(db)
        archive = zipfile.ZipFile(io.BytesIO(export_archive(db, user)))
        last = json.loads(archive.read("last_reset.json"))
        assert last["what"]["copies"] == 10 and len(last["removed_rows"]) == 4 and last["removed_rows"][0]["name"]
        assert purge_user(db, user.id)["reset_snapshots"] == 1
        assert db.scalar(select(func.count()).select_from(ResetSnapshot)) == 0


# -- 10. the MCP tools ------------------------------------------------------------------------------------------------------------------

def test_the_tools_are_listed_classified_and_destructive(agent, bot):
    from test_agents import rpc
    from vault.api import mcp

    assert {"reset_collection", "undo_collection_reset"} <= set(mcp.BY_NAME)
    assert "undo_collection_reset" in mcp.OWN_DATA_ONLY and "reset_collection" in mcp.SCRYFALL_DATA  # the preview totals the market value
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=make_token(agent, scopes=["read", "write"])).json()["result"]["tools"]}
    for name in ("reset_collection", "undo_collection_reset"):
        assert tools[name]["annotations"]["destructiveHint"] is True and tools[name]["annotations"]["readOnlyHint"] is False
    read_only = {t["name"] for t in rpc(bot, "tools/list", token=make_token(agent, scopes=["read"])).json()["result"]["tools"]}
    assert not {"reset_collection", "undo_collection_reset"} & read_only


def test_an_assistant_previews_then_resets_one_bucket_and_undoes_it(agent, bot):
    write = make_token(agent, scopes=["read", "write"])
    buckets = call_tool(bot, write, "list_buckets")["structuredContent"]["items"]
    target = buckets[0]
    copies = target["copies"]
    seen = call_tool(bot, write, "reset_collection", bucket_id=target["id"])["structuredContent"]
    assert seen["applied"] is False and seen["removes"]["copies"] == copies and seen["scope"]["bucket"]["name"] == target["name"]
    assert call_tool(bot, write, "list_buckets")["structuredContent"]["items"][0]["copies"] == copies  # the preview changed nothing
    wrong = call_tool(bot, write, "reset_collection", confirmation=seen["confirmation"])  # another scope than the one previewed
    assert wrong.get("isError")
    done = call_tool(bot, write, "reset_collection", bucket_id=target["id"], confirmation=seen["confirmation"])["structuredContent"]
    assert done["applied"] is True and done["removed"]["copies"] == copies
    after = {b["id"]: b for b in call_tool(bot, write, "list_buckets")["structuredContent"]["items"]}
    assert after[target["id"]]["copies"] == 0
    shown = call_tool(bot, write, "undo_collection_reset")["structuredContent"]
    assert shown["applied"] is False and shown["restores"]["copies"] == copies
    back = call_tool(bot, write, "undo_collection_reset", confirm=True)["structuredContent"]
    assert back["applied"] is True
    assert {b["id"]: b["copies"] for b in call_tool(bot, write, "list_buckets")["structuredContent"]["items"]}[target["id"]] == copies
    assert call_tool(bot, write, "undo_collection_reset").get("isError")  # nothing left to undo
