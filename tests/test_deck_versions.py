"""A saved deck's versions (vault/deck_versions.py, #93): recorded when the cards change and not otherwise, at most 20, what
changed between them, the older list on request, the change in get_deck, and gone with the deck, the account and in the export."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

import vault.db
from vault import deck_versions
from vault.db import Database
from vault.models import Deck, DeckVersion

V1 = "/api/v1"
BASE = "Commander\n1 Sliver Overlord\n\nDeck\n1 Sol Ring\n1 Arcane Signet\n"


def save(client, text=BASE, name="Slivers"):
    res = client.post(f"{V1}/decks", json={"name": name, "text": text})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def edit(client, deck_id, text, name="Slivers"):
    res = client.put(f"{V1}/decks/{deck_id}", json={"name": name, "text": text})
    assert res.status_code == 200, res.text


def versions(client, deck_id):
    res = client.get(f"{V1}/decks/{deck_id}/versions")
    assert res.status_code == 200, res.text
    return res.json()


def test_saving_records_the_first_version_and_nothing_older_is_invented(signed_in):
    deck_id = save(signed_in)
    body = versions(signed_in, deck_id)
    assert body["total"] == 1 and body["keep"] == 20
    (first,) = body["items"]
    assert first["source"] == "saved" and first["cards"] == 3 and first["changes"] is None and first["summary"] is None


def test_a_save_that_changes_no_card_records_nothing(signed_in):
    deck_id = save(signed_in)
    edit(signed_in, deck_id, "Commander\n1 Sliver Overlord\n\nDeck\n1 Arcane Signet\n1 Sol Ring\n", name="Renamed")  # order and name only
    edit(signed_in, deck_id, BASE.replace("1 Sol Ring", "1 sol ring"))  # another spelling of the same card
    assert versions(signed_in, deck_id)["total"] == 1


def test_a_change_of_cards_records_a_version_with_the_adds_and_cuts(signed_in):
    deck_id = save(signed_in)
    edit(signed_in, deck_id, BASE.replace("1 Arcane Signet", "1 Mind Stone\n2 Forest"))
    body = versions(signed_in, deck_id)
    assert body["total"] == 2
    newest, oldest = body["items"]
    assert newest["source"] == "edited" and oldest["changes"] is None
    by_card = {c["card"]: (c["before"], c["after"]) for c in newest["changes"]}
    assert by_card == {"Arcane Signet": (1, 0), "Mind Stone": (0, 1), "Forest": (0, 2)}
    assert newest["summary"]["added"] == 2 and newest["summary"]["removed"] == 1
    # the older list is readable as it was
    older = signed_in.get(f"{V1}/decks/{deck_id}/versions/{oldest['id']}").json()
    assert older["text"] == BASE and older["source"] == "saved"


def test_at_most_twenty_versions_are_kept_and_the_oldest_go_first(signed_in, app):
    deck_id = save(signed_in)
    for n in range(24):
        edit(signed_in, deck_id, BASE + f"{n + 1} Forest\n")
    body = versions(signed_in, deck_id)
    assert body["total"] == 20
    texts = [signed_in.get(f"{V1}/decks/{deck_id}/versions/{v['id']}").json()["text"] for v in body["items"]]
    assert texts[0].endswith("24 Forest\n") and texts[-1].endswith("5 Forest\n")  # versions 1 to 5 (the save and four edits) were dropped
    assert body["items"][-1]["changes"] is None
    with app.state.db.sessions() as db:
        assert db.query(DeckVersion).count() == 20


def test_get_deck_says_what_changed_since_the_previous_version(signed_in):
    deck_id = save(signed_in)
    assert signed_in.get(f"{V1}/decks/{deck_id}").json()["last_change"] is None  # one version: nothing changed yet
    edit(signed_in, deck_id, BASE + "1 Mind Stone\n")
    for detail in ("cards", "summary"):
        last = signed_in.get(f"{V1}/decks/{deck_id}", params={"detail": detail}).json()["last_change"]
        assert last["source"] == "edited" and last["changes"] == [{"section": "Deck", "card": "Mind Stone", "before": 0, "after": 1}]
        assert last["summary"]["added"] == 1


def test_versions_are_private_to_the_decks_owner(signed_in, client):
    deck_id = save(signed_in)
    version_id = versions(signed_in, deck_id)["items"][0]["id"]
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": "other@example.com"}).status_code == 200
    assert client.get(f"{V1}/decks/{deck_id}/versions").status_code == 404
    assert client.get(f"{V1}/decks/{deck_id}/versions/{version_id}").status_code == 404
    other = save(client, name="Mine")
    assert client.get(f"{V1}/decks/{other}/versions/{version_id}").status_code == 404  # another deck's version id is not found either


def test_deleting_a_deck_deletes_its_versions_and_the_export_carries_them(signed_in, app):
    keep = save(signed_in, name="Keep")
    gone = save(signed_in, name="Gone")
    edit(signed_in, gone, BASE + "1 Mind Stone\n", name="Gone")
    z = zipfile.ZipFile(io.BytesIO(signed_in.get(f"{V1}/me/export").content))
    decks = {d["id"]: d for d in json.loads(z.read("decks.json"))}
    assert [v["source"] for v in decks[gone]["versions"]] == ["saved", "edited"] and len(decks[keep]["versions"]) == 1
    assert signed_in.delete(f"{V1}/decks/{gone}").status_code == 200
    with app.state.db.sessions() as db:
        assert {v.deck_id for v in db.scalars(select(DeckVersion))} == {keep}


def test_deleting_the_account_deletes_every_version(signed_in, app):
    deck_id = save(signed_in)
    edit(signed_in, deck_id, BASE + "1 Mind Stone\n")
    assert signed_in.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}).status_code == 200
    with app.state.db.sessions() as db:
        assert db.query(DeckVersion).count() == 0 and db.query(Deck).count() == 0


def test_the_fingerprint_ignores_spelling_order_and_comments_but_not_sections_or_counts():
    a = deck_versions.card_fingerprint(BASE)
    assert a == deck_versions.card_fingerprint("Commander\n1 sliver overlord\n\nDeck\n1 Arcane Signet\n1 SOL RING\n")
    assert a != deck_versions.card_fingerprint(BASE.replace("1 Sol Ring", "2 Sol Ring"))
    assert a != deck_versions.card_fingerprint(BASE.replace("Commander\n1 Sliver Overlord\n\n", "") + "1 Sliver Overlord\n")  # main, not commander


# -- the migration -------------------------------------------------------------------------------

def alembic(database, revision, direction="upgrade"):
    config = Config()
    config.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))
    with database.engine.begin() as conn:
        config.attributes["connection"] = conn
        getattr(command, direction)(config, revision)


def test_the_migration_gives_every_existing_deck_its_current_list_as_the_first_version(blank_database_url):
    database = Database(blank_database_url)
    try:
        alembic(database, "0113")
        with database.engine.begin() as conn:
            conn.execute(text("INSERT INTO users (id, email, created_at, collection_version) VALUES (1, 'a@example.com', now(), 0)"))
            for i, body in enumerate((BASE, BASE + "1 Mind Stone\n"), start=1):
                conn.execute(text("INSERT INTO decks (id, user_id, name, text, created_at, updated_at) VALUES (:i, 1, 'd', :t, now(), now())"),
                             {"i": i, "t": body})
        alembic(database, "0114")
        with database.engine.connect() as conn:
            rows = conn.execute(text("SELECT deck_id, text, source, fingerprint FROM deck_versions ORDER BY deck_id")).all()
        assert [(r[0], r[2]) for r in rows] == [(1, "saved"), (2, "saved")]
        assert rows[0][1] == BASE and rows[0][3] == deck_versions.card_fingerprint(BASE) != rows[1][3]
        alembic(database, "0113", "downgrade")
        with database.engine.connect() as conn:
            assert "deck_versions" not in {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
    finally:
        database.engine.dispose()


def test_the_database_budget_check_counts_the_versions_table(signed_in, app, monkeypatch):
    """#93: jobs/db_budget.py measures every table in the public schema, so versions are inside the guarded size."""
    from jobs import db_budget

    deck_id = save(signed_in)
    edit(signed_in, deck_id, BASE + "1 Mind Stone\n")
    monkeypatch.setattr(db_budget, "TOP_TABLES", 1000)
    with app.state.db.sessions() as db:
        report = db_budget.usage(db)
    assert "deck_versions" in report["tables_mb"] and report["database_mb"] > 0


# -- "changed since you last looked" (#93) -------------------------------------------------------

def seen(client, deck_id, text=None):
    res = client.post(f"{V1}/decks/{deck_id}/seen", json={} if text is None else {"text": text})
    assert res.status_code == 200, res.text
    return res.json()


def test_opening_a_deck_reports_what_changed_since_the_last_time_and_then_only_what_is_new(signed_in):
    deck_id = save(signed_in)
    assert seen(signed_in, deck_id) == {"recorded": False, "since_last_looked": None}  # saved and never seen: nothing to compare
    edit(signed_in, deck_id, BASE.replace("1 Arcane Signet", "1 Mind Stone"))
    out = seen(signed_in, deck_id)["since_last_looked"]
    assert {c["card"]: (c["before"], c["after"]) for c in out["changes"]} == {"Arcane Signet": (1, 0), "Mind Stone": (0, 1)}
    assert out["summary"]["added"] == 1 and out["summary"]["removed"] == 1
    assert seen(signed_in, deck_id)["since_last_looked"] is None  # looked: the next open reports only what is new
    edit(signed_in, deck_id, BASE.replace("1 Arcane Signet", "1 Mind Stone") + "1 Forest\n")
    again = seen(signed_in, deck_id)["since_last_looked"]
    assert [(c["card"], c["after"]) for c in again["changes"]] == [("Forest", 1)]


def test_a_list_the_page_saw_that_differs_from_the_latest_version_is_recorded_as_opened(signed_in):
    deck_id = save(signed_in)
    seen(signed_in, deck_id)
    live = BASE + "1 Mind Stone\n"  # e.g. the deck's list at its source today, not yet saved
    assert seen(signed_in, deck_id, live)["recorded"] is True
    assert versions(signed_in, deck_id)["items"][0]["source"] == "opened"
    assert seen(signed_in, deck_id, live)["recorded"] is False  # the same cards again: nothing new
    assert signed_in.get(f"{V1}/decks/{deck_id}").json()["text"] == BASE  # the saved copy is not replaced by looking


def test_opening_a_deck_that_is_not_yours_or_with_an_empty_list_is_refused(signed_in, client):
    deck_id = save(signed_in)
    assert signed_in.post(f"{V1}/decks/{deck_id}/seen", json={"text": "nothing here"}).status_code == 400
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": "other@example.com"}).status_code == 200
    assert client.post(f"{V1}/decks/{deck_id}/seen", json={}).status_code == 404


def test_migration_0115_marks_each_existing_decks_latest_version_as_seen(blank_database_url):
    database = Database(blank_database_url)
    try:
        alembic(database, "0113")
        with database.engine.begin() as conn:
            conn.execute(text("INSERT INTO users (id, email, created_at, collection_version) VALUES (1, 'a@example.com', now(), 0)"))
            conn.execute(text("INSERT INTO decks (id, user_id, name, text, created_at, updated_at) VALUES (1, 1, 'd', :t, now(), now())"), {"t": BASE})
        alembic(database, "0115")  # 0114 gives the deck its first version, 0115 marks it seen
        with database.engine.connect() as conn:
            viewed, latest = conn.execute(text("SELECT d.viewed_version_id, (SELECT max(id) FROM deck_versions WHERE deck_id = 1) FROM decks d")).one()
        assert viewed is not None and viewed == latest
        alembic(database, "0114", "downgrade")
        with database.engine.connect() as conn:
            assert "viewed_version_id" not in {r[0] for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'decks'"))}
    finally:
        database.engine.dispose()
