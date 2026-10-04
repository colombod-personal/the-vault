"""OAuth for MCP: clients, grants (connected apps), rotated refresh tokens and authorization codes.

Revision ID: 0101
Revises: 0007
Create Date: 2026-10-04 12:00:00

The revision numbers of this branch start at 0101 so they do not collide with the 0008+ migrations
of other branches. Whichever merges second must rebase ``down_revision`` onto the other's head.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0101'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'oauth_clients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.String(length=512), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('redirect_uris', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_id'),
        if_not_exists=True,
    )
    op.create_index('ix_oauth_clients_expires_at', 'oauth_clients', ['expires_at'], if_not_exists=True)
    op.create_table(
        'oauth_grants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.String(length=512), nullable=False),
        sa.Column('scopes', sa.String(length=40), nullable=False),
        sa.Column('access_scopes', sa.String(length=40), nullable=False),
        sa.Column('resource', sa.String(length=300), nullable=False),
        sa.Column('access_hash', sa.String(length=64), nullable=False),
        sa.Column('access_expires', sa.DateTime(timezone=True), nullable=False),
        sa.Column('refresh_hash', sa.String(length=64), nullable=False),
        sa.Column('refresh_expires', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('access_hash'),
        sa.UniqueConstraint('refresh_hash'),
        if_not_exists=True,
    )
    op.create_index('ix_oauth_grants_user_id', 'oauth_grants', ['user_id'], if_not_exists=True)
    op.create_index('ix_oauth_grants_client_id', 'oauth_grants', ['client_id'], if_not_exists=True)
    op.create_table(
        'oauth_retired_refresh_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('grant_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['grant_id'], ['oauth_grants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
        if_not_exists=True,
    )
    op.create_index('ix_oauth_retired_refresh_tokens_user_id', 'oauth_retired_refresh_tokens', ['user_id'], if_not_exists=True)
    op.create_index('ix_oauth_retired_refresh_tokens_grant_id', 'oauth_retired_refresh_tokens', ['grant_id'], if_not_exists=True)
    op.create_table(
        'oauth_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('client_id', sa.String(length=512), nullable=False),
        sa.Column('redirect_uri', sa.String(length=2000), nullable=False),
        sa.Column('code_challenge', sa.String(length=128), nullable=False),
        sa.Column('resource', sa.String(length=300), nullable=False),
        sa.Column('scopes', sa.String(length=40), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('grant_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code_hash'),
        if_not_exists=True,
    )
    op.create_index('ix_oauth_codes_user_id', 'oauth_codes', ['user_id'], if_not_exists=True)
    op.create_index('ix_oauth_codes_expires_at', 'oauth_codes', ['expires_at'], if_not_exists=True)


def downgrade() -> None:
    op.drop_table('oauth_codes')
    op.drop_table('oauth_retired_refresh_tokens')
    op.drop_table('oauth_grants')
    op.drop_table('oauth_clients')
