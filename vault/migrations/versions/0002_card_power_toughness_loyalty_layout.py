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


NEW_COLUMNS = (('power', 20), ('toughness', 20), ('loyalty', 20), ('layout', 40))


def upgrade() -> None:
    # A database made by create_all from these models already has the columns: add only missing ones.
    existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('cards')}
    missing = [(name, size) for name, size in NEW_COLUMNS if name not in existing]
    if missing:
        with op.batch_alter_table('cards', schema=None) as batch_op:
            for name, size in missing:
                batch_op.add_column(sa.Column(name, sa.String(length=size), nullable=True))

    # A cards table made by an early create_all may predate this index (0001 leaves existing tables as they are).
    op.create_index('ix_cards_set_number', 'cards', ['set_code', 'collector_number'], if_not_exists=True)



def downgrade() -> None:
    with op.batch_alter_table('cards', schema=None) as batch_op:
        for name, _ in reversed(NEW_COLUMNS):
            batch_op.drop_column(name)

