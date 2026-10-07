"""Compact price_snapshots: integer cents, a native uuid key, and the column nothing read dropped (#63).

The row was a varchar(36) key, a date and six double-precision prices. It becomes a 16-byte uuid, a date and five integer
cents columns (``eur_etched`` is gone: no reader, no screen and no export used it). Measured on 300,000 real-shaped rows
(docs/catalog-design.md, "Price history"): about 40% fewer bytes a row. Order of the steps, their cost and how to go back are
in docs/catalog-design.md ("Migration plan for 0113").

One transaction (Alembic's), so a failure leaves the old table untouched:
 1. delete rows whose id is not a UUID (no Scryfall printing has such an id, so no reader could ever reach them);
 2. one ALTER TABLE that changes the key and every price column in a single rewrite of the table and its primary key index,
    turning a price into whole cents (and a price no import would accept, or NaN or infinity, into no price) and dropping eur_etched;
 3. rename the five price columns to ``*_cents`` (metadata only).

Revision ID: 0113
Revises: 0109
Create Date: 2026-10-07 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0113'
down_revision = '0109'
branch_labels = None
depends_on = None

MAX_PRICE = 10_000_000  # dollars per copy: vault.prices.MAX_PRICE
UUID = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
PRICES = ("usd", "usd_foil", "usd_etched", "eur", "eur_foil")


def _cents(col: str) -> str:
    return (f"CASE WHEN {col} IS NULL OR {col} = 'NaN'::float8 OR abs({col}) > {MAX_PRICE} THEN NULL "
            f"ELSE round(({col} * 100)::numeric)::integer END")


def upgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('price_snapshots')}
    if 'usd_cents' in have:
        return  # a database made by create_all of the current models already has the compact table
    op.execute(sa.text("DELETE FROM price_snapshots WHERE scryfall_id !~* :pattern").bindparams(pattern=UUID))
    changes = ["ALTER COLUMN scryfall_id TYPE uuid USING scryfall_id::uuid"]
    changes += [f"ALTER COLUMN {c} TYPE integer USING {_cents(c)}" for c in PRICES]
    changes.append("DROP COLUMN eur_etched")
    op.execute("ALTER TABLE price_snapshots " + ", ".join(changes))
    for c in PRICES:
        op.execute(f"ALTER TABLE price_snapshots RENAME COLUMN {c} TO {c}_cents")


def downgrade() -> None:
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('price_snapshots')}
    if 'usd_cents' not in have:
        return
    for c in PRICES:
        op.execute(f"ALTER TABLE price_snapshots RENAME COLUMN {c}_cents TO {c}")
    changes = ["ALTER COLUMN scryfall_id TYPE varchar(36) USING scryfall_id::text"]
    changes += [f"ALTER COLUMN {c} TYPE double precision USING {c} / 100.0" for c in PRICES]
    changes.append("ADD COLUMN eur_etched double precision")
    op.execute("ALTER TABLE price_snapshots " + ", ".join(changes))
