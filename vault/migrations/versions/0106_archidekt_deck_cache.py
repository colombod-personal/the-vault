"""A cache of public Archidekt decks, so repeat reads do not reach Archidekt.

Revision ID: 0106
Revises: 0105
Create Date: 2026-10-06 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0106'
down_revision = '0105'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'archidekt_deck_cache',
        sa.Column('deck_id', sa.Integer(), nullable=False),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('deck_id'),
        if_not_exists=True,
    )
    op.create_index('ix_archidekt_deck_cache_fetched_at', 'archidekt_deck_cache', ['fetched_at'], if_not_exists=True)


def downgrade() -> None:
    op.drop_table('archidekt_deck_cache')
