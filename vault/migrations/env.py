"""Alembic environment for the Vault. The app runs migrations itself (``Database.migrate``);
the ``alembic`` command line (see ``alembic.ini``) is for writing new ones:

    DATABASE_URL=sqlite:///./vault.db alembic revision --autogenerate -m "what changed"
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from vault import models  # noqa: F401  (registers every table on Base.metadata)
from vault.db import Base, normalise_url

target_metadata = Base.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite can't ALTER most things in place
        compare_type=True,
    )


def run_migrations_offline() -> None:
    context.configure(url=normalise_url(os.environ.get("DATABASE_URL", "sqlite:///./vault.db")),
                      target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:  # called from Database.migrate with an open connection
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(normalise_url(os.environ.get("DATABASE_URL", "sqlite:///./vault.db")))
    with engine.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
