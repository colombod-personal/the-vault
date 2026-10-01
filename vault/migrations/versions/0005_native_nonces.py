"""Native sign-in nonces, so each Apple / Google ID token signs in once.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'native_nonces',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('expires', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        if_not_exists=True,
    )
    op.create_index('ix_native_nonces_expires', 'native_nonces', ['expires'], if_not_exists=True)


def downgrade() -> None:
    op.drop_index('ix_native_nonces_expires', table_name='native_nonces')
    op.drop_table('native_nonces')
