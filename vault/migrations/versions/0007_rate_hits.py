"""Rate-limit counters for the sign-in endpoints.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-01 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'rate_hits',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('minute', sa.Integer(), nullable=False),
        sa.Column('hits', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('key', 'minute'),
        if_not_exists=True,
    )
    op.create_index('ix_rate_hits_minute', 'rate_hits', ['minute'], if_not_exists=True)


def downgrade() -> None:
    op.drop_index('ix_rate_hits_minute', table_name='rate_hits')
    op.drop_table('rate_hits')
