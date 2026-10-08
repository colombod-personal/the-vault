"""Buckets, tags and card annotations (#121, docs/collections.md, agreed 2026-10-08).

``buckets``: a named place copies live in. One per distinct Dragon Shield folder of each person (names compare
case-insensitively) and an "Unsorted" bucket for every person, for copies with no folder. ``entries.bucket_id`` is the Vault's
grouping of ``entries.folder`` (which stays as imported): every existing entry is given its bucket here, then the column is
made NOT NULL. ``tag_assignments`` and ``card_annotations`` are keyed on the card (oracle id) so they survive imports.
Nothing is deleted or merged: totals are the same before and after.

Revision ID: 0116
Revises: 0115
Create Date: 2026-10-08 19:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0116'
down_revision = '0115'
branch_labels = None
depends_on = None

METADATA_OK = ("coalesce(jsonb_typeof(vault_metadata) = 'object' AND jsonb_typeof(vault_metadata->'version') = 'number' "
               "AND (vault_metadata->>'version') ~ '^[1-9][0-9]{0,8}$' AND octet_length(vault_metadata::text) <= 8192, false)")
METADATA_DEFAULT = sa.text("""'{"version": 1}'::jsonb""")


def _metadata_column() -> sa.Column:
    return sa.Column('vault_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=METADATA_DEFAULT, nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    if 'buckets' in sa.inspect(bind).get_table_names():  # a create_all database has it
        return
    op.create_table(
        'buckets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        _metadata_column(),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(METADATA_OK, name='ck_buckets_vault_metadata'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_buckets_user_id'), 'buckets', ['user_id'], unique=False)
    op.create_index('uq_buckets_user_name', 'buckets', ['user_id', sa.literal_column('lower(name)')], unique=True)

    op.create_table(
        'tag_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('oracle_id', sa.String(length=36), nullable=False),
        sa.Column('tag', sa.String(length=40), nullable=False),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('source_detail', sa.String(length=200), nullable=True),
        _metadata_column(),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("tag ~ '^[a-z0-9:-]{1,40}$'", name='ck_tag_assignments_tag'),
        sa.CheckConstraint("source IN ('person', 'assistant', 'system')", name='ck_tag_assignments_source'),
        sa.CheckConstraint(METADATA_OK, name='ck_tag_assignments_vault_metadata'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'oracle_id', 'tag', name='uq_tag_assignments_card_tag'),
    )
    op.create_index(op.f('ix_tag_assignments_user_id'), 'tag_assignments', ['user_id'], unique=False)
    op.create_index('ix_tag_assignments_user_tag', 'tag_assignments', ['user_id', 'tag'], unique=False)

    op.create_table(
        'card_annotations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('oracle_id', sa.String(length=36), nullable=False),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('source_detail', sa.String(length=200), nullable=True),
        _metadata_column(),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('person', 'assistant', 'system')", name='ck_card_annotations_source'),
        sa.CheckConstraint(METADATA_OK, name='ck_card_annotations_vault_metadata'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'oracle_id', name='uq_card_annotations_card'),
    )
    op.create_index(op.f('ix_card_annotations_user_id'), 'card_annotations', ['user_id'], unique=False)

    op.add_column('entries', sa.Column('bucket_id', sa.Integer(), nullable=True))
    # Backfill: "Unsorted" for every person; then one bucket per distinct folder (the first spelling, case-insensitively),
    # numbered after it; then every entry gets the bucket of its folder. A folder that is blank, or "Unsorted" in any case,
    # is the Unsorted bucket. Nothing is merged or dropped: entries keep their folder.
    op.execute("INSERT INTO buckets (user_id, name, kind, position, created_at) "
               "SELECT id, 'Unsorted', 'default', 0, now() FROM users")
    op.execute("INSERT INTO buckets (user_id, name, kind, position, created_at) "
               "SELECT user_id, name, 'folder', row_number() OVER (PARTITION BY user_id ORDER BY lower(name)), now() FROM ("
               "  SELECT DISTINCT ON (user_id, lower(btrim(folder))) user_id, btrim(folder) AS name FROM entries "
               "  WHERE btrim(coalesce(folder, '')) <> '' AND lower(btrim(folder)) <> 'unsorted' "
               "  ORDER BY user_id, lower(btrim(folder)), id) first_spelling")
    op.execute("UPDATE entries SET bucket_id = buckets.id FROM buckets WHERE buckets.user_id = entries.user_id "
               "AND lower(buckets.name) = lower(coalesce(nullif(btrim(entries.folder), ''), 'Unsorted'))")
    op.alter_column('entries', 'bucket_id', nullable=False)
    op.create_foreign_key('fk_entries_bucket', 'entries', 'buckets', ['bucket_id'], ['id'], ondelete='RESTRICT')
    op.create_index(op.f('ix_entries_bucket_id'), 'entries', ['bucket_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_entries_bucket_id'), table_name='entries')
    op.drop_constraint('fk_entries_bucket', 'entries', type_='foreignkey')
    op.drop_column('entries', 'bucket_id')
    op.drop_table('card_annotations')
    op.drop_table('tag_assignments')
    op.drop_table('buckets')
