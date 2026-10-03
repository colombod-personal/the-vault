"""Import a collection file (Dragon Shield, Moxfield or generic CSV), and export the collection
in any format people can take elsewhere (``mtg_toolkits.formats``)."""

from __future__ import annotations

import csv
import math
from datetime import date

from mtg_toolkits import delta, formats
from mtg_toolkits import normalize_collector_number, normalize_set_code
from mtg_toolkits.models import CollectionEntry, Finish
from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.orm import Session

from .models import Card, Entry, Import, User
from .prices import MAX_PRICE, compute_values

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
EXACT = ("set_number", "id")
MAX_QUANTITY = 1_000_000  # copies of one row
MAX_COPIES = 2**31 - 1  # the collection's total, stored in INTEGER columns
# Column sizes (vault.models.Entry). Free text is clipped to fit; the printing's identity is
# never clipped (it would become another printing), so a value too long for those is refused.
CLIP = {"name": 300, "set_name": 200, "folder": 200}
REFUSE = {"set_code": 20, "collector_number": 30, "scryfall_id": 36, "language": 5}


class ImportError_(ValueError):
    pass


class ImportConflict(ImportError_):
    """Another import replaced the collection while this one ran."""


def user_entries(db: Session, user: User) -> list[Entry]:
    return list(db.scalars(select(Entry).where(Entry.user_id == user.id).order_by(Entry.position, Entry.id)))


def import_collection(db: Session, user: User, filename: str, content: bytes) -> Import:
    """Replace the user's collection with the file's contents.

    The format (Dragon Shield, Moxfield, generic CSV) is detected from the header. The file is
    treated as a full snapshot. The change against the previous collection is stored on the
    import (``summary``). Printings are matched to Scryfall right away where possible, so
    prices show immediately:
    - exact matches from the previous import are carried over
    - Scryfall ids in the file are used
    - set + collector number is looked up among the cards the server already knows
    Everything else is resolved by the next daily sync.
    """
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportError_("File is too large (20 MB max)")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportError_("File is not UTF-8 text") from exc
    try:
        source, entries = formats.parse(text)
    except (ValueError, TypeError, AttributeError, OverflowError, csv.Error) as exc:  # whatever the library raises
        raise ImportError_(f"No cards found. {exc}") from exc
    if not entries:
        raise ImportError_("No cards found in the file.")
    for row, e in enumerate(entries, 1):
        _clean(e, row)
    if sum(e.quantity for e in entries) > MAX_COPIES:
        raise ImportError_("The file has more copies than a collection can hold.")

    version = db.scalar(select(User.collection_version).where(User.id == user.id))
    old_rows = user_entries(db, user)
    changes = delta.diff([r.to_collection_entry() for r in old_rows], entries)

    known = {}
    for row in old_rows:
        if row.scryfall_id and row.match_method in EXACT:
            known[delta.key_of(row.to_collection_entry())] = (row.scryfall_id, row.match_method, row.price_finish)
    # Printings the server already knows, matched the way the library matches (set aliases, case,
    # leading zeros); queried by the number as written and as normalised, so the index is used.
    printing = lambda e: (normalize_set_code(e.set_code), normalize_collector_number(e.collector_number))  # noqa: E731
    wanted = sorted({(printing(e)[0], n) for e in entries if e.set_code and e.collector_number
                     for n in {e.collector_number.strip(), e.collector_number.strip().lower(), printing(e)[1]}})
    by_number: dict[tuple, str] = {}
    for i in range(0, len(wanted), 500):
        query = select(Card.scryfall_id, Card.set_code, Card.collector_number).where(
            tuple_(Card.set_code, Card.collector_number).in_(wanted[i:i + 500]))
        for sid, set_code, number in db.execute(query):
            by_number[(set_code, normalize_collector_number(number))] = sid

    imp = Import(
        user_id=user.id, filename=filename[:255], source=source, rows=len(entries),
        copies=sum(e.quantity for e in entries), summary=changes.summary(),
    )
    # Claim the collection: only one import replaces the version it read. Another import that
    # got there first (committed, or still running and holding the row) makes this one fail
    # instead of adding its cards to the other file's.
    claimed = db.execute(update(User).where(User.id == user.id, User.collection_version == version)
                         .values(collection_version=version + 1).execution_options(synchronize_session=False)).rowcount
    if not claimed:
        raise ImportConflict("Another import of this collection just finished. Reload and try again.")
    db.add(imp)
    db.flush()
    db.execute(delete(Entry).where(Entry.user_id == user.id))
    for position, e in enumerate(entries):
        scryfall_id, method, price_finish = known.get(delta.key_of(e), (None, None, None))
        if scryfall_id is None and e.scryfall_id:
            scryfall_id, method = e.scryfall_id, "id"
        elif scryfall_id is None and printing(e) in by_number:
            scryfall_id, method = by_number[printing(e)], "set_number"
        e.scryfall_id = scryfall_id
        db.add(Entry.from_collection_entry(
            e, user_id=user.id, import_id=imp.id, position=position, match_method=method,
            price_finish=price_finish,
        ))
    # Today's value right away, so the value-over-time chart starts with the first import (the
    # daily sync writes it again with that day's prices). Same transaction as the import, left
    # for the caller to commit (with the Idempotency-Key answer): all of it or nothing.
    db.flush()
    compute_values(db, date.today(), user.id, commit=False)
    return imp


def _price(value) -> float | None:
    """A price as a finite number in range, or None (NaN, infinity or junk: no price)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and abs(value) <= MAX_PRICE else None


def _count(value, row: int, what: str) -> int:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_QUANTITY:
        raise ImportError_(f"Row {row}: {what} must be a whole number from 0 to {MAX_QUANTITY:,}.")
    return value


def _clean(e: CollectionEntry, row: int) -> None:
    """Make a parsed entry safe to store, whatever the parser let through: counts in range,
    prices finite (or dropped), text that fits its column."""
    e.quantity = _count(e.quantity, row, "Quantity")
    e.trade_quantity = _count(e.trade_quantity or 0, row, "Trade quantity")
    e.purchase_price = _price(e.purchase_price)
    prices = e.source_prices if isinstance(e.source_prices, dict) else {}
    e.source_prices = {k: p for k, v in prices.items() if isinstance(k, str) and (p := _price(v)) is not None}
    extra = e.extra if isinstance(e.extra, dict) else {}  # text, as every format writes it (dict[str, str])
    e.extra = {k: v for k, v in extra.items() if isinstance(k, str) and isinstance(v, str)}
    if not isinstance(e.name, str) or not e.name.strip():
        raise ImportError_(f"Row {row}: the card has no name.")
    for column, size in CLIP.items():
        value = getattr(e, column)
        if value is not None and not isinstance(value, str):
            raise ImportError_(f"Row {row}: {column.replace('_', ' ')} is not text.")
        if value is not None and len(value) > size:
            setattr(e, column, value[:size])
    for column, size in REFUSE.items():
        value = getattr(e, column)
        if value is not None and (not isinstance(value, str) or len(value) > size):
            raise ImportError_(f"Row {row}: {column.replace('_', ' ')} must be text of at most {size} characters.")


def export_entries(db: Session, user: User, fmt: str) -> list[CollectionEntry]:
    """The collection as entries for ``fmt``.

    Dragon Shield gets the rows exactly as imported, so a Dragon Shield export comes back byte
    for byte. Other apps get:
    - Scryfall's set code and collector number for exactly matched printings (Dragon Shield's
      own codes, like ``GK2_ORZHOV``, mean nothing to Moxfield)
    - the finish the printing actually exists in
    - Scryfall ids, where the format carries them
    """
    rows = user_entries(db, user)
    entries = [r.to_collection_entry() for r in rows]
    if fmt == "dragonshield":
        return entries
    ids = {r.scryfall_id for r in rows if r.scryfall_id and r.match_method in EXACT}
    cards = {c.scryfall_id: c for c in db.scalars(select(Card).where(Card.scryfall_id.in_(ids)))} if ids else {}
    for row, e in zip(rows, entries):
        card = cards.get(row.scryfall_id) if row.match_method in EXACT else None
        if card is not None:
            e.set_code, e.collector_number, e.scryfall_id = card.set_code, card.collector_number, card.scryfall_id
            e.set_name = e.set_name or card.set_name
        else:
            # Not in the Vault's card table yet (e.g. before the first sync): an exact id is still
            # right and is kept; a name-only guess is not exported as if it were the printing.
            e.set_code = normalize_set_code(e.set_code)
            e.scryfall_id = row.scryfall_id if row.match_method in EXACT else None
        if row.price_finish:  # the printing exists only in this finish (e.g. blank Printing on etched-only cards)
            e.finish = Finish(row.price_finish)
    return entries


def export_collection(db: Session, user: User, fmt: str) -> str:
    return formats.FORMATS[fmt].dumps(export_entries(db, user, fmt))


def export_dragonshield(db: Session, user: User) -> str:
    return export_collection(db, user, "dragonshield")
