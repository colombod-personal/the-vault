"""Moving copies between buckets (#123, docs/collections.md).

An explicit move writes the target bucket's name to ``entries.folder`` of the moved rows (it is the only way an export's folder
changes) and is recorded as a change set (``imports.kind = "move"``), so history shows it and the next re-import's three-way
update sees the folder edit as the Vault's own. A stack can be split: moving 2 of 5 copies leaves 3 in the source and puts 2 in
the target, merged into an identical row there when there is one.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .collection_view import entry_group_id
from .models import Bucket, Entry, Import, User

COPY_FIELDS = ("name", "set_code", "set_name", "collector_number", "finish", "condition", "language", "purchase_price",
               "purchase_date", "source_prices", "extra", "scryfall_id", "match_method", "price_finish")
CONFIRM_ABOVE = 10  # copies: a bigger move is shown first and applied only with confirm


class MoveError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class Piece:
    entry: Entry
    take: int
    trade: int  # of those copies, how many are marked for trade


def plan(db: Session, user: User, source: Bucket, lines: list[tuple[str, int]]) -> tuple[list[Piece], dict[str, int]]:
    """What a move would take: the pieces of rows, in order, and the copies asked for per card id. Refuses an unknown card
    and a quantity larger than what the bucket holds."""
    asked: dict[str, int] = defaultdict(int)
    for card_id, quantity in lines:
        asked[card_id] += quantity
    rows = list(db.scalars(select(Entry).where(Entry.user_id == user.id, Entry.bucket_id == source.id, Entry.quantity > 0)
                           .order_by(Entry.id)))
    by_card: dict[str, list[Entry]] = defaultdict(list)
    for row in rows:
        by_card[entry_group_id(row)].append(row)
    pieces: list[Piece] = []
    for card_id, quantity in asked.items():
        held = by_card.get(card_id)
        if not held:
            raise MoveError(f"Card {card_id!r} is not in the bucket {source.name!r} (ids come from search_cards with bucket)", 404)
        have = sum(r.quantity for r in held)
        if quantity > have:
            raise MoveError(f"Asked to move {quantity} copies of {held[0].name} but the bucket {source.name!r} holds {have}")
        need = quantity
        for row in held:
            if need == 0:
                break
            take = min(row.quantity, need)
            # copies marked for trade stay behind unless they have to go: the plain ones move first
            plain = row.quantity - row.trade_quantity
            pieces.append(Piece(row, take, max(0, take - plain)))
            need -= take
    return pieces, dict(asked)


def apply(db: Session, user: User, source: Bucket, target: Bucket, pieces: list[Piece], label: str | None) -> Import:
    moved = sum(p.take for p in pieces)
    total = db.scalar(select(func.coalesce(func.sum(Entry.quantity), 0)).where(Entry.user_id == user.id)) or 0
    imp = Import(user_id=user.id, filename=f"Moved {moved} copies from {source.name} to {target.name}"[:255], source="move",
                 rows=len(pieces), copies=total, kind="move", app=(label or None) and label[:200])
    db.add(imp)
    db.flush()
    position = (db.scalar(select(func.max(Entry.position)).where(Entry.user_id == user.id)) or 0) + 1
    already = {_key(e): e for e in db.scalars(select(Entry).where(Entry.user_id == user.id, Entry.bucket_id == target.id,
                                                                    Entry.folder == target.name))}
    lines = []
    for piece in pieces:
        row = piece.entry
        probe = _key(row, folder=target.name)
        before = row.quantity
        if piece.take == row.quantity:  # the whole row moves
            joined = already.get(probe)
            if joined is not None and joined is not row:
                joined.quantity += row.quantity
                joined.trade_quantity += row.trade_quantity
                db.delete(row)
            else:
                row.bucket_id, row.folder = target.id, target.name
                already[probe] = row
        else:  # a split: the rest stays, the moved copies go to an identical row there, or a new one
            row.quantity -= piece.take
            row.trade_quantity -= piece.trade
            joined = already.get(probe)
            if joined is not None:
                joined.quantity += piece.take
                joined.trade_quantity += piece.trade
            else:
                fresh = Entry(user_id=user.id, import_id=imp.id, position=position, bucket_id=target.id, folder=target.name,
                              quantity=piece.take, trade_quantity=piece.trade, **{f: getattr(row, f) for f in COPY_FIELDS})
                db.add(fresh)
                already[probe] = fresh
                position += 1
        lines.append({"card": row.name, "set": row.set_code, "number": row.collector_number, "finish": row.finish,
                      "copies": piece.take, "from": source.name, "to": target.name, "row_before": before})
    imp.summary = {"added": 0, "removed": 0, "increased": 0, "decreased": 0, "unchanged": 0, "copies_in": 0, "copies_out": 0,
                   "moved": moved}
    imp.changes = {"lines": lines, "from": source.name, "to": target.name}
    db.execute(update(User).where(User.id == user.id).values(collection_version=User.collection_version + 1)
               .execution_options(synchronize_session=False))
    db.flush()
    return imp


def _key(row: Entry, folder: str | None = None) -> tuple:
    """Rows that may be merged: the same copy in every field that is not a quantity, in the same folder."""
    return (row.name, row.set_code, row.collector_number, row.finish, row.condition, row.language, row.purchase_price,
            row.purchase_date, row.scryfall_id, row.price_finish, folder if folder is not None else row.folder)
