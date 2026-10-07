"""A saved deck remembers when its list was last taken from its source link (#96: deck answers carry fetched_at).

Decks saved before this column existed get their last saved time, the closest thing known.

Revision ID: 0110
Revises: 0109
Create Date: 2026-10-07 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0110'
down_revision = '0109'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('decks')}  # a create_all database has it
    if 'source_fetched_at' not in have:
        op.add_column('decks', sa.Column('source_fetched_at', sa.DateTime(timezone=True), nullable=True))
        op.execute("UPDATE decks SET source_fetched_at = updated_at WHERE source_url IS NOT NULL")


def downgrade() -> None:
    op.drop_column('decks', 'source_fetched_at')
