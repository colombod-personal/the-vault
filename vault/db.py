"""Database engine and session handling (SQLite locally, Postgres in production)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
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


class Database:
    def __init__(self, url: str):
        self.engine = make_engine(url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from . import models  # noqa: F401  (register tables)

        Base.metadata.create_all(self.engine)

    def session(self) -> Iterator[Session]:
        with self.sessions() as s:
            yield s
