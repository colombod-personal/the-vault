"""OAuth clients record how they authenticate at the token endpoint: public (none) or private_key_jwt with their keys.

Revision ID: 0108
Revises: 0107
Create Date: 2026-10-06 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0108'
down_revision = '0107'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('oauth_clients')}  # a create_all database has them
    if 'token_auth' not in have:
        op.add_column('oauth_clients', sa.Column('token_auth', sa.String(length=20), server_default='none', nullable=False))
    if 'jwks_uri' not in have:
        op.add_column('oauth_clients', sa.Column('jwks_uri', sa.String(length=512), nullable=True))
    if 'auth_alg' not in have:
        op.add_column('oauth_clients', sa.Column('auth_alg', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('oauth_clients', 'auth_alg')
    op.drop_column('oauth_clients', 'jwks_uri')
    op.drop_column('oauth_clients', 'token_auth')
