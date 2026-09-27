"""Import a collection file (Dragon Shield, Moxfield or generic CSV), and export the collection
in any format people can take elsewhere (``mtg_toolkits.formats``)."""

from __future__ import annotations

from mtg_toolkits import delta, formats
from mtg_toolkits.dragonshield import SET_ALIASES
from mtg_toolkits.models import CollectionEntry, Finish
from sqlalchemy import delete, select, tuple_
from sqlalchemy.orm import Session

from .models import Card, Entry, Import, User

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
EXACT = ("set_number", "id")


class ImportError_(ValueError):
    pass


def user_entries(db: Session, user: User) -> list[Entry]:
    return list(db.scalars(select(Entry).where(Entry.user_id == user.id).order_by(Entry.position, Entry.id)))


def scryfall_set(code: str | None) -> str:
    code = (code or "").lower()
    return SET_ALIASES.get(code, code)


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
    except ValueError as exc:
        raise ImportError_(f"No cards found. {exc}") from exc
    if not entries:
        raise ImportError_("No cards found in the file.")

    old_rows = user_entries(db, user)
    changes = delta.diff([r.to_collection_entry() for r in old_rows], entries)

    known = {}
    for row in old_rows:
        if row.scryfall_id and row.match_method in EXACT:
            known[delta.key_of(row.to_collection_entry())] = (row.scryfall_id, row.match_method, row.price_finish)
    wanted = sorted({(scryfall_set(e.set_code), e.collector_number) for e in entries if e.set_code and e.collector_number})
    by_number: dict[tuple, str] = {}
    for i in range(0, len(wanted), 500):
        query = select(Card.scryfall_id, Card.set_code, Card.collector_number).where(
            tuple_(Card.set_code, Card.collector_number).in_(wanted[i:i + 500]))
        for sid, set_code, number in db.execute(query):
            by_number[(set_code, number)] = sid

    imp = Import(
        user_id=user.id, filename=filename[:255], source=source, rows=len(entries),
        copies=sum(e.quantity for e in entries), summary=changes.summary(),
    )
    db.add(imp)
    db.flush()
    db.execute(delete(Entry).where(Entry.user_id == user.id))
    for position, e in enumerate(entries):
        scryfall_id, method, price_finish = known.get(delta.key_of(e), (None, None, None))
        if scryfall_id is None and e.scryfall_id:
            scryfall_id, method = e.scryfall_id, "id"
        elif scryfall_id is None and (scryfall_set(e.set_code), e.collector_number) in by_number:
            scryfall_id, method = by_number[(scryfall_set(e.set_code), e.collector_number)], "set_number"
        e.scryfall_id = scryfall_id
        db.add(Entry.from_collection_entry(
            e, user_id=user.id, import_id=imp.id, position=position, match_method=method,
            price_finish=price_finish,
        ))
    db.commit()
    return imp


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
            e.set_code, e.scryfall_id = scryfall_set(e.set_code) or None, None
        if row.price_finish:  # the printing exists only in this finish (e.g. blank Printing on etched-only cards)
            e.finish = Finish(row.price_finish)
    return entries


def export_collection(db: Session, user: User, fmt: str) -> str:
    return formats.FORMATS[fmt].dumps(export_entries(db, user, fmt))


def export_dragonshield(db: Session, user: User) -> str:
    return export_collection(db, user, "dragonshield")
