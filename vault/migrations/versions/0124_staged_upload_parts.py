"""The parts of a collection file sent from a chat app as text (vault-0n9.3).

``staged_upload_parts``: one row per part of a file that ``import_collection_file`` receives as ordered parts (``text``, ``part``,
``parts``, ``lines``, ``sum``), kept only until all parts are there and the whole has been checked (part count, line count and
checksum). The staged upload it belongs to is an ordinary ``staged_uploads`` row (no file yet), and the parts have its lifetime:
``ON DELETE CASCADE`` removes them with it when it expires (the daily retention job), when it is applied or replaced, and with the
account. The primary key is the staged upload and the part's number; ``part_hash`` is the SHA-256 of the part's text, so the same
part sent again is the same row. No data changes.

Revision ID: 0124
Revises: 0123
Create Date: 2026-10-18 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0124'
down_revision = '0123'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'staged_upload_parts' in sa.inspect(op.get_bind()).get_table_names():  # a create_all database has it
        return
    op.create_table(
        'staged_upload_parts',
        sa.Column('staged_upload_id', sa.Integer(), nullable=False),
        sa.Column('part_index', sa.Integer(), nullable=False),
        sa.Column('part_hash', sa.String(length=64), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(['staged_upload_id'], ['staged_uploads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('staged_upload_id', 'part_index'),
    )


def downgrade() -> None:
    op.drop_table('staged_upload_parts')
