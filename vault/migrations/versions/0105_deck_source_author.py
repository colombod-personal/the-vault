"""Saved decks keep their source's author, so a saved copy shown while the source can't be reached is
still credited to its author (Archidekt's attribution terms).

Revision ID: 0105
Revises: 0104
Create Date: 2026-10-05 11:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0105'
down_revision = '0104'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('decks')}  # a create_all database has it
    if 'source_author' not in have:
        op.add_column('decks', sa.Column('source_author', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('decks', 'source_author')
