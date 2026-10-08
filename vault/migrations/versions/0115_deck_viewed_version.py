"""Which version of a deck the person had last looked at (#93), for "changed since you last looked".

Every deck that exists starts with its latest version as the one seen, so the first open after the upgrade reports nothing
(there is nothing the person has not seen). The column is cleared (``ON DELETE SET NULL``) if that version is dropped
as one of the oldest of a deck's 20.

Revision ID: 0115
Revises: 0114
Create Date: 2026-10-08 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0115'
down_revision = '0114'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if 'viewed_version_id' in {c['name'] for c in sa.inspect(bind).get_columns('decks')}:  # a create_all database has it
        return
    op.add_column('decks', sa.Column('viewed_version_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_decks_viewed_version', 'decks', 'deck_versions', ['viewed_version_id'], ['id'], ondelete='SET NULL')
    op.execute("UPDATE decks SET viewed_version_id = (SELECT max(v.id) FROM deck_versions v WHERE v.deck_id = decks.id)")


def downgrade() -> None:
    op.drop_constraint('fk_decks_viewed_version', 'decks', type_='foreignkey')
    op.drop_column('decks', 'viewed_version_id')
