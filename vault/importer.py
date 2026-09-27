"""Import a Dragon Shield CSV export as the user's collection."""

from __future__ import annotations

from mtg_toolkits import delta, dragonshield
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Entry, Import, User

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class ImportError_(ValueError):
    pass


def user_entries(db: Session, user: User) -> list[Entry]:
    return list(db.scalars(select(Entry).where(Entry.user_id == user.id).order_by(Entry.position, Entry.id)))


def import_dragonshield(db: Session, user: User, filename: str, content: bytes) -> Import:
    """Replace the user's collection with the file's contents.

    The file is treated as a full snapshot, the way Dragon Shield exports it.
    The change against the previous collection is stored on the import
    (``summary``), and Scryfall matches from the previous import are carried
    over so prices show straight away.
    """
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImportError_("File is too large (20 MB max)")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportError_("File is not UTF-8 text") from exc
    entries = dragonshield.parse(text)
    if not entries:
        raise ImportError_("No cards found. Is this a Dragon Shield CSV export?")

    old_rows = user_entries(db, user)
    old = [r.to_collection_entry() for r in old_rows]
    changes = delta.diff(old, entries)

    # Carry over exact matches only; name-only guesses are re-resolved by the next sync.
    known = {}
    for row in old_rows:
        if row.scryfall_id and row.match_method in ("set_number", "id"):
            known[delta.key_of(row.to_collection_entry())] = (row.scryfall_id, row.match_method, row.price_finish)

    imp = Import(
        user_id=user.id, filename=filename[:255], rows=len(entries),
        copies=sum(e.quantity for e in entries), summary=changes.summary(),
    )
    db.add(imp)
    db.flush()
    db.execute(delete(Entry).where(Entry.user_id == user.id))
    for position, e in enumerate(entries):
        scryfall_id, method, price_finish = known.get(delta.key_of(e), (None, None, None))
        e.scryfall_id = scryfall_id
        db.add(Entry.from_collection_entry(
            e, user_id=user.id, import_id=imp.id, position=position, match_method=method,
            price_finish=price_finish,
        ))
    db.commit()
    return imp


def export_dragonshield(db: Session, user: User) -> str:
    return dragonshield.dumps(r.to_collection_entry() for r in user_entries(db, user))
