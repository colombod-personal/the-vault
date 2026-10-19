"""A restore keeps the copy: when it was restored and as which deck (vault-wxy.18, review of PR 485).

``recovery_items.restored_at`` and ``restored_deck_id``: a restore no longer deletes the item, it marks it, and the copy stays until its
30 days are over, so editing a restored deck and deleting it again can never lose the original. No data changes (nothing is marked).

Revision ID: 0126
Revises: 0125
Create Date: 2026-10-19 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0126'
down_revision = '0125'
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('recovery_items')}
    if 'restored_at' not in columns:  # a create_all database has them
        op.add_column('recovery_items', sa.Column('restored_at', sa.DateTime(timezone=True), nullable=True))
    if 'restored_deck_id' not in columns:
        op.add_column('recovery_items', sa.Column('restored_deck_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('recovery_items', 'restored_deck_id')
    op.drop_column('recovery_items', 'restored_at')
