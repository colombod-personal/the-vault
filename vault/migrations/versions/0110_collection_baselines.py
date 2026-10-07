"""The last imported file's cards per person, the base of a re-import's three-way update (#194).

Collections imported before this have no stored base, so one is rebuilt from what the Vault recorded: the rows of the
collection now are the last file's, except the cards an assistant change set touched since (their copies before the
first change are in the change set's record). Those cards are compared by copies only (state ``null``). Without
this, the first re-import after the upgrade would still wipe the edits made through an assistant.

Revision ID: 0110
Revises: 0109
Create Date: 2026-10-07 10:00:00
"""

from __future__ import annotations

import json
from datetime import date

import sqlalchemy as sa
from alembic import op

revision = '0110'
down_revision = '0109'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if 'collection_baselines' in sa.inspect(bind).get_table_names():  # a create_all database has it
        return
    table = op.create_table(
        'collection_baselines',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('import_id', sa.Integer(), nullable=True),
        sa.Column('cards', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['import_id'], ['imports.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )
    rows = [_rebuilt(bind, user_id) for user_id in bind.execute(sa.text("SELECT DISTINCT user_id FROM entries")).scalars().all()]
    rows = [r for r in rows if r is not None]
    if rows:
        op.bulk_insert(table, rows)


def _rebuilt(bind, user_id: int) -> dict | None:
    from mtg_toolkits.models import CollectionEntry, Condition, Finish

    from vault import merge

    last = bind.execute(sa.text("SELECT id, created_at FROM imports WHERE user_id = :u AND kind = 'import' "
                                "ORDER BY id DESC LIMIT 1"), {"u": user_id}).first()
    if last is None:
        return None  # rows that no file import made (a test account): the next import starts the record
    entries = [CollectionEntry(
        name=r.name, quantity=r.quantity, set_code=r.set_code, set_name=r.set_name, collector_number=r.collector_number,
        finish=Finish(r.finish), condition=Condition(r.condition), language=r.language, folder=r.folder,
        trade_quantity=r.trade_quantity, purchase_price=r.purchase_price,
        purchase_date=r.purchase_date if isinstance(r.purchase_date, date) else None)
        for r in bind.execute(sa.text(
            "SELECT name, quantity, set_code, set_name, collector_number, finish, condition, language, folder, "
            "trade_quantity, purchase_price, purchase_date FROM entries WHERE user_id = :u"), {"u": user_id})]
    cards = merge.snapshot(entries)
    first_before: dict[str, tuple[CollectionEntry, int]] = {}
    for changes in bind.execute(sa.text("SELECT changes FROM imports WHERE user_id = :u AND id > :last "
                                        "AND kind IN ('assistant', 'undo') ORDER BY id"),
                                {"u": user_id, "last": last.id}).scalars():
        changes = json.loads(changes) if isinstance(changes, str) else changes
        for line in (changes or {}).get('lines', []):
            card = CollectionEntry(name=line['card'], quantity=0, set_code=line.get('set'),
                                   collector_number=line.get('number'), finish=Finish(line.get('finish') or 'nonfoil'))
            first_before.setdefault(merge.key_string(card), (card, int(line['before'])))
    for ks, (card, before) in first_before.items():  # touched since the last file: known by copies only
        cards[ks] = {"k": ks, "n": card.name, "s": card.set_code, "c": card.collector_number, "f": card.finish.value,
                     "q": before, "r": None}
    return {"user_id": user_id, "import_id": last.id, "cards": merge.stored(cards), "created_at": last.created_at}


def downgrade() -> None:
    op.drop_table('collection_baselines')
