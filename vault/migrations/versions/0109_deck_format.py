"""Decks can store their format (commander, standard, ...); unset, it is read from the list (vault.deck_overview).

Revision ID: 0109
Revises: 0108
Create Date: 2026-10-06 21:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0109'
down_revision = '0108'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('decks')}  # a create_all database has it
    if 'format' not in have:
        op.add_column('decks', sa.Column('format', sa.String(length=30), nullable=True))


def downgrade() -> None:
    op.drop_column('decks', 'format')
