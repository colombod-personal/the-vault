"""The base of a re-import into one bucket, and the bucket a staged upload goes into (#124).

``bucket_baselines``: the last file imported into a bucket, as it was imported (the same record ``collection_baselines`` keeps for
a whole-collection import), one per bucket, deleted with the bucket. ``staged_uploads.bucket_id``: the bucket an upload goes into
(``bucket_bound``: the link was made for it, so the upload page cannot change it); ``ON DELETE CASCADE`` so a link whose bucket is gone is gone, never a whole-collection import. No data changes: every
existing import and upload keeps meaning the whole collection.

Revision ID: 0117
Revises: 0116
Create Date: 2026-10-09 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0117'
down_revision = '0116'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'bucket_baselines' in inspector.get_table_names():  # a create_all database has both
        return
    op.create_table(
        'bucket_baselines',
        sa.Column('bucket_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('import_id', sa.Integer(), nullable=True),
        sa.Column('cards', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['bucket_id'], ['buckets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['import_id'], ['imports.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('bucket_id'),
    )
    op.create_index(op.f('ix_bucket_baselines_user_id'), 'bucket_baselines', ['user_id'], unique=False)
    op.add_column('staged_uploads', sa.Column('bucket_id', sa.Integer(), nullable=True))
    op.add_column('staged_uploads', sa.Column('bucket_bound', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_foreign_key('fk_staged_uploads_bucket_id', 'staged_uploads', 'buckets', ['bucket_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint('fk_staged_uploads_bucket_id', 'staged_uploads', type_='foreignkey')
    op.drop_column('staged_uploads', 'bucket_bound')
    op.drop_column('staged_uploads', 'bucket_id')
    op.drop_index(op.f('ix_bucket_baselines_user_id'), table_name='bucket_baselines')
    op.drop_table('bucket_baselines')
