"""Per-card Limited statistics from 17Lands' public data sets (#178).

``limited_game_stats`` and ``limited_pick_stats``: counts per set, format and card, reduced by ``jobs/sync_limited.py`` from
17Lands' game and draft files (CC BY 4.0), which are never stored. ``limited_sources``: the file version behind each set, format
and kind. No user column: nothing here is personal data. No data changes.

Revision ID: 0120
Revises: 0118
Create Date: 2026-10-09 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0120'
down_revision = '0118'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())  # a create_all database has them
    if 'limited_game_stats' not in existing:
        op.create_table(
            'limited_game_stats',
            sa.Column('set_code', sa.String(length=10), nullable=False),
            sa.Column('format', sa.String(length=20), nullable=False),
            sa.Column('card_name', sa.String(length=200), nullable=False),
            sa.Column('oracle_id', sa.String(length=36), nullable=True),
            sa.Column('games_played', sa.Integer(), nullable=False),
            sa.Column('wins_played', sa.Integer(), nullable=False),
            sa.Column('opening', sa.Integer(), nullable=False),
            sa.Column('wins_opening', sa.Integer(), nullable=False),
            sa.Column('drawn', sa.Integer(), nullable=False),
            sa.Column('wins_drawn', sa.Integer(), nullable=False),
            sa.Column('in_hand', sa.Integer(), nullable=False),
            sa.Column('wins_in_hand', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('set_code', 'format', 'card_name'),
        )
        op.create_index(op.f('ix_limited_game_stats_oracle_id'), 'limited_game_stats', ['oracle_id'], unique=False)
    if 'limited_pick_stats' not in existing:
        op.create_table(
            'limited_pick_stats',
            sa.Column('set_code', sa.String(length=10), nullable=False),
            sa.Column('format', sa.String(length=20), nullable=False),
            sa.Column('card_name', sa.String(length=200), nullable=False),
            sa.Column('oracle_id', sa.String(length=36), nullable=True),
            sa.Column('seen', sa.Integer(), nullable=False),
            sa.Column('last_seen_sum', sa.Integer(), nullable=False),
            sa.Column('picked', sa.Integer(), nullable=False),
            sa.Column('picked_sum', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('set_code', 'format', 'card_name'),
        )
        op.create_index(op.f('ix_limited_pick_stats_oracle_id'), 'limited_pick_stats', ['oracle_id'], unique=False)
    if 'limited_sources' not in existing:
        op.create_table(
            'limited_sources',
            sa.Column('set_code', sa.String(length=10), nullable=False),
            sa.Column('format', sa.String(length=20), nullable=False),
            sa.Column('kind', sa.String(length=10), nullable=False),
            sa.Column('etag', sa.Text(), nullable=True),
            sa.Column('last_modified', sa.DateTime(timezone=True), nullable=True),
            sa.Column('content_length', sa.BigInteger(), nullable=True),
            sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('records', sa.BigInteger(), nullable=False),
            sa.Column('skipped_records', sa.BigInteger(), nullable=False),
            sa.Column('wins', sa.BigInteger(), nullable=True),
            sa.Column('first_picks', sa.BigInteger(), nullable=True),
            sa.Column('empty_first_picks', sa.BigInteger(), nullable=True),
            sa.Column('first_time', sa.DateTime(timezone=True), nullable=True),
            sa.Column('last_time', sa.DateTime(timezone=True), nullable=True),
            sa.Column('cards', sa.Integer(), nullable=False),
            sa.Column('unmatched_cards', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('set_code', 'format', 'kind'),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    if 'limited_sources' in existing:
        op.drop_table('limited_sources')
    if 'limited_pick_stats' in existing:
        op.drop_index(op.f('ix_limited_pick_stats_oracle_id'), table_name='limited_pick_stats')
        op.drop_table('limited_pick_stats')
    if 'limited_game_stats' in existing:
        op.drop_index(op.f('ix_limited_game_stats_oracle_id'), table_name='limited_game_stats')
        op.drop_table('limited_game_stats')
