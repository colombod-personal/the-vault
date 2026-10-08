"""Buckets, tags and card annotations (#121, docs/collections.md): the schema, the backfill of existing data, and the rule that
every entry is in exactly one bucket whichever code adds it."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, IntegrityError

from vault.db import Database
from vault.models import Bucket, CardAnnotation, Entry, Identity, TagAssignment, User

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
SOL = "5f8287b1-5bb6-5f4c-ac3d-bf5a14b3b6a4"  # an oracle id (any 36-character string is a key)


@pytest.fixture
def database(blank_database_url):
    db = Database(blank_database_url)
    yield db
    db.engine.dispose()


def _upgrade(database, revision):
    from alembic import command
    from alembic.config import Config

    import vault.db

    config = Config()
    config.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))
    with database.engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


def _entry(conn, user_id, name, folder, quantity=1):
    conn.execute(text("INSERT INTO entries (user_id, position, name, finish, condition, language, folder, quantity, trade_quantity, "
                      "source_prices, extra) VALUES (:u, 0, :n, 'nonfoil', 'near_mint', 'en', :f, :q, 0, '{}', '{}')"),
                 {"u": user_id, "n": name, "f": folder, "q": quantity})


def test_existing_entries_are_given_the_bucket_of_their_folder_and_nothing_is_lost(database):
    _upgrade(database, "0115")  # the schema before buckets
    with database.engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, name, created_at) VALUES (1, 'A', now()), (2, 'B', now()), (3, 'Empty', now())"))
        for name, folder, qty in [("Sol Ring", "Trade Binder", 2), ("Counterspell", "trade binder", 1), ("Mountain", "", 40),
                                  ("Island", None, 3), ("Swamp", " Unsorted ", 1), ("Forest", "Box 1", 4), ("Plains", "box 1 ", 1)]:
            _entry(conn, 1, name, folder, qty)
        _entry(conn, 2, "Sol Ring", None, 1)
        _entry(conn, 2, "Rhystic Study", "Box 1", 1)
    before = _snapshot(database)
    database.migrate()
    with database.engine.connect() as conn:
        names = lambda uid: [r[0] for r in conn.execute(text("SELECT name FROM buckets WHERE user_id = :u ORDER BY position, id"), {"u": uid})]  # noqa: E731
        assert names(1)[0] == "Unsorted" and sorted(names(1)) == ["Box 1", "Trade Binder", "Unsorted"]  # the first spelling is kept
        assert sorted(names(2)) == ["Box 1", "Unsorted"] and names(3) == ["Unsorted"]  # a bucket per person, even with no entries
        got = conn.execute(text("SELECT e.name, e.folder, b.name FROM entries e JOIN buckets b ON b.id = e.bucket_id "
                                "WHERE e.user_id = 1 ORDER BY e.id")).all()
        assert got == [("Sol Ring", "Trade Binder", "Trade Binder"), ("Counterspell", "trade binder", "Trade Binder"),
                       ("Mountain", "", "Unsorted"), ("Island", None, "Unsorted"), ("Swamp", " Unsorted ", "Unsorted"),
                       ("Forest", "Box 1", "Box 1"), ("Plains", "box 1 ", "Box 1")]  # folders untouched (the CSV round-trip)
        other = conn.execute(text("SELECT b.name FROM entries e JOIN buckets b ON b.id = e.bucket_id WHERE e.user_id = 2 ORDER BY e.id")).all()
        assert [r[0] for r in other] == ["Unsorted", "Box 1"]  # user 2's own buckets, not user 1's
        assert conn.execute(text("SELECT count(*) FROM entries WHERE bucket_id IS NULL")).scalar() == 0
    assert _snapshot(database) == before  # copies, rows and folders: the same before and after
    from tests.test_schema_migrations import _diff

    assert _diff(database) == []


def _snapshot(database):
    with database.engine.connect() as conn:
        return (conn.execute(text("SELECT user_id, sum(quantity), count(*) FROM entries GROUP BY user_id ORDER BY user_id")).all(),
                conn.execute(text("SELECT id, folder FROM entries ORDER BY id")).all())


def test_a_new_entry_lands_in_the_bucket_of_its_folder_whichever_code_adds_it(app):
    with app.state.db.sessions() as db:
        a, b = User(name="A"), User(name="B")
        db.add_all([a, b])
        db.flush()
        rows = [Entry(user_id=a.id, name="Sol Ring", folder="Box"), Entry(user_id=a.id, name="Island", folder="box "),
                Entry(user_id=a.id, name="Swamp", folder=None), Entry(user_id=a.id, name="Forest", folder=""),
                Entry(user_id=b.id, name="Sol Ring", folder="Box")]
        db.add_all(rows)
        db.commit()
        by = {(r.user_id, r.name): r.bucket_id for r in rows}
        assert by[(a.id, "Sol Ring")] == by[(a.id, "Island")]  # case and spaces: one bucket
        assert by[(a.id, "Swamp")] == by[(a.id, "Forest")] != by[(a.id, "Sol Ring")]
        assert by[(b.id, "Sol Ring")] not in {by[(a.id, "Sol Ring")], by[(a.id, "Swamp")]}  # another person's own bucket
        names = {x.id: x.name for x in db.scalars(select(Bucket))}
        assert names[by[(a.id, "Sol Ring")]] == "Box" and names[by[(a.id, "Swamp")]] == "Unsorted"
        later = Entry(user_id=a.id, name="Plains", folder="BOX")
        db.add(later)
        db.commit()
        assert later.bucket_id == by[(a.id, "Sol Ring")]  # an existing bucket is found, not duplicated
        assert db.scalar(select(func.count()).select_from(Bucket).where(Bucket.user_id == a.id)) == 2
        assert db.scalar(select(Bucket.kind).where(Bucket.id == by[(a.id, "Swamp")])) == "default"


def test_an_import_and_an_assistants_edit_put_every_copy_in_a_bucket_and_the_totals_add_up(app, signed_in):
    assert signed_in.post("/api/v1/imports", files={"file": ("export.csv", CSV, "text/csv")}).status_code in (200, 201)
    with app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Entry).where(Entry.bucket_id.is_(None))) == 0
        total = db.scalar(select(func.sum(Entry.quantity)))
        per_bucket = db.execute(select(Bucket.name, func.sum(Entry.quantity)).join(Entry, Entry.bucket_id == Bucket.id)
                                .group_by(Bucket.name)).all()
        assert total and sum(q for _, q in per_bucket) == total  # the inventory is the sum of the buckets
        assert {n for n, _ in per_bucket} == {"my cards"}  # the fixture's folder
    signed_in.post("/api/v1/imports", files={"file": ("export.csv", CSV, "text/csv")})  # replacing every entry again
    with app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Entry).where(Entry.bucket_id.is_(None))) == 0
        assert db.scalar(select(func.count()).select_from(Bucket)) == 1  # the same bucket, not a new one each time


@pytest.mark.parametrize("bad", [{"version": 0}, {"version": "1"}, {"version": 1.5}, {"x": 1}, [], {"version": 1, "pad": "x" * 9000}])
def test_vault_metadata_must_be_an_object_with_a_positive_integer_version_and_at_most_8_kb(app, bad):
    with app.state.db.sessions() as db:
        user = User(name="M")
        db.add(user)
        db.commit()
        for row in (Bucket(user_id=user.id, name="X", vault_metadata=bad),
                    TagAssignment(user_id=user.id, oracle_id=SOL, tag="x", vault_metadata=bad),
                    CardAnnotation(user_id=user.id, oracle_id=SOL, vault_metadata=bad)):
            db.add(row)
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        for row in (Bucket(user_id=user.id, name="Ok", vault_metadata={"version": 2, "note": "fine"}),
                    TagAssignment(user_id=user.id, oracle_id=SOL, tag="ok"), CardAnnotation(user_id=user.id, oracle_id=SOL)):
            db.add(row)
        db.commit()  # a valid object, and the default {"version": 1}, are accepted
        assert db.scalar(select(TagAssignment.vault_metadata)) == {"version": 1}


def test_tags_follow_the_agreed_shape_and_a_card_has_each_tag_once(app):
    with app.state.db.sessions() as db:
        user, other = User(name="T"), User(name="U")
        db.add_all([user, other])
        db.commit()
        for tag in ("Trade", "has space", "", "x" * 41, "a_b", "ünï"):
            db.add(TagAssignment(user_id=user.id, oracle_id=SOL, tag=tag))
            with pytest.raises((IntegrityError, DataError)):  # the shape (check) or the 40 characters (the column)
                db.commit()
            db.rollback()
        db.add(TagAssignment(user_id=user.id, oracle_id=SOL, tag="deck:sliver", source="assistant", source_detail="claude.ai"))
        db.add(TagAssignment(user_id=other.id, oracle_id=SOL, tag="deck:sliver"))  # another person may use the same tag
        db.commit()
        db.add(TagAssignment(user_id=user.id, oracle_id=SOL, tag="deck:sliver"))
        with pytest.raises(IntegrityError):  # once per person, card and tag
            db.commit()
        db.rollback()
        db.add(TagAssignment(user_id=user.id, oracle_id=SOL, tag="robot", source="robot"))
        with pytest.raises(IntegrityError):  # who wrote it: person, assistant or system
            db.commit()


def test_a_bucket_name_is_unique_per_person_ignoring_case_and_a_bucket_with_copies_cannot_be_deleted(app):
    with app.state.db.sessions() as db:
        user, other = User(name="P"), User(name="Q")
        db.add_all([user, other])
        db.commit()
        db.add_all([Bucket(user_id=user.id, name="Binder"), Bucket(user_id=other.id, name="binder")])
        db.commit()
        db.add(Bucket(user_id=user.id, name="BINDER"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        entry = Entry(user_id=user.id, name="Sol Ring", folder="Binder")
        db.add(entry)
        db.commit()
        with pytest.raises(IntegrityError):  # RESTRICT: move the copies first
            db.execute(text("DELETE FROM buckets WHERE id = :b"), {"b": entry.bucket_id})
            db.commit()


def test_the_export_lists_buckets_tags_and_annotations_and_deleting_the_account_removes_them(app, signed_in):
    signed_in.post("/api/v1/imports", files={"file": ("export.csv", CSV, "text/csv")})
    with app.state.db.sessions() as db:
        uid = db.scalar(select(func.max(Entry.user_id)))  # the signed-in person, who has just imported
        db.add_all([TagAssignment(user_id=uid, oracle_id=SOL, tag="trade", source="assistant", source_detail="claude.ai"),
                    CardAnnotation(user_id=uid, oracle_id=SOL, vault_metadata={"version": 1, "note": "keep"})])
        db.commit()
    z = zipfile.ZipFile(io.BytesIO(signed_in.get("/api/v1/me/export").content))
    assert {"buckets.json", "tags.json", "card_annotations.json"} <= set(z.namelist())
    buckets = json.loads(z.read("buckets.json"))
    assert [b["name"] for b in buckets] == ["Unsorted", "my cards"] or {b["name"] for b in buckets} >= {"my cards"}
    assert sum(b["copies"] for b in buckets) > 0
    tags = json.loads(z.read("tags.json"))
    assert tags == [{"card": SOL, "tag": "trade", "source": "assistant", "source_detail": "claude.ai", "metadata": {"version": 1},
                     "created_at": tags[0]["created_at"]}]
    assert json.loads(z.read("card_annotations.json"))[0]["metadata"] == {"version": 1, "note": "keep"}
    assert signed_in.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"}).json()["deleted"]
    with app.state.db.sessions() as db:
        for model in (Bucket, TagAssignment, CardAnnotation, Entry):
            assert db.scalar(select(func.count()).select_from(model).where(model.user_id == uid)) == 0, model
