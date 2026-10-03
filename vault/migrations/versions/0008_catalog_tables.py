"""Catalog tables: oracle cards, rulings, rules, tags, legality changes, prices, sources (docs/catalog-design.md).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04 00:49:02.125943
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'oracle_cards' in sa.inspect(op.get_bind()).get_table_names():
        return  # a database made by the old create_all already has the whole catalog
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')  # fuzzy card names (Neon and the test Postgres both allow it)
    op.create_table('catalog_sources',
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('version', sa.String(length=60), nullable=False),
    sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=True),
    sa.Column('url', sa.String(length=500), nullable=True),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('legality_changes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('oracle_id', sa.String(length=36), nullable=False),
    sa.Column('format', sa.String(length=30), nullable=False),
    sa.Column('old', sa.String(length=20), nullable=True),
    sa.Column('new', sa.String(length=20), nullable=True),
    sa.Column('observed_on', sa.Date(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_legality_changes_oracle_id', 'legality_changes', ['oracle_id'], unique=False)
    op.create_table('oracle_cards',
    sa.Column('oracle_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('layout', sa.String(length=40), nullable=True),
    sa.Column('mana_cost', sa.String(length=100), nullable=True),
    sa.Column('cmc', sa.Float(), nullable=True),
    sa.Column('type_line', sa.String(length=300), nullable=True),
    sa.Column('oracle_text', sa.Text(), nullable=True),
    sa.Column('power', sa.String(length=20), nullable=True),
    sa.Column('toughness', sa.String(length=20), nullable=True),
    sa.Column('loyalty', sa.String(length=20), nullable=True),
    sa.Column('defense', sa.String(length=20), nullable=True),
    sa.Column('colors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('color_identity', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('produced_mana', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('legalities', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('faces', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('game_changer', sa.Boolean(), nullable=True),
    sa.Column('edhrec_rank', sa.Integer(), nullable=True),
    sa.Column('released_at', sa.Date(), nullable=True),
    sa.Column('scryfall_uri', sa.String(length=500), nullable=True),
    sa.Column('representative_id', sa.String(length=36), nullable=True),
    sa.Column('digital', sa.Boolean(), nullable=False),
    sa.Column('content_hash', sa.String(length=40), nullable=False),
    sa.PrimaryKeyConstraint('oracle_id')
    )
    op.create_index('ix_oracle_cards_fts', 'oracle_cards', [sa.literal_column("to_tsvector('english', oracle_text)")], unique=False, postgresql_using='gin')
    op.create_index('ix_oracle_cards_name_lower', 'oracle_cards', [sa.literal_column('lower(name)')], unique=False)
    op.create_index('ix_oracle_cards_name_trgm', 'oracle_cards', ['name'], unique=False, postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})
    op.create_table('oracle_prices',
    sa.Column('oracle_id', sa.String(length=36), nullable=False),
    sa.Column('scryfall_id', sa.String(length=36), nullable=False),
    sa.Column('usd', sa.Float(), nullable=True),
    sa.Column('usd_foil', sa.Float(), nullable=True),
    sa.Column('eur', sa.Float(), nullable=True),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.PrimaryKeyConstraint('oracle_id')
    )
    op.create_table('oracle_tags',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('label', sa.String(length=160), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('parent_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('child_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('rules_versions',
    sa.Column('version', sa.String(length=10), nullable=False),
    sa.Column('effective_date', sa.Date(), nullable=False),
    sa.Column('source_url', sa.String(length=500), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('version')
    )
    op.create_table('rulings',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('oracle_id', sa.String(length=36), nullable=False),
    sa.Column('published_at', sa.Date(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('comment', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rulings_oracle_id'), 'rulings', ['oracle_id'], unique=False)
    op.create_table('oracle_tag_links',
    sa.Column('tag_id', sa.String(length=36), nullable=False),
    sa.Column('oracle_id', sa.String(length=36), nullable=False),
    sa.Column('weight', sa.String(length=12), nullable=True),
    sa.ForeignKeyConstraint(['tag_id'], ['oracle_tags.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('tag_id', 'oracle_id')
    )
    op.create_index('ix_oracle_tag_links_oracle_id', 'oracle_tag_links', ['oracle_id'], unique=False)
    op.create_table('rules',
    sa.Column('version', sa.String(length=10), nullable=False),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('parent', sa.String(length=20), nullable=True),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.ForeignKeyConstraint(['version'], ['rules_versions.version'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('version', 'number')
    )
    op.create_index('ix_rules_fts', 'rules', [sa.literal_column("to_tsvector('english', text)")], unique=False, postgresql_using='gin')
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_rules_fts', table_name='rules', postgresql_using='gin')
    op.drop_table('rules')
    op.drop_index('ix_oracle_tag_links_oracle_id', table_name='oracle_tag_links')
    op.drop_table('oracle_tag_links')
    op.drop_index(op.f('ix_rulings_oracle_id'), table_name='rulings')
    op.drop_table('rulings')
    op.drop_table('rules_versions')
    op.drop_table('oracle_tags')
    op.drop_table('oracle_prices')
    op.drop_index('ix_oracle_cards_name_trgm', table_name='oracle_cards', postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})
    op.drop_index('ix_oracle_cards_name_lower', table_name='oracle_cards')
    op.drop_index('ix_oracle_cards_fts', table_name='oracle_cards', postgresql_using='gin')
    op.drop_table('oracle_cards')
    op.drop_index('ix_legality_changes_oracle_id', table_name='legality_changes')
    op.drop_table('legality_changes')
    op.drop_table('catalog_sources')
    # ### end Alembic commands ###
