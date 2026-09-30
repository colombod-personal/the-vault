"""Passkey challenges kept on the server, so each is used once.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30 20:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'passkey_challenges',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('challenge', sa.String(length=128), nullable=False),
        sa.Column('expires', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        if_not_exists=True,
    )
    op.create_index('ix_passkey_challenges_expires', 'passkey_challenges', ['expires'], if_not_exists=True)


def downgrade() -> None:
    op.drop_index('ix_passkey_challenges_expires', table_name='passkey_challenges')
    op.drop_table('passkey_challenges')
