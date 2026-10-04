"""The artist and image link of the printing shown for each Oracle card (credited wherever the image is shown).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-04 01:34:41
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('oracle_cards')}  # a create_all database has them
    if 'artist' not in have:
        op.add_column('oracle_cards', sa.Column('artist', sa.String(length=200), nullable=True))
    if 'image_normal' not in have:
        op.add_column('oracle_cards', sa.Column('image_normal', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('oracle_cards', 'image_normal')
    op.drop_column('oracle_cards', 'artist')
