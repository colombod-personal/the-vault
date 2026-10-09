"""Import into one bucket (#124, docs/collections.md decisions 1 and 6): a file maps to a single bucket, only that bucket's rows are
compared and replaced, and everything else (other buckets' copies, tags, notes) is untouched. The baseline of the three-way update
is kept per scope. Through the API, the staged upload (and its page), and the MCP tools.

Without ``bucket_id`` nothing changes: tests/test_reimport_merge.py (including the required test of decision 6 for the whole
collection) and tests/test_staged_upload.py stay as they were."""

import hashlib
import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from test_owned_changes import cards  # noqa: F401 - fixture
from test_reimport_merge import (BELFRY, CAVERN, KILLER, MARAUDER, SOL, assistant, copies_of, file, line, person,  # noqa: F401
                                 preview_file, sell, upload)
from vault.models import (Bucket, BucketBaseline, CardAnnotation, CollectionBaseline, Entry, StagedUpload, TagAssignment, User)

BUCKETS = f"{V1}/collection/buckets"
CARDS = f"{V1}/collection/cards"


def whole() -> bytes:
    """A Dragon Shield export with two folders: Binder (foil Sol Ring, Accursed Marauder) and Trade (Killer x4, Belfry Spirit)."""
    return file(line(*SOL, printing="Foil", folder="Binder"), line(*MARAUDER, printing="", folder="Binder"),
                line(*KILLER, qty=4, folder="Trade"), line(*BELFRY, folder="Trade"))


def trade_file(killer=4, belfry=1, extra=(), folder="Somewhere else") -> bytes:
    """A file for the Trade bucket alone. Its rows name other folders on purpose: the bucket is the target, not the folder."""
    rows = []
    if killer:
        rows.append(line(*KILLER, qty=killer, folder=folder))
    if belfry:
        rows.append(line(*BELFRY, qty=belfry, folder=folder))
    rows += [line(*r, qty=q, folder="") for r, q in extra]
    return file(*rows)


def bucket_ids(client) -> dict[str, int]:
    return {b["name"]: b["id"] for b in client.get(BUCKETS).json()["items"]}


def held(app, bucket_name, email="dev@localhost"):
    """What a bucket holds, as stored: (name, set, number, finish, copies, folder), in order."""
    with app.state.db.sessions() as db:
        user = db.scalar(select(User).where(User.email == email))
        rows = db.scalars(select(Entry).join(Bucket, Bucket.id == Entry.bucket_id)
                          .where(Entry.user_id == user.id, func.lower(Bucket.name) == bucket_name.lower())
                          .order_by(Entry.position, Entry.id))
        return [(r.name, (r.set_code or "").lower(), r.collector_number, r.finish, r.quantity, r.folder) for r in rows]


def names_in(app, bucket_name):
    return [r[0] for r in held(app, bucket_name)]


def copies_in(app, bucket_name, name):
    return sum(r[4] for r in held(app, bucket_name) if r[0] == name)


def vault_adds(app, bucket_name, name, set_code, number, quantity=1, email="dev@localhost"):
    """A card added only in the Vault, in a bucket: the end state of a move (folder = the bucket's name)."""
    with app.state.db.sessions() as db:
        user = db.scalar(select(User).where(User.email == email))
        bucket = db.scalar(select(Bucket).where(Bucket.user_id == user.id, func.lower(Bucket.name) == bucket_name.lower()))
        top = db.scalar(select(func.max(Entry.position)).where(Entry.user_id == user.id)) or 0
        db.add(Entry(user_id=user.id, position=top + 1, name=name, set_code=set_code, collector_number=number, finish="nonfoil",
                     quantity=quantity, bucket_id=bucket.id, folder=bucket.name))
        user.collection_version += 1
        db.commit()


def into(client, content, bucket_id, **params):
    return upload(client, content, bucket_id=bucket_id, **params)


def baselines(app):
    """(scoped bases, whole-collection bases)"""
    with app.state.db.sessions() as db:
        return db.query(BucketBaseline).count(), db.query(CollectionBaseline).count()


@pytest.fixture
def stocked(app, person):
    """dev@localhost with the two-folder export imported as a whole: buckets Binder and Trade (and the default Unsorted)."""
    assert upload(person, whole()).status_code == 201
    return person


# -- 1. only that bucket is compared and replaced --------------------------------------------------------------------

def test_a_scoped_import_replaces_only_that_bucket_and_every_other_bucket_is_untouched(app, stocked):
    ids = bucket_ids(stocked)
    listing = {c["name"]: c["id"] for c in stocked.get(CARDS, params={"limit": 100}).json()["items"]}
    stocked.post(f"{V1}/collection/tags/staple/cards", json={"card_ids": [listing["Sol Ring"]]})  # a card in Binder
    stocked.put(f"{CARDS}/{listing['Sol Ring']}/metadata", json={"data": {"why": "in the binder"}})
    stocked.put(f"{BUCKETS}/{ids['Binder']}/metadata", json={"data": {"purpose": "the binder"}})
    binder_before, buckets_before = held(app, "Binder"), stocked.get(BUCKETS).json()
    res = into(stocked, trade_file(killer=2, belfry=0, extra=[(CAVERN, 1)]), ids["Trade"])
    assert res.status_code == 201, res.text
    done = res.json()
    assert done["bucket"] == {"id": ids["Trade"], "name": "Trade"}
    assert held(app, "Trade") == [("A Killer Among Us", "mkm", "167", "nonfoil", 2, "Somewhere else"),
                                  ("Cavern of Souls", "lci", "269", "nonfoil", 1, None)]
    assert held(app, "Binder") == binder_before  # every other bucket's copies, as they were
    after = stocked.get(BUCKETS).json()
    assert [b["name"] for b in after["items"]] == [b["name"] for b in buckets_before["items"]]  # no bucket made or lost
    assert {b["name"]: b["vault_metadata"] for b in after["items"] if b["name"] == "Binder"}["Binder"]["user"] == {"purpose": "the binder"}
    with app.state.db.sessions() as db:  # the tag and the note on the Binder's card
        assert db.query(TagAssignment).filter(TagAssignment.tag == "staple").count() == 1 and db.query(CardAnnotation).count() == 1
    changes = done["changes"]  # the summary is the bucket's alone: the Killer lost 2 copies, Belfry went, the Cavern came
    assert (changes["added"], changes["removed"], changes["decreased"], changes["copies_out"], changes["copies_in"]) == (1, 1, 1, 3, 1)
    assert stocked.get(f"{V1}/imports/{done['id']}").json()["bucket"] == {"id": ids["Trade"], "name": "Trade"}


def test_new_entries_land_in_the_target_bucket_whatever_their_folder_says_and_keep_the_files_folder(app, stocked):
    ids = buckets_before = bucket_ids(stocked)
    mixed = file(line(*KILLER, qty=4, folder="Binder"), line(*BELFRY, folder="Trade"),
                 line(*CAVERN, folder="Brand new folder"), line(*MARAUDER, printing="", folder=""))
    assert into(stocked, mixed, ids["Trade"]).status_code == 201
    assert sorted(names_in(app, "Trade")) == ["A Killer Among Us", "Accursed Marauder", "Belfry Spirit", "Cavern of Souls"]
    assert {r[0]: r[5] for r in held(app, "Trade")} == {"A Killer Among Us": "Binder", "Belfry Spirit": "Trade",
                                                        "Cavern of Souls": "Brand new folder", "Accursed Marauder": None}  # as written
    assert bucket_ids(stocked) == buckets_before  # the folders in the file made no bucket
    assert names_in(app, "Binder") == ["Sol Ring", "Accursed Marauder"]  # and took nothing from Binder
    # the file's folder is the file's word: the export shows it, the bucket is the Vault's grouping
    exported = stocked.get(f"{V1}/collection/export.csv", params={"bucket": ids["Trade"]}).content.decode()
    assert "Brand new folder" in exported and "Binder,4,0,A Killer Among Us" in exported


def test_a_file_with_several_folders_goes_whole_into_the_one_bucket(app, stocked):
    ids = bucket_ids(stocked)
    before_binder = held(app, "Binder")
    assert into(stocked, whole(), ids["Trade"]).status_code == 201  # the whole export, aimed at one bucket
    assert len(held(app, "Trade")) == 4 and held(app, "Binder") == before_binder
    assert sum(1 for r in held(app, "Trade") if r[5] == "Binder") == 2


def test_importing_into_an_empty_bucket_is_a_first_import_and_the_other_buckets_stay(app, stocked):
    made = stocked.post(BUCKETS, json={"name": "Deck box"}).json()
    seen = preview_file(stocked, trade_file(), bucket_id=made["id"])
    assert seen["merge"]["mode"] == "first" and "bucket" in seen["merge"]["how"]
    done = into(stocked, trade_file(), made["id"])
    assert done.status_code == 201 and done.json()["merge"]["mode"] == "first"
    assert names_in(app, "Deck box") == ["A Killer Among Us", "Belfry Spirit"]
    assert names_in(app, "Binder") == ["Sol Ring", "Accursed Marauder"]


def test_replace_everything_in_a_scope_discards_that_buckets_edits_only(app, stocked):
    ids = bucket_ids(stocked)
    vault_adds(app, "Trade", "Sol Ring", "cmr", "472", 2)
    vault_adds(app, "Binder", "Sol Ring", "cmr", "472", 1)
    seen = preview_file(stocked, trade_file(), bucket_id=ids["Trade"], replace_everything=True)
    assert seen["merge"]["mode"] == "replace" and seen["merge"]["replace_everything_discards_vault_edits"] == 1
    assert into(stocked, trade_file(), ids["Trade"], replace_everything=True).status_code == 201
    assert [r for r in held(app, "Trade") if r[1] == "cmr"] == []  # discarded in the bucket
    assert [r for r in held(app, "Binder") if r[1] == "cmr"] != []  # and kept in the other


# -- 2. no bucket_id: today's behaviour ------------------------------------------------------------------------------

def test_without_a_bucket_the_import_is_the_whole_collection_as_before(app, stocked):
    seen = preview_file(stocked, whole())
    assert "bucket" not in seen and "untouched" not in seen  # the answer's shape is unchanged
    only_cavern = file(line(*CAVERN, folder="Trade"))
    done = upload(stocked, only_cavern).json()
    assert done.get("bucket") is None and done["changes"]["removed"] == 4  # everything the file does not have is gone
    assert held(app, "Binder") == []
    assert stocked.get(f"{V1}/collection/export.csv").content == only_cavern


# -- 3. the preview names the bucket and totals what is left alone ------------------------------------------------------

def test_the_preview_names_the_bucket_and_totals_everything_it_leaves_untouched(app, stocked):
    ids = bucket_ids(stocked)
    before = held(app, "Trade")
    seen = preview_file(stocked, trade_file(killer=1, belfry=0), bucket_id=ids["Trade"])
    assert seen["bucket"] == {"id": ids["Trade"], "name": "Trade", "rows": 2, "copies": 5}  # what the bucket holds now
    assert seen["untouched"]["rows"] == 2 and seen["untouched"]["copies"] == 2 and seen["untouched"]["buckets"] == 1
    assert "left as they are" in seen["untouched"]["note"]
    assert seen["changes"]["removed"] == 1 and seen["changes"]["decreased"] == 1  # about the bucket alone
    assert held(app, "Trade") == before  # a preview changes nothing
    assert stocked.get(f"{V1}/imports").json()["total"] == 1


def test_the_preview_of_a_bucket_with_nothing_else_around_totals_zero(app, person):
    only = person.post(BUCKETS, json={"name": "Only"}).json()
    seen = preview_file(person, trade_file(), bucket_id=only["id"])
    assert (seen["untouched"]["rows"], seen["untouched"]["copies"], seen["untouched"]["buckets"]) == (0, 0, 0)


# -- 4. tags and notes survive a re-import (their key is the card, not the entry) ------------------------------------------------

def test_tags_and_notes_survive_a_scoped_re_import_even_when_the_card_leaves_and_comes_back(app, stocked):
    ids = bucket_ids(stocked)
    listing = {c["name"]: c["id"] for c in stocked.get(CARDS, params={"limit": 100}).json()["items"]}
    killer = listing["A Killer Among Us"]
    assert stocked.post(f"{V1}/collection/tags/trade/cards", json={"card_ids": [killer]}).status_code == 200
    assert stocked.put(f"{CARDS}/{killer}/metadata", json={"data": {"why": "extra copies"}}).status_code == 200
    stocked.put(f"{BUCKETS}/{ids['Trade']}/metadata", json={"data": {"purpose": "to trade"}})

    def kept():
        with app.state.db.sessions() as db:
            return db.query(TagAssignment).filter(TagAssignment.tag == "trade").count(), db.query(CardAnnotation).count()

    assert kept() == (1, 1)
    assert into(stocked, trade_file(killer=0), ids["Trade"]).status_code == 201  # the Killer is gone from the file
    assert copies_of(app, "A Killer Among Us") == 0 and kept() == (1, 1)  # but its tag and note are the person's, still there
    assert into(stocked, trade_file(), ids["Trade"]).status_code == 201  # and it comes back
    tagged = stocked.get(CARDS, params={"limit": 100, "tag": "trade"}).json()["items"]
    assert [c["name"] for c in tagged] == ["A Killer Among Us"]
    assert stocked.get(f"{CARDS}/{tagged[0]['id']}/metadata").json()["namespaces"]["user"] == {"why": "extra copies"}
    assert stocked.get(f"{BUCKETS}/{ids['Trade']}/metadata").json()["namespaces"]["user"] == {"purpose": "to trade"}


# -- 5. the three-way update, per scope ---------------------------------------------------------------------------------------

def test_a_card_added_only_in_the_vault_survives_a_changed_file_that_still_omits_it_and_the_same_file_again_in_a_bucket(app, stocked):
    """The required test of docs/collections.md (decision 6) for the bucket scope: import A into the bucket, add X in the Vault,
    import a changed file that still omits X (X kept), import the same file again (X still kept)."""
    ids = bucket_ids(stocked)
    assert into(stocked, trade_file(), ids["Trade"]).status_code == 201  # A
    vault_adds(app, "Trade", "Sol Ring", "cmr", "472", 1)  # X, in no file
    vault_adds(app, "Binder", "Sol Ring", "cmr", "472", 3)  # another bucket's, in no file either
    b = trade_file(extra=[(CAVERN, 2)])
    assert into(stocked, b, ids["Trade"]).status_code == 201
    cmr = lambda bucket: sum(r[4] for r in held(app, bucket) if r[1] == "cmr")  # noqa: E731
    assert cmr("Trade") == 1 and copies_in(app, "Trade", "Cavern of Souls") == 2
    again = into(stocked, b, ids["Trade"])
    assert again.status_code == 201 and cmr("Trade") == 1 and cmr("Binder") == 3
    assert copies_in(app, "Trade", "Cavern of Souls") == 2
    assert again.json()["merge"]["kept_vault_edits"]["count"] == 1 and again.json()["merge"]["mode"] == "merge"


def test_vault_edits_in_the_bucket_stay_and_only_the_apps_changes_are_applied(app, stocked, assistant):
    bot_, token = assistant
    ids = bucket_ids(stocked)
    into(stocked, trade_file(), ids["Trade"])
    sell(bot_, token, "A Killer Among Us", "MKM", "167", 1)  # 4 -> 3 here; the app still says 4
    newer = trade_file(killer=4, belfry=2)
    seen = preview_file(stocked, newer, bucket_id=ids["Trade"])
    assert seen["merge"]["from_your_app"]["increased"] == 1 and seen["merge"]["kept_vault_edits"]["count"] == 1
    assert into(stocked, newer, ids["Trade"]).status_code == 201
    assert {r[0]: r[4] for r in held(app, "Trade")} == {"A Killer Among Us": 3, "Belfry Spirit": 2}


def test_the_first_scoped_import_into_a_bucket_that_came_from_a_whole_import_keeps_the_vaults_edits(app, stocked, assistant):
    """No scope base yet: the base is what the last whole-collection file said about that bucket, so a Vault edit is kept
    instead of the file replacing the bucket."""
    bot_, token = assistant
    ids = bucket_ids(stocked)
    sell(bot_, token, "A Killer Among Us", "MKM", "167", 1)  # 4 -> 3 in Trade
    assert baselines(app) == (0, 1)
    f = trade_file(extra=[(CAVERN, 1)], folder="Trade")  # the folder is part of a card's state: this file names the bucket's own
    seen = preview_file(stocked, f, bucket_id=ids["Trade"])
    assert seen["merge"]["mode"] == "merge" and seen["merge"]["baseline"]["derived_from"]
    assert seen["merge"]["kept_vault_edits"]["count"] == 1 and seen["merge"]["from_your_app"]["added"] == 1
    assert into(stocked, f, ids["Trade"]).status_code == 201
    assert {r[0]: r[4] for r in held(app, "Trade")} == {"A Killer Among Us": 3, "Belfry Spirit": 1, "Cavern of Souls": 1}
    assert baselines(app) == (1, 1)  # now the bucket has a base of its own


def test_a_bucket_that_holds_copies_but_has_no_base_at_all_is_replaced_and_the_preview_says_so(app, stocked):
    ids = bucket_ids(stocked)
    with app.state.db.sessions() as db:
        db.query(CollectionBaseline).delete()
        db.commit()
    seen = preview_file(stocked, trade_file(belfry=0), bucket_id=ids["Trade"])
    assert seen["merge"]["mode"] == "no_baseline"
    assert into(stocked, trade_file(belfry=0), ids["Trade"]).status_code == 201
    assert names_in(app, "Trade") == ["A Killer Among Us"] and len(held(app, "Binder")) == 2


def test_a_whole_import_forgets_the_scoped_bases_and_a_scoped_import_leaves_the_whole_one(app, stocked):
    ids = bucket_ids(stocked)
    with app.state.db.sessions() as db:
        whole_before = json.dumps(db.query(CollectionBaseline).one().cards, sort_keys=True)
    into(stocked, trade_file(), ids["Trade"])
    assert baselines(app) == (1, 1)
    with app.state.db.sessions() as db:
        assert json.dumps(db.query(CollectionBaseline).one().cards, sort_keys=True) == whole_before  # the whole base is as it was
    upload(stocked, whole())
    assert baselines(app) == (0, 1)  # the whole file is the latest word on every bucket: scoped bases are derived from it again


def test_importing_the_same_file_into_the_bucket_twice_changes_nothing_and_a_retry_with_a_key_imports_once(app, stocked):
    ids = bucket_ids(stocked)
    f = trade_file(extra=[(CAVERN, 1)])
    assert into(stocked, f, ids["Trade"]).status_code == 201
    rows, binder = held(app, "Trade"), held(app, "Binder")
    twice = into(stocked, f, ids["Trade"]).json()
    assert held(app, "Trade") == rows and held(app, "Binder") == binder
    assert {k: v for k, v in twice["changes"].items() if k != "unchanged"} == {
        "added": 0, "removed": 0, "increased": 0, "decreased": 0, "copies_in": 0, "copies_out": 0}
    key, before = {"Idempotency-Key": "bucket-import-1"}, stocked.get(f"{V1}/imports").json()["total"]
    send = lambda: stocked.post(f"{V1}/imports", params={"bucket_id": ids["Trade"]},  # noqa: E731
                                files={"file": ("e.csv", trade_file(belfry=3), "text/csv")}, headers=key)
    a, b = send(), send()
    assert a.status_code == b.status_code == 201 and a.json() == b.json() and b.headers.get("Idempotent-Replayed") == "true"
    assert stocked.get(f"{V1}/imports").json()["total"] == before + 1


# -- 6. tenancy and scopes -------------------------------------------------------------------------------------------------------

def test_another_persons_bucket_is_a_404_on_every_way_in_and_nothing_is_imported(app, stocked, assistant):
    bot_, token = assistant
    with TestClient(app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        theirs = bob.post(BUCKETS, json={"name": "Bob's box"}).json()
        before = stocked.get(f"{V1}/imports").json()["total"]
        files = {"file": ("e.csv", trade_file(), "text/csv")}
        assert stocked.post(f"{V1}/imports", params={"bucket_id": theirs["id"]}, files=files).status_code == 404
        assert stocked.post(f"{V1}/imports/preview", params={"bucket_id": theirs["id"]}, files=files).status_code == 404
        assert stocked.post(f"{V1}/uploads", params={"bucket_id": theirs["id"]}).status_code == 404
        assert stocked.post(f"{V1}/imports", params={"bucket_id": 999999}, files=files).status_code == 404
        assert stocked.post(f"{V1}/imports", params={"bucket_id": 0}, files=files).status_code == 422
        for tool, args in (("import_collection_csv", {"csv": trade_file().decode(), "confirm": True}),
                           ("import_collection_csv", {"csv": trade_file().decode()}), ("start_collection_upload", {})):
            assert call_tool(bot_, token, tool, bucket_id=theirs["id"], **args).get("isError"), tool
        assert stocked.get(f"{V1}/imports").json()["total"] == before
        assert bob.get(f"{BUCKETS}/{theirs['id']}").json()["copies"] == 0
        assert held(app, "Bob's box", "bob@example.com") == []


def test_a_read_only_connection_cannot_import_into_a_bucket_or_start_a_bucket_upload(app, stocked, bot):
    ids = bucket_ids(stocked)
    read = make_token(stocked)
    h = {"Authorization": f"Bearer {read['token']}"}
    files = {"file": ("e.csv", trade_file(), "text/csv")}
    assert bot.post(f"{V1}/imports", params={"bucket_id": ids["Trade"]}, files=files, headers=h).status_code == 403
    assert bot.post(f"{V1}/imports/preview", params={"bucket_id": ids["Trade"]}, files=files, headers=h).status_code == 200  # a read
    assert bot.post(f"{V1}/uploads", params={"bucket_id": ids["Trade"]}, headers=h).status_code == 403
    for tool, args in (("import_collection_csv", {"csv": trade_file().decode(), "confirm": True}), ("start_collection_upload", {})):
        assert call_tool(bot, read, tool, bucket_id=ids["Trade"], **args).get("isError"), tool
    assert names_in(app, "Trade") == ["A Killer Among Us", "Belfry Spirit"]


# -- 7. the MCP tools --------------------------------------------------------------------------------------------------------------

def test_the_tools_take_a_bucket_and_say_what_it_does():
    from vault.api import mcp
    for name in ("import_collection_csv", "start_collection_upload", "confirm_staged_upload"):
        tool = mcp.BY_NAME[name]
        assert "bucket_id" in tool.properties and "bucket_id" in tool.query, name
        assert "bucket" in tool.description.lower(), name
        assert "bucket_id" not in tool.required, name


def test_csv_through_the_tool_previews_then_imports_into_the_bucket_only(app, stocked, assistant):
    bot_, token = assistant
    ids = bucket_ids(stocked)
    csv = trade_file(killer=1, belfry=0).decode()
    shown = call_tool(bot_, token, "import_collection_csv", csv=csv, bucket_id=ids["Trade"])["structuredContent"]
    assert shown["bucket"]["name"] == "Trade" and shown["untouched"]["copies"] == 2
    assert len(held(app, "Trade")) == 2  # a preview
    done = call_tool(bot_, token, "import_collection_csv", csv=csv, bucket_id=ids["Trade"], confirm=True)["structuredContent"]
    assert done["bucket"]["name"] == "Trade" and [(r[0], r[4]) for r in held(app, "Trade")] == [("A Killer Among Us", 1)]
    assert len(held(app, "Binder")) == 2
    assert call_tool(bot_, token, "list_imports")["structuredContent"]["items"][0]["bucket"]["name"] == "Trade"


# -- 8. the staged upload: the bucket is bound when the link is made ------------------------------------------------------------

def ticket_of(url: str) -> str:
    return url.split("ticket=")[1]


def put_file(client, ticket, content, **form):
    return client.post("/upload", data={"ticket": ticket, **form}, files={"file": ("big.csv", content, "text/csv")})


def test_a_staged_upload_is_bound_to_its_bucket_from_the_link_to_the_apply(app, stocked, assistant):
    bot_, token = assistant
    ids = bucket_ids(stocked)
    started = call_tool(bot_, token, "start_collection_upload", bucket_id=ids["Trade"])["structuredContent"]
    assert started["bucket"] == {"id": ids["Trade"], "name": "Trade"}
    waiting = call_tool(bot_, token, "get_staged_upload", upload_id=started["id"])["structuredContent"]
    assert waiting["status"] == "waiting" and waiting["bucket"]["name"] == "Trade"
    smaller = trade_file(killer=1, belfry=0)
    with TestClient(app) as browser:
        page = browser.get(f"/upload?ticket={ticket_of(started['url'])}")
        assert page.status_code == 200 and "Trade" in page.text and "<select" not in page.text  # bound: no picker
        received = put_file(browser, ticket_of(started["url"]), smaller)
        assert received.status_code == 200 and "Trade" in received.text and "left as they are" in received.text
        # a posted bucket cannot change what the assistant bound
        assert put_file(browser, ticket_of(started["url"]), smaller, bucket=str(ids["Binder"])).status_code == 400
    preview = call_tool(bot_, token, "confirm_staged_upload", upload_id=started["id"])["structuredContent"]
    assert preview["bucket"]["name"] == "Trade" and preview["untouched"]["copies"] == 2
    assert preview["content_hash"] != hashlib.sha256(smaller).hexdigest()  # the hash covers the bucket as well as the file (#352)
    assert len(held(app, "Binder")) == 2 and len(held(app, "Trade")) == 2  # nothing imported yet
    wrong = call_tool(bot_, token, "confirm_staged_upload", upload_id=started["id"], bucket_id=ids["Binder"], confirm=True,
                      content_hash=preview["content_hash"])
    assert wrong.get("isError") and len(held(app, "Binder")) == 2 and len(held(app, "Trade")) == 2  # only the bound bucket
    done = call_tool(bot_, token, "confirm_staged_upload", upload_id=started["id"], bucket_id=ids["Trade"], confirm=True,
                     content_hash=preview["content_hash"])
    assert not done.get("isError"), done
    assert names_in(app, "Trade") == ["A Killer Among Us"] and len(held(app, "Binder")) == 2
    assert done["structuredContent"]["bucket"]["name"] == "Trade"


def test_an_upload_link_without_a_bucket_is_the_whole_collection_as_before(app, stocked):
    started = stocked.post(f"{V1}/uploads").json()
    assert started["bucket"] is None
    with TestClient(app) as browser:
        assert put_file(browser, ticket_of(started["url"]), whole()).status_code == 200
    seen = stocked.get(f"{V1}/uploads/{started['id']}").json()
    assert seen["status"] == "uploaded" and seen["bucket"] is None and "untouched" not in seen
    assert seen["content_hash"] == hashlib.sha256(whole()).hexdigest()  # unchanged for the whole collection
    assert stocked.post(f"{V1}/uploads/{started['id']}/apply", params={"content_hash": seen["content_hash"]}).status_code == 201


def test_the_hash_of_a_preview_stops_a_changed_bucket_as_well_as_a_changed_file(app, stocked):
    ids = bucket_ids(stocked)
    started = stocked.post(f"{V1}/uploads").json()
    ticket = ticket_of(started["url"])
    with TestClient(app) as browser:
        put_file(browser, ticket, trade_file(killer=1, belfry=0), bucket=str(ids["Trade"]))
        shown = stocked.get(f"{V1}/uploads/{started['id']}").json()
        assert shown["bucket"]["name"] == "Trade"
        put_file(browser, ticket, trade_file(killer=1, belfry=0), bucket=str(ids["Binder"]))  # the link is used again
    stale = stocked.post(f"{V1}/uploads/{started['id']}/apply", params={"content_hash": shown["content_hash"]})
    assert stale.status_code == 409 and len(held(app, "Binder")) == 2 and len(held(app, "Trade")) == 2


def test_an_upload_bound_to_a_bucket_that_is_gone_is_gone_too(app, stocked):
    made = stocked.post(BUCKETS, json={"name": "Temp"}).json()
    started = stocked.post(f"{V1}/uploads", params={"bucket_id": made["id"]}).json()
    assert stocked.delete(f"{BUCKETS}/{made['id']}").status_code == 200
    assert stocked.get(f"{V1}/uploads/{started['id']}").status_code == 404  # never silently the whole collection
    with app.state.db.sessions() as db:
        assert db.get(StagedUpload, started["id"]) is None


# -- 9. the upload page's picker -----------------------------------------------------------------------------------------------------

def test_the_page_offers_a_picker_of_the_persons_own_buckets_only(app, stocked):
    with TestClient(app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        bob.post(BUCKETS, json={"name": "Bobs private binder"})
    started = stocked.post(f"{V1}/uploads").json()
    with TestClient(app) as browser:
        page = browser.get(f"/upload?ticket={ticket_of(started['url'])}").text
    assert "<select" in page and "Binder" in page and "Trade" in page and "Whole collection" in page
    assert "Bobs private binder" not in page  # only this ticket's person's buckets
    options = re.findall(r'<option value="(\d*)"', page)
    assert options[0] == "" and set(options[1:]) == {str(i) for i in bucket_ids(stocked).values()}


def test_a_person_with_one_bucket_gets_no_picker(app, person):
    only = person.post(BUCKETS, json={"name": "Only"}).json()
    assert [b["name"] for b in person.get(BUCKETS).json()["items"]] == ["Only"] and only["id"]
    started = person.post(f"{V1}/uploads").json()
    with TestClient(app) as browser:
        page = browser.get(f"/upload?ticket={ticket_of(started['url'])}").text
    assert "<select" not in page and 'name="file"' in page


def test_choosing_a_bucket_on_the_page_binds_the_upload_to_it(app, stocked):
    ids = bucket_ids(stocked)
    started = stocked.post(f"{V1}/uploads").json()
    ticket, smaller = ticket_of(started["url"]), trade_file(killer=1, belfry=0)
    with TestClient(app) as browser:
        bad = put_file(browser, ticket, smaller, bucket="999999")
        assert bad.status_code == 400 and "bucket" in bad.text.lower()
        with TestClient(app) as bob:
            bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
            theirs = bob.post(BUCKETS, json={"name": "Bob's"}).json()
            assert put_file(browser, ticket, smaller, bucket=str(theirs["id"])).status_code == 400  # not this person's
        assert put_file(browser, ticket, smaller, bucket="not-a-number").status_code == 400
        ok = put_file(browser, ticket, smaller, bucket=str(ids["Trade"]))
        assert ok.status_code == 200 and "Trade" in ok.text and "left as they are" in ok.text
        assert stocked.get(f"{V1}/uploads/{started['id']}").json()["bucket"]["name"] == "Trade"
        assert put_file(browser, ticket, smaller, bucket="").status_code == 200  # back to the whole collection
    shown = stocked.get(f"{V1}/uploads/{started['id']}").json()
    assert shown["bucket"] is None and "untouched" not in shown


# -- 10. privacy ---------------------------------------------------------------------------------------------------------------------------

def test_scoped_bases_are_exported_and_erased_with_the_account(app, stocked):
    ids = bucket_ids(stocked)
    into(stocked, trade_file(), ids["Trade"])
    z = zipfile.ZipFile(io.BytesIO(stocked.get(f"{V1}/me/export").content))
    last = json.loads(z.read("last_import_cards.json"))
    assert [b["bucket"] for b in last["by_bucket"]] == ["Trade"]
    assert {c["name"] for c in last["by_bucket"][0]["cards"]} == {"A Killer Among Us", "Belfry Spirit"}
    res = stocked.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}).json()
    assert res["removed"]["bucket_baselines"] == 1
    with app.state.db.sessions() as db:
        assert db.query(BucketBaseline).count() == 0
