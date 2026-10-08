"""A saved deck's versions: its list as it was each time its cards changed (#93).

Every deck that exists gets its current list as its first version (``source`` "saved"), so "what changed" has a start for
decks saved before this. The table belongs to the deck (``ON DELETE CASCADE``), so deleting a deck, or an account, deletes
its versions. At most 20 a deck are kept by the code that records them (vault.deck_versions).

Revision ID: 0114
Revises: 0113
Create Date: 2026-10-08 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0114'
down_revision = '0113'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if 'deck_versions' in sa.inspect(bind).get_table_names():  # a create_all database has it
        return
    table = op.create_table(
        'deck_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('deck_id', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('fingerprint', sa.String(length=20), nullable=False),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['deck_id'], ['decks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_deck_versions_deck_id'), 'deck_versions', ['deck_id'], unique=False)
    from vault import deck_versions  # the fingerprint of a list, so the first real change is compared with this one

    rows = [{"deck_id": deck_id, "text": text, "fingerprint": deck_versions.card_fingerprint(text), "source": "saved",
             "created_at": updated_at or created_at}
            for deck_id, text, created_at, updated_at in bind.execute(sa.text("SELECT id, text, created_at, updated_at FROM decks ORDER BY id"))]
    if rows:
        op.bulk_insert(table, rows)


def downgrade() -> None:
    op.drop_index(op.f('ix_deck_versions_deck_id'), table_name='deck_versions')
    op.drop_table('deck_versions')
