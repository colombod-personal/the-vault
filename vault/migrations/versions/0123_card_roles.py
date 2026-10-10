"""What a card does, stored with the card (#435, docs/functional-equivalents.md section 13).

``oracle_cards.roles``: the Vault's own reading of the Oracle text in its 22-role vocabulary (``vault.card_roles``), a JSON object
``{slug: {strength, rule, repeatable}}``, with a GIN index so "which of the person's cards have these roles" is one indexed query.
``oracle_cards.roles_version``: the version of the rules that read it (null: not read yet).

No data is written here. The roles are part of each row's ``content_hash``, so the next catalog load (the daily job) finds every
row changed once, writes the roles with it, and rewrites a row again only when its text or the rules change. Until that load
has run, a card whose ``roles_version`` is null is read on the fly by the card endpoints and the role filters match nothing.

Revision ID: 0123
Revises: 0122
Create Date: 2026-10-10 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0123'
down_revision = '0122'
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c['name'] for c in inspector.get_columns('oracle_cards')}
    if 'roles' not in columns:
        op.add_column('oracle_cards', sa.Column('roles', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    if 'roles_version' not in columns:
        op.add_column('oracle_cards', sa.Column('roles_version', sa.String(length=20), nullable=True))
    if 'ix_oracle_cards_roles' not in {i['name'] for i in inspector.get_indexes('oracle_cards')}:
        op.create_index('ix_oracle_cards_roles', 'oracle_cards', ['roles'], unique=False, postgresql_using='gin')


def downgrade() -> None:
    op.drop_index('ix_oracle_cards_roles', table_name='oracle_cards', postgresql_using='gin')
    op.drop_column('oracle_cards', 'roles_version')
    op.drop_column('oracle_cards', 'roles')
