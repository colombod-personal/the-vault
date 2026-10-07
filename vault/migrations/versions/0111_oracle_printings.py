"""Every priced paper printing with today's price, for shopping lists that pick a printing under the person's rules (#29).

Revision ID: 0111
Revises: 0110
Create Date: 2026-10-07 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0111'
down_revision = '0110'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'oracle_printings' in sa.inspect(op.get_bind()).get_table_names():
        return  # a create_all database has it
    op.create_table('oracle_printings',
    sa.Column('scryfall_id', sa.String(length=36), nullable=False),
    sa.Column('oracle_id', sa.String(length=36), nullable=False),
    sa.Column('set_code', sa.String(length=10), nullable=False),
    sa.Column('set_name', sa.String(length=100), nullable=True),
    sa.Column('collector_number', sa.String(length=20), nullable=False),
    sa.Column('lang', sa.String(length=5), nullable=False),
    sa.Column('usd', sa.Float(), nullable=True),
    sa.Column('usd_foil', sa.Float(), nullable=True),
    sa.Column('usd_etched', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('scryfall_id')
    )
    op.create_index(op.f('ix_oracle_printings_oracle_id'), 'oracle_printings', ['oracle_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_oracle_printings_oracle_id'), table_name='oracle_printings')
    op.drop_table('oracle_printings')
