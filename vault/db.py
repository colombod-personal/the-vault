"""Database engine and session handling. The Vault runs on Postgres only: in production (Neon),
in development and in the tests, so what is tested is what runs."""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


log = logging.getLogger("vault.access")


class Base(DeclarativeBase):
    pass


def normalise_url(url: str) -> str:
    """Neon/Vercel hand out ``postgres://`` URLs; SQLAlchemy wants an explicit driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(url: str) -> Engine:
    url = normalise_url(url)
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must be a Postgres URL (postgresql://…); the Vault runs on Postgres only")
    # Serverless functions come and go: check connections before use, keep the pool small, and
    # recycle idle connections (Neon suspends idle compute after a few minutes).
    # Neon's pooled endpoint (DATABASE_URL, host "...-pooler...") is PgBouncer in transaction mode,
    # so psycopg's automatic server-side prepared statements are turned off (prepare_threshold=None).
    # An MCP tool call used to hold one connection for its whole request while its in-process API call opened two or three
    # more, so a pool of 2+2 was exhausted by two concurrent calls on one instance and the second one waited 30 s and
    # failed with a 500 (found in a real council run, #169). A request now needs one connection at a time (tests/
    # test_parallel_load.py proves it with a pool of one), so calls queue for the pool instead of starving it. Neon's pooler
    # takes many client connections cheaply, hence 6+14 per instance and a short wait that answers 503 with Retry-After.
    # A database with its own small connection limit (a direct Neon URL, a small role) needs DB_POOL_SIZE and
    # DB_MAX_OVERFLOW set so that instances x (size + overflow) stay under it (docs/ai-integration-testing.md).
    # A connection that is refused or times out (a compute waking from suspend, a database at its limit) is tried again
    # (DB_CONNECT_ATTEMPTS, DB_CONNECT_RETRY_DELAY); the wait per attempt is bounded by connect_timeout.
    engine = create_engine(
        url, pool_pre_ping=True, pool_recycle=240,
        connect_args={"prepare_threshold": None, "connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "8")),
                      "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3},
        pool_size=int(os.environ.get("DB_POOL_SIZE", "6")),
        max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "14")),
        pool_timeout=float(os.environ.get("DB_POOL_TIMEOUT", "8")))
    _retry_connects(engine)
    return engine


def _retryable(exc: Exception) -> bool:
    """Can trying to connect again help? Not for a wrong password, a database that does not exist or a privilege error
    (SQLSTATE classes 28, 3D, 42); yes for too many connections (53300), the database starting up (57P03), and
    for failures with no SQLSTATE at all, which are the network's (refused, reset, timed out, name not resolved)."""
    code = getattr(exc, "sqlstate", None)
    return not code or not code.startswith(("28", "3D", "42"))


def _retry_connects(engine: Engine) -> None:
    @event.listens_for(engine, "do_connect")
    def connect(dialect, conn_rec, cargs, cparams):
        attempts = max(1, int(os.environ.get("DB_CONNECT_ATTEMPTS", "4")))
        delay = float(os.environ.get("DB_CONNECT_RETRY_DELAY", "0.5"))
        driver = dialect.loaded_dbapi
        for attempt in range(1, attempts + 1):
            try:
                return driver.connect(*cargs, **cparams)
            except driver.OperationalError as exc:
                if attempt == attempts or not _retryable(exc):
                    raise
                log.warning(json.dumps({"event": "db_connect_retry", "attempt": attempt, "of": attempts,
                                        "error_class": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None)}))
                time.sleep(delay * 2 ** (attempt - 1) * (0.5 + random.random()))


class Database:
    def __init__(self, url: str):
        self.engine = make_engine(url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.pending_migration = False
        self._migrating = threading.Lock()

    def start(self) -> None:
        """Migrate at startup. A database that cannot be reached right now (Neon waking from suspend, a connection limit)
        used to raise out of the function's import, so the whole instance failed and the request that caused the cold start
        got a 500; now the instance comes up, and the first request that needs the database finishes the job
        (:meth:`ensure_migrated`) or is answered 503 with Retry-After like any other."""
        try:
            self.migrate()
        except (OperationalError, InterfaceError) as exc:
            self.pending_migration = True
            log.warning(json.dumps({"event": "migration_deferred", "error_class": type(exc).__name__}))

    def ensure_migrated(self) -> None:
        if not self.pending_migration:
            return
        with self._migrating:
            if self.pending_migration:
                self.migrate()
                self.pending_migration = False

    def migrate(self) -> None:
        """Bring the schema up to date with the migrations in ``vault/migrations`` (Alembic).

        Cheap when nothing is pending (one query), so it runs at every startup. A
        transaction-scoped advisory lock stops two cold starts from migrating at once. A database
        made by the old ``create_all`` (no ``alembic_version`` table) goes through the same chain:
        the baseline revision only creates the tables it doesn't have yet.
        """
        from alembic import command
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        from . import models  # noqa: F401  (register tables)

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        head = ScriptDirectory.from_config(config).get_current_head()
        with self.engine.connect() as conn:
            if MigrationContext.configure(conn).get_current_revision() == head:
                return
        with self.engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(7261)"))  # 7261: the Vault's migration lock
            if MigrationContext.configure(conn).get_current_revision() == head:
                return  # another instance finished while we waited
            config.attributes["connection"] = conn
            command.upgrade(config, "head")

    def session(self) -> Iterator[Session]:
        with self.sessions() as s:
            yield s
