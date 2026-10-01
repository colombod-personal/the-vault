"""Schema migrations (Alembic, vault/migrations): the app upgrades the database at startup.

- The migrations produce exactly the models' schema, so a model change without a migration fails
  here. Write one with: DATABASE_URL=sqlite:///./vault.db alembic revision --autogenerate -m "..."
- A database made by the old create_all is adopted: the baseline adds only missing tables.
- Set VAULT_TEST_POSTGRES_URL to run the same checks against Postgres.
"""

import os
from datetime import datetime, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from vault import models  # noqa: F401
from vault.db import Base, Database

POSTGRES = os.environ.get("VAULT_TEST_POSTGRES_URL")
URLS = ["sqlite"] + (["postgres"] if POSTGRES else [])


@pytest.fixture(params=URLS)
def database(request, tmp_path):
    if request.param == "sqlite":
        db = Database(f"sqlite:///{tmp_path}/schema.db")
        yield db
    else:
        db = Database(POSTGRES)
        _drop_everything(db)
        yield db
        _drop_everything(db)
    db.engine.dispose()


def _drop_everything(db):
    with db.engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


def _revision(db):
    with db.engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _diff(db):
    with db.engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)


def test_migrations_build_exactly_the_models(database):
    database.migrate()
    assert _diff(database) == [], "models and migrations disagree: add a migration (see module docstring)"
    assert _revision(database) is not None


def test_running_again_changes_nothing(database):
    database.migrate()
    before = _revision(database)
    database.migrate()
    assert _revision(database) == before and _diff(database) == []


def test_a_create_all_database_is_adopted_with_its_data(database):
    # an older create_all run: some tables, no alembic_version, and a user in it
    with database.engine.begin() as conn:
        Base.metadata.create_all(conn, tables=[Base.metadata.tables["users"], Base.metadata.tables["identities"]])
        conn.execute(text("INSERT INTO users (name, created_at) VALUES ('Existing', CURRENT_TIMESTAMP)"))
    database.migrate()
    assert _revision(database) is not None and _diff(database) == []
    with database.engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM users")).scalar() == "Existing"
        assert "retired_refresh_tokens" in inspect(conn).get_table_names()


def test_an_old_cards_table_gains_the_new_columns(database):
    # cards as the first release made it (before power/toughness/loyalty/layout), holding a card
    from sqlalchemy import MetaData, Table

    old = Table("cards", MetaData(), *[c.copy() for c in Base.metadata.tables["cards"].columns
                                      if c.name not in ("power", "toughness", "loyalty", "layout")])
    with database.engine.begin() as conn:
        old.create(conn)
        conn.execute(old.insert().values(scryfall_id="sol", name="Sol Ring", set_code="c21", collector_number="263",
                                         colors=[], color_identity=[], finishes=[], updated_at=datetime.now(timezone.utc)))
    database.migrate()
    with database.engine.connect() as conn:
        assert conn.execute(text("SELECT name, power FROM cards")).one() == ("Sol Ring", None)
    assert _diff(database) == []  # including the set/number index the old table lacked


def test_a_full_create_all_database_of_the_current_models_is_adopted(database):
    # create_all of today's models: every table and column already there, no alembic_version
    Base.metadata.create_all(database.engine)
    database.migrate()
    assert _revision(database) == "0006" and _diff(database) == []


def test_duplicate_grants_from_before_are_merged_when_the_unique_index_arrives(database):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    import vault.db

    config = Config()
    config.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))
    with database.engine.begin() as conn:  # the schema as 0003 left it
        config.attributes["connection"] = conn
        command.upgrade(config, "0003")
    with database.engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, name, created_at) VALUES (1, 'Owner', CURRENT_TIMESTAMP), "
                          "(2, 'Guest', CURRENT_TIMESTAMP)"))
        for show in (False, True):  # two grants of the same collection, the newer showing costs
            conn.execute(text("INSERT INTO shares (owner_id, kind, grantee_id, show_costs, created_at) "
                              "VALUES (1, 'collection', 2, :show, CURRENT_TIMESTAMP)"), {"show": show})
        conn.execute(text("INSERT INTO shares (owner_id, kind, token_hash, show_costs, created_at) "
                          "VALUES (1, 'collection', 'pending', false, CURRENT_TIMESTAMP)"))  # an open invite
    database.migrate()
    with database.engine.connect() as conn:
        rows = conn.execute(text("SELECT grantee_id, show_costs FROM shares ORDER BY id")).all()
    assert [(g, bool(s)) for g, s in rows] == [(2, True), (None, False)]  # one grant (the newest), the invite kept
    assert _diff(database) == []
