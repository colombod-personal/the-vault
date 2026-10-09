"""E-mailed one-time codes that confirm it is the person (#347).

``identities.email_verified``: whether the provider vouched for the identity's e-mail address (Google and Apple do); only such an
address receives a code. Existing rows start false and become true at the person's next sign-in with that provider.

``email_codes``: one row per code sent to the address on an account, to confirm a recent sign-in before a serious account action
(vault.recent_signin). Only keyed hashes of the code, of the link token and of the session that asked are stored. Rows live ten
minutes as codes and two days as the record the send caps count; the daily retention job deletes them, and so does erasing
the account. No data changes.

Revision ID: 0122
Revises: 0118
Create Date: 2026-10-09 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0122'
down_revision = '0118'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'email_verified' not in {c['name'] for c in inspector.get_columns('identities')}:
        op.add_column('identities', sa.Column('email_verified', sa.Boolean(), server_default=sa.false(), nullable=False))
    if 'email_codes' in inspector.get_table_names():  # a create_all database has it
        return
    op.create_table(
        'email_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('session_digest', sa.String(length=64), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('link_hash', sa.String(length=64), nullable=False),
        sa.Column('asked_from', sa.String(length=80), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('link_hash'),
    )
    op.create_index(op.f('ix_email_codes_user_id'), 'email_codes', ['user_id'], unique=False)
    op.create_index(op.f('ix_email_codes_created_at'), 'email_codes', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_column('identities', 'email_verified')
    op.drop_index(op.f('ix_email_codes_created_at'), table_name='email_codes')
    op.drop_index(op.f('ix_email_codes_user_id'), table_name='email_codes')
    op.drop_table('email_codes')
