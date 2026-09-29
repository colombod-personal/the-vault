"""Schema migrations (Alembic, vault/migrations): the app upgrades the database at startup.

- The migrations produce exactly the models' schema, so a model change without a migration fails
  here. Write one with: DATABASE_URL=sqlite:///./vault.db alembic revision --autogenerate -m "..."
- A database made by the old create_all is adopted: missing tables added, stamped, upgraded.
- Set VAULT_TEST_POSTGRES_URL to run the same checks against Postgres.
"""

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from vault import models  # noqa: F401
from vault.db import BASELINE, Base, Database

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


def test_baseline_is_the_first_revision(database):
    database.migrate()
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path

    import vault.db

    config = Config()
    config.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))
    assert BASELINE in {r.revision for r in ScriptDirectory.from_config(config).walk_revisions()}
    assert ScriptDirectory.from_config(config).get_base() == BASELINE
