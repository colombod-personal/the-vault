"""The undo snapshot of a collection reset (#129).

``reset_snapshots``: one row per person, the latest reset's removed rows as compact JSON compressed with zlib (valid for 7 days,
deleted by the daily retention job, when the undo is used and with the account). No data changes.

Revision ID: 0118
Revises: 0117
Create Date: 2026-10-09 15:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0118'
down_revision = '0117'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if 'reset_snapshots' in sa.inspect(bind).get_table_names():  # a create_all database has it
        return
    op.create_table(
        'reset_snapshots',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('import_id', sa.Integer(), nullable=True),
        sa.Column('bucket_id', sa.Integer(), nullable=True),
        sa.Column('version_after', sa.Integer(), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('payload', sa.LargeBinary(), nullable=False),
        sa.Column('raw_bytes', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['import_id'], ['imports.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )
    op.create_index(op.f('ix_reset_snapshots_expires_at'), 'reset_snapshots', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_reset_snapshots_expires_at'), table_name='reset_snapshots')
    op.drop_table('reset_snapshots')
