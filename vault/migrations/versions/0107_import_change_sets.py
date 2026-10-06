"""Imports record assistant change sets too: their kind, the app that made them, and their exact changes.

Revision ID: 0107
Revises: 0106
Create Date: 2026-10-06 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0107'
down_revision = '0106'
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('imports')}  # a create_all database has them
    if 'kind' not in have:
        op.add_column('imports', sa.Column('kind', sa.String(length=20), server_default='import', nullable=False))
    if 'app' not in have:
        op.add_column('imports', sa.Column('app', sa.String(length=200), nullable=True))
    if 'changes' not in have:
        op.add_column('imports', sa.Column('changes', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('imports', 'changes')
    op.drop_column('imports', 'app')
    op.drop_column('imports', 'kind')
