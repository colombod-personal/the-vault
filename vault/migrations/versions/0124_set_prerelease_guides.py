"""Prerelease guides: one row per set with the links to Wizards' player guide and WPN event page (vault-064.10.8).

``set_prerelease_guides``: set code, name, prerelease dates, the two page links, the pack composition per format, each fact with
its source URL, the day it was read and a status. Links and short facts only, no page text; no user column, so nothing here is
personal data. The first rows are the manual seed (``vault/prerelease_seed.py``, every URL opened and checked on 2026-10-12);
later rows come from ``jobs/sync_prerelease_guides.py``.

Revision ID: 0124
Revises: 0123
Create Date: 2026-10-12 12:00:00
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = '0124'
down_revision = '0123'
branch_labels = None
depends_on = None

TEXT_COLUMNS = ('set_code', 'set_name', 'dates_status', 'dates_source_url', 'player_guide_url', 'player_guide_title',
                'player_guide_status', 'wpn_event_url', 'wpn_event_title', 'wpn_event_status', 'status', 'note',
                'source_change_note')
DATE_COLUMNS = ('released_at', 'prerelease_start', 'prerelease_end')
TIME_COLUMNS = ('dates_fetched_at', 'player_guide_fetched_at', 'wpn_event_fetched_at', 'last_looked_at', 'created_at', 'updated_at')


def upgrade() -> None:
    bind = op.get_bind()
    if 'set_prerelease_guides' not in set(sa.inspect(bind).get_table_names()):  # a create_all database has it
        op.create_table(
            'set_prerelease_guides',
            sa.Column('set_code', sa.String(length=10), nullable=False),
            sa.Column('set_name', sa.String(length=200), nullable=False),
            sa.Column('released_at', sa.Date(), nullable=True),
            sa.Column('prerelease_start', sa.Date(), nullable=True),
            sa.Column('prerelease_end', sa.Date(), nullable=True),
            sa.Column('dates_status', sa.String(length=12), nullable=False),
            sa.Column('dates_source_url', sa.Text(), nullable=True),
            sa.Column('dates_fetched_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('player_guide_url', sa.Text(), nullable=True),
            sa.Column('player_guide_title', sa.String(length=300), nullable=True),
            sa.Column('player_guide_status', sa.String(length=12), nullable=False),
            sa.Column('player_guide_fetched_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('wpn_event_url', sa.Text(), nullable=True),
            sa.Column('wpn_event_title', sa.String(length=300), nullable=True),
            sa.Column('wpn_event_status', sa.String(length=12), nullable=False),
            sa.Column('wpn_event_fetched_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('composition', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(length=12), nullable=False),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('source_change_note', sa.Text(), nullable=True),
            sa.Column('last_looked_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('set_code'),
        )
    # The manual seed: sets released in the two years before 2026-10-12 and the next announced set. Inserted only where no row
    # exists, so running it twice (or after the job) changes nothing. Written against a description of this revision's table, not
    # the ORM model: a later migration may add columns to the model, and this one must keep working on a fresh database.
    from vault import prerelease_seed

    table = sa.table('set_prerelease_guides',
                     *(sa.column(c, sa.String()) for c in TEXT_COLUMNS), *(sa.column(c, sa.Date()) for c in DATE_COLUMNS),
                     *(sa.column(c, sa.DateTime(timezone=True)) for c in TIME_COLUMNS), sa.column('composition', sa.JSON()))
    have = {r[0] for r in bind.execute(sa.select(table.c.set_code))}
    now = datetime.now(timezone.utc)
    names = (*TEXT_COLUMNS, *DATE_COLUMNS, *TIME_COLUMNS, 'composition')
    rows = [{**{c: row.get(c) for c in names}, 'created_at': now, 'updated_at': now}
            for row in prerelease_seed.seed_rows() if row['set_code'] not in have]
    if rows:
        op.bulk_insert(table, rows)


def downgrade() -> None:
    op.drop_table('set_prerelease_guides')
