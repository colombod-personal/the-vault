"""Things removed, kept 30 days so they can be put back (vault-wxy.18).

``recovery_items``: one row per removal (a deleted deck today; later the copies an import replaced, a tag, a share), the data needed to
put it back as zlib-compressed JSON, who removed it, and when it stops being restorable. Written in the same transaction as the
removal; deleted by the daily retention job once ``expires_at`` has passed, by the person, and with the account (``ON DELETE
CASCADE``). No data changes.

Revision ID: 0125
Revises: 0124
Create Date: 2026-10-19 03:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0125'
down_revision = '0124'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'recovery_items' in sa.inspect(op.get_bind()).get_table_names():  # a create_all database has it
        return
    op.create_table(
        'recovery_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('payload', sa.LargeBinary(), nullable=False),
        sa.Column('raw_bytes', sa.Integer(), nullable=False),
        sa.Column('stored_bytes', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('source_detail', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('restored_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('restored_deck_id', sa.Integer(), nullable=True),
        sa.CheckConstraint("kind IN ('deck', 'collection_rows', 'tag', 'share')", name='ck_recovery_items_kind'),
        sa.CheckConstraint("source IN ('person', 'assistant')", name='ck_recovery_items_source'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_recovery_items_user_id'), 'recovery_items', ['user_id'], unique=False)
    op.create_index(op.f('ix_recovery_items_expires_at'), 'recovery_items', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_recovery_items_expires_at'), table_name='recovery_items')
    op.drop_index(op.f('ix_recovery_items_user_id'), table_name='recovery_items')
    op.drop_table('recovery_items')
