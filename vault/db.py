"""Database engine and session handling. The Vault runs on Postgres only: in production (Neon),
in development and in the tests, so what is tested is what runs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, text
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
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must be a Postgres URL (postgresql://…); the Vault runs on Postgres only")
    # Serverless functions come and go: check connections before use, keep the pool small, and
    # recycle idle connections (Neon suspends idle compute after a few minutes).
    # Neon's pooled endpoint (DATABASE_URL, host "...-pooler...") is PgBouncer in transaction mode,
    # so psycopg's automatic server-side prepared statements are turned off (prepare_threshold=None).
    # An MCP tool call holds one connection for its whole request and makes an in-process API call that opens two
    # or three more, so a pool of 2+2 was exhausted by two concurrent calls on one instance and the second one waited
    # 30 s and failed with a 500 (found in a real council run, #169). Neon's pooler is PgBouncer, which takes many
    # client connections cheaply: a larger pool per instance is fine, and a short wait answers 503 with Retry-After.
    return create_engine(url, pool_pre_ping=True, pool_recycle=240, connect_args={"prepare_threshold": None},
                         pool_size=int(os.environ.get("DB_POOL_SIZE", "6")),
                         max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "14")),
                         pool_timeout=float(os.environ.get("DB_POOL_TIMEOUT", "8")))


class Database:
    def __init__(self, url: str):
        self.engine = make_engine(url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

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
