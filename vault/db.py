"""Database engine and session handling (SQLite locally, Postgres in production)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


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
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _foreign_keys(dbapi_conn, _record):  # SQLite ignores ON DELETE CASCADE without this
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    # Serverless functions come and go: check connections before use, keep the pool small, and
    # recycle idle connections (Neon suspends idle compute after a few minutes).
    # Neon's pooled endpoint (DATABASE_URL, host "...-pooler...") is PgBouncer in transaction mode,
    # so psycopg's automatic server-side prepared statements are turned off (prepare_threshold=None).
    return create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2, pool_recycle=240,
                         connect_args={"prepare_threshold": None})


BASELINE = "0001"  # the schema as create_all left it; earlier databases are stamped with it


class Database:
    def __init__(self, url: str):
        self.engine = make_engine(url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        """Bring the schema up to date with the migrations in ``vault/migrations`` (Alembic).

        Cheap when nothing is pending (one query), so it runs at every startup. On Postgres a
        transaction-scoped advisory lock stops two cold starts from migrating at once. A database
        made by the old ``create_all`` (no ``alembic_version`` table) gets its missing tables,
        is stamped as the first revision, then upgraded like any other.
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
            if conn.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_xact_lock(7261)"))  # 7261: the Vault's migration lock
            if MigrationContext.configure(conn).get_current_revision() == head:
                return  # another instance finished while we waited
            config.attributes["connection"] = conn
            tables = set(inspect(conn).get_table_names())
            if "alembic_version" not in tables and "users" in tables:
                Base.metadata.create_all(conn)  # tables added after the last create_all
                command.stamp(config, BASELINE)
            command.upgrade(config, "head")

    def session(self) -> Iterator[Session]:
        with self.sessions() as s:
            yield s
