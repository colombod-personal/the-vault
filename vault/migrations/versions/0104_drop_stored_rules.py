"""The Comprehensive Rules are no longer stored: they are read live from Wizards of the Coast (vault/rules_live.py,
docs/rules-index.md). Drops the rules tables (never loaded in production).

Revision ID: 0104
Revises: 0103
Create Date: 2026-10-05 13:00:00
"""

from __future__ import annotations

from alembic import op

revision = '0104'
down_revision = '0103'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rules")
    op.execute("DROP TABLE IF EXISTS rules_versions")
    op.execute("DELETE FROM catalog_sources WHERE name = 'rules'")


def downgrade() -> None:
    pass  # nothing to restore: the rules are read live
