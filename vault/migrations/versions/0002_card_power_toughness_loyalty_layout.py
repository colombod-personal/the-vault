"""Cards: power, toughness, loyalty and layout, so the card drawer needs no Scryfall call.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29 10:43:33.273782
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.add_column(sa.Column('power', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('toughness', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('loyalty', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('layout', sa.String(length=40), nullable=True))

    # A cards table made by an early create_all may predate this index (0001 leaves existing tables as they are).
    op.create_index('ix_cards_set_number', 'cards', ['set_code', 'collector_number'], if_not_exists=True)



def downgrade() -> None:
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.drop_column('layout')
        batch_op.drop_column('loyalty')
        batch_op.drop_column('toughness')
        batch_op.drop_column('power')

