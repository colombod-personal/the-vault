"""Staged collection uploads: a file the person uploads through a one-time link, held until they confirm.

Revision ID: 0103
Revises: 0102
Create Date: 2026-10-04 23:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0103'
down_revision = '0102'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'staged_uploads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ticket_hash', sa.String(length=64), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('content', sa.LargeBinary(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ticket_hash'),
        if_not_exists=True,
    )
    op.create_index('ix_staged_uploads_user_id', 'staged_uploads', ['user_id'], if_not_exists=True)
    op.create_index('ix_staged_uploads_expires_at', 'staged_uploads', ['expires_at'], if_not_exists=True)


def downgrade() -> None:
    op.drop_table('staged_uploads')
