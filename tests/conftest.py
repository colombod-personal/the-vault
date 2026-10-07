import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from vault.app import create_app
from vault.config import Settings
from vault.db import normalise_url

# The Vault runs on Postgres only, and so do the tests: VAULT_TEST_DATABASE_URL names a Postgres
# database the tests may wipe. Each test that needs one gets it with an empty schema.
TEST_DATABASE_URL = os.environ.get("VAULT_TEST_DATABASE_URL")


def _empty_postgres(url: str) -> None:
    """Start the test from an empty, fully migrated database. Emptying the tables is fast; the
    schema is rebuilt from the migrations only when a test changed it (or on the first run)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    import vault.db
    from vault import models  # noqa: F401  (register tables)
    from vault.db import Base, Database

    config = Config()
    config.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    engine = create_engine(normalise_url(url))
    with engine.begin() as conn:
        tables = set(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars())
        at_head = "alembic_version" in tables and conn.execute(
            text("SELECT version_num FROM alembic_version")).scalar() == head
        if at_head and tables == set(Base.metadata.tables) | {"alembic_version"}:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in Base.metadata.tables)
                              + " RESTART IDENTITY CASCADE"))
            engine.dispose()
            return
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    db = Database(url)
    db.migrate()
    db.engine.dispose()


@pytest.fixture(autouse=True)
def frozen_rate_limit_clock(monkeypatch):
    """Rate limits count in clock-aligned one-minute windows, so a burst that crossed a minute
    boundary would start a fresh window and miss its 429 or 503 (#112). Freeze the clock every
    per-minute counter reads (``tests/frozen_clock.py`` lists the modules: the sign-in and OAuth
    limiter, the catalog and deck-analysis limiter, the metadata-fetch budget) at the test's start.
    Only the module's own ``time`` reference is replaced, and only its ``time()``: ``monotonic``
    and every other clock stay real. A test that moves time sets ``ratelimit.time.time`` itself (the one shared clock).
    ``tests/test_clock_is_frozen.py`` fails when a new per-minute counter is not in the list."""
    import importlib
    import time as real_time

    from frozen_clock import FROZEN_CLOCK_MODULES, FrozenClock

    now = real_time.time()
    frozen = FrozenClock(real_time, now)  # one clock for all of them, so a test that moves it moves every counter
    for name in FROZEN_CLOCK_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "time", frozen)


@pytest.fixture(autouse=True)
def fresh_upstream_guard():
    """Commander Spellbook's client keeps a rate limit and a circuit breaker per process (vault.combos.GUARD): start every test
    with both clear, so one test's failures never open the breaker for the next."""
    from vault import combos

    combos.GUARD.reset()
    yield
    combos.GUARD.reset()


@pytest.fixture
def database_url():
    """An empty Postgres database for this test, migrated to the latest schema."""
    if not TEST_DATABASE_URL:
        pytest.fail("Set VAULT_TEST_DATABASE_URL to a Postgres database the tests may wipe "
                    "(README -> Run it locally)", pytrace=False)
    _empty_postgres(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
def blank_database_url(database_url):
    """A Postgres database with no tables at all, for the tests of the migrations themselves."""
    engine = create_engine(normalise_url(database_url))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    return database_url


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, session_secret="test", dev_login=True,
                    base_url="http://testserver", facebook_client_secret="fb-secret")


@pytest.fixture
def app(settings):
    app = create_app(settings, serve_static=False)
    yield app
    app.state.db.engine.dispose()  # don't let pooled connections pile up across tests


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def signed_in(client):
    assert client.post("/api/auth/dev-login").status_code == 200
    return client
