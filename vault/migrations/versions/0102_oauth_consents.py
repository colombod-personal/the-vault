"""OAuth consent screens: one-time nonces kept in the database, so an answer can be used once.

Revision ID: 0102
Revises: 0101
Create Date: 2026-10-04 15:00:00

Numbered 0101+ with the rest of the OAuth work; rebase ``down_revision`` if another branch's
migrations merge first.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0102'
down_revision = '0101'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'oauth_consents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('nonce_hash', sa.String(length=64), nullable=False),
        sa.Column('query', sa.String(length=2000), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nonce_hash'),
        if_not_exists=True,
    )
    op.create_index('ix_oauth_consents_user_id', 'oauth_consents', ['user_id'], if_not_exists=True)
    op.create_index('ix_oauth_consents_expires_at', 'oauth_consents', ['expires_at'], if_not_exists=True)


def downgrade() -> None:
    op.drop_table('oauth_consents')
