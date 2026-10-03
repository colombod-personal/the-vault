"""One accepted grant per owner, person and thing shared, enforced by the database.

Duplicate grants left by earlier concurrent acceptances are merged first: the newest grant
(the latest settings) stays, the older ones go.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01 06:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM shares WHERE grantee_id IS NOT NULL AND id NOT IN ("
        " SELECT max(id) FROM shares WHERE grantee_id IS NOT NULL"
        " GROUP BY owner_id, grantee_id, kind, coalesce(deck_id, 0))"))
    op.create_index('uq_shares_grant', 'shares',
                    ['owner_id', 'grantee_id', 'kind', sa.text('coalesce(deck_id, 0)')],
                    unique=True, if_not_exists=True)


def downgrade() -> None:
    op.drop_index('uq_shares_grant', table_name='shares')
