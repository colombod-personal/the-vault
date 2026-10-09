"""Where a person buys: the country they chose and up to three shops they typed (#212).

``buy_settings``: one row per person, deleted with the account and exported with it. ``country`` is a two-letter code the person chose
(never derived from an address); ``stores`` is a JSON list of at most three ``{name, url, search_url}`` the person typed. Nothing else is
stored: no postcode, address, coordinates, IP address or device language. No data changes.

Revision ID: 0121
Revises: 0120
Create Date: 2026-10-09 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0121'
down_revision = '0120'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if 'buy_settings' in sa.inspect(bind).get_table_names():  # a create_all database has it
        return
    op.create_table(
        'buy_settings',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('stores', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )


def downgrade() -> None:
    op.drop_table('buy_settings')
