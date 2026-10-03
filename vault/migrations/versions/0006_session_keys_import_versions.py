"""Per-user web session keys, collection versions, and one passkey identity per account.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01 15:00:00
"""

from __future__ import annotations

import secrets

import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c['name'] for c in sa.inspect(bind).get_columns('users')}  # a create_all database may have them
    with op.batch_alter_table('users') as batch:
        if 'session_key' not in existing:
            batch.add_column(sa.Column('session_key', sa.String(length=32), nullable=True))
        if 'collection_version' not in existing:
            batch.add_column(sa.Column('collection_version', sa.Integer(), server_default='0', nullable=False))
    users = sa.table('users', sa.column('id', sa.Integer), sa.column('session_key', sa.String))
    for (uid,) in bind.execute(sa.select(users.c.id).where(users.c.session_key.is_(None))).all():
        bind.execute(users.update().where(users.c.id == uid).values(session_key=secrets.token_hex(16)))
    # A second passkey identity on one account (only possible through a race) could never sign
    # in; keep the oldest.
    bind.execute(sa.text(
        "DELETE FROM identities WHERE provider = 'passkey' AND id NOT IN "
        "(SELECT min(id) FROM identities WHERE provider = 'passkey' GROUP BY user_id)"))
    op.create_index('uq_identities_one_passkey', 'identities', ['user_id'], unique=True,
                    postgresql_where=sa.text("provider = 'passkey'"), if_not_exists=True)


def downgrade() -> None:
    op.drop_index('uq_identities_one_passkey', table_name='identities')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('collection_version')
        batch.drop_column('session_key')
