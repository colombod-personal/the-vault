"""Initial schema: every table as of the first release (the baseline for databases made by create_all).

Revision ID: 0001
Revises: 
Create Date: 2026-09-29 10:37:22.580207
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only the tables that don't exist yet: a database made by the old create_all is brought
    # up to this baseline without touching its data, then carries on with the later revisions.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if 'cards' not in existing:
        op.create_table('cards',
        sa.Column('scryfall_id', sa.String(length=36), nullable=False),
        sa.Column('oracle_id', sa.String(length=36), nullable=True),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('set_code', sa.String(length=20), nullable=False),
        sa.Column('set_name', sa.String(length=200), nullable=True),
        sa.Column('collector_number', sa.String(length=30), nullable=False),
        sa.Column('rarity', sa.String(length=20), nullable=True),
        sa.Column('type_line', sa.String(length=300), nullable=True),
        sa.Column('mana_cost', sa.String(length=100), nullable=True),
        sa.Column('cmc', sa.Float(), nullable=True),
        sa.Column('colors', sa.JSON(), nullable=False),
        sa.Column('color_identity', sa.JSON(), nullable=False),
        sa.Column('oracle_text', sa.Text(), nullable=True),
        sa.Column('finishes', sa.JSON(), nullable=False),
        sa.Column('image_small', sa.String(length=500), nullable=True),
        sa.Column('image_normal', sa.String(length=500), nullable=True),
        sa.Column('artist', sa.String(length=200), nullable=True),
        sa.Column('scryfall_uri', sa.String(length=500), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('scryfall_id')
        )
        with op.batch_alter_table('cards', schema=None) as batch_op:
            batch_op.create_index('ix_cards_set_number', ['set_code', 'collector_number'], unique=False)

    if 'price_snapshots' not in existing:
        op.create_table('price_snapshots',
        sa.Column('scryfall_id', sa.String(length=36), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('usd', sa.Float(), nullable=True),
        sa.Column('usd_foil', sa.Float(), nullable=True),
        sa.Column('usd_etched', sa.Float(), nullable=True),
        sa.Column('eur', sa.Float(), nullable=True),
        sa.Column('eur_foil', sa.Float(), nullable=True),
        sa.Column('eur_etched', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('scryfall_id', 'day')
        )

    if 'users' not in existing:
        op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=True),
        sa.Column('name', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )

    if 'access_tokens' not in existing:
        op.create_table('access_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('prefix', sa.String(length=20), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('scopes', sa.String(length=40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
        )
        with op.batch_alter_table('access_tokens', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_access_tokens_user_id'), ['user_id'], unique=False)

    if 'api_sessions' not in existing:
        op.create_table('api_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client', sa.String(length=40), nullable=False),
        sa.Column('device_name', sa.String(length=120), nullable=True),
        sa.Column('access_hash', sa.String(length=64), nullable=False),
        sa.Column('access_expires', sa.DateTime(timezone=True), nullable=False),
        sa.Column('refresh_hash', sa.String(length=64), nullable=False),
        sa.Column('refresh_expires', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('access_hash'),
        sa.UniqueConstraint('refresh_hash')
        )
        with op.batch_alter_table('api_sessions', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_api_sessions_user_id'), ['user_id'], unique=False)

    if 'auth_codes' not in existing:
        op.create_table('auth_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('code_challenge', sa.String(length=128), nullable=False),
        sa.Column('redirect_uri', sa.String(length=300), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code_hash')
        )
        with op.batch_alter_table('auth_codes', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_auth_codes_user_id'), ['user_id'], unique=False)

    if 'collection_values' not in existing:
        op.create_table('collection_values',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('market_usd', sa.Float(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('copies', sa.Integer(), nullable=False),
        sa.Column('priced_copies', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'day')
        )

    if 'decks' not in existing:
        op.create_table('decks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('decks', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_decks_user_id'), ['user_id'], unique=False)

    if 'idempotent_requests' not in existing:
        op.create_table('idempotent_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('endpoint', sa.String(length=120), nullable=False),
        sa.Column('status', sa.Integer(), nullable=False),
        sa.Column('body', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'key')
        )
        with op.batch_alter_table('idempotent_requests', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_idempotent_requests_created_at'), ['created_at'], unique=False)
            batch_op.create_index(batch_op.f('ix_idempotent_requests_user_id'), ['user_id'], unique=False)

    if 'identities' not in existing:
        op.create_table('identities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'subject')
        )
        with op.batch_alter_table('identities', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_identities_user_id'), ['user_id'], unique=False)

    if 'imports' not in existing:
        op.create_table('imports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('rows', sa.Integer(), nullable=False),
        sa.Column('copies', sa.Integer(), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('imports', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_imports_user_id'), ['user_id'], unique=False)

    if 'passkeys' not in existing:
        op.create_table('passkeys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('credential_id', sa.String(length=1400), nullable=False),
        sa.Column('public_key', sa.LargeBinary(), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False),
        sa.Column('transports', sa.JSON(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('aaguid', sa.String(length=36), nullable=True),
        sa.Column('backed_up', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('credential_id')
        )
        with op.batch_alter_table('passkeys', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_passkeys_user_id'), ['user_id'], unique=False)

    if 'entries' not in existing:
        op.create_table('entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('import_id', sa.Integer(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('set_code', sa.String(length=20), nullable=True),
        sa.Column('set_name', sa.String(length=200), nullable=True),
        sa.Column('collector_number', sa.String(length=30), nullable=True),
        sa.Column('finish', sa.String(length=10), nullable=False),
        sa.Column('condition', sa.String(length=20), nullable=False),
        sa.Column('language', sa.String(length=5), nullable=False),
        sa.Column('folder', sa.String(length=200), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('trade_quantity', sa.Integer(), nullable=False),
        sa.Column('purchase_price', sa.Float(), nullable=True),
        sa.Column('purchase_date', sa.Date(), nullable=True),
        sa.Column('source_prices', sa.JSON(), nullable=False),
        sa.Column('extra', sa.JSON(), nullable=False),
        sa.Column('scryfall_id', sa.String(length=36), nullable=True),
        sa.Column('match_method', sa.String(length=12), nullable=True),
        sa.Column('price_finish', sa.String(length=10), nullable=True),
        sa.ForeignKeyConstraint(['import_id'], ['imports.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('entries', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_entries_scryfall_id'), ['scryfall_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_entries_user_id'), ['user_id'], unique=False)

    if 'retired_refresh_tokens' not in existing:
        op.create_table('retired_refresh_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['api_sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
        )
        with op.batch_alter_table('retired_refresh_tokens', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_retired_refresh_tokens_session_id'), ['session_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_retired_refresh_tokens_user_id'), ['user_id'], unique=False)

    if 'shares' not in existing:
        op.create_table('shares',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('deck_id', sa.Integer(), nullable=True),
        sa.Column('grantee_id', sa.Integer(), nullable=True),
        sa.Column('show_costs', sa.Boolean(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['deck_id'], ['decks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['grantee_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
        )
        with op.batch_alter_table('shares', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_shares_grantee_id'), ['grantee_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_shares_owner_id'), ['owner_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('shares', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_shares_owner_id'))
        batch_op.drop_index(batch_op.f('ix_shares_grantee_id'))

    op.drop_table('shares')
    with op.batch_alter_table('retired_refresh_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_retired_refresh_tokens_user_id'))
        batch_op.drop_index(batch_op.f('ix_retired_refresh_tokens_session_id'))

    op.drop_table('retired_refresh_tokens')
    with op.batch_alter_table('entries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_entries_user_id'))
        batch_op.drop_index(batch_op.f('ix_entries_scryfall_id'))

    op.drop_table('entries')
    with op.batch_alter_table('passkeys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_passkeys_user_id'))

    op.drop_table('passkeys')
    with op.batch_alter_table('imports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_imports_user_id'))

    op.drop_table('imports')
    with op.batch_alter_table('identities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_identities_user_id'))

    op.drop_table('identities')
    with op.batch_alter_table('idempotent_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_idempotent_requests_user_id'))
        batch_op.drop_index(batch_op.f('ix_idempotent_requests_created_at'))

    op.drop_table('idempotent_requests')
    with op.batch_alter_table('decks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_decks_user_id'))

    op.drop_table('decks')
    op.drop_table('collection_values')
    with op.batch_alter_table('auth_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auth_codes_user_id'))

    op.drop_table('auth_codes')
    with op.batch_alter_table('api_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_sessions_user_id'))

    op.drop_table('api_sessions')
    with op.batch_alter_table('access_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_access_tokens_user_id'))

    op.drop_table('access_tokens')
    op.drop_table('users')
    op.drop_table('price_snapshots')
    with op.batch_alter_table('cards', schema=None) as batch_op:
        batch_op.drop_index('ix_cards_set_number')

    op.drop_table('cards')
