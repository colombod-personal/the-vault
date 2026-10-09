"""Import a collection file (Dragon Shield, Moxfield or generic CSV), and export the collection
in any format people can take elsewhere (``mtg_toolkits.formats``)."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from mtg_toolkits import delta, formats
from mtg_toolkits import normalize_collector_number, normalize_set_code
from mtg_toolkits.models import CollectionEntry, Finish
from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.orm import Session

from . import merge
from .models import Card, CollectionBaseline, Entry, Import, TagAssignment, User, utcnow
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


def user_entries(db: Session, user: User, bucket_id: int | None = None, tag: str | None = None) -> list[Entry]:
    """A person's entries in file order; with ``bucket_id``, only those in that bucket (#123); with ``tag``, only the copies of
    cards the person tagged so (the tag is on the card: every printing of it counts, #130)."""
    query = select(Entry).where(Entry.user_id == user.id).order_by(Entry.position, Entry.id)
    if bucket_id is not None:
        query = query.where(Entry.bucket_id == bucket_id)
    if tag is not None:
        query = query.where(Entry.scryfall_id.in_(
            select(Card.scryfall_id).join(TagAssignment, TagAssignment.oracle_id == Card.oracle_id)
            .where(TagAssignment.user_id == user.id, TagAssignment.tag == tag)))
    return list(db.scalars(query))


@dataclass(frozen=True)
class ImportOptions:
    """How a re-import answers the questions of its three-way update (vault.merge)."""
    replace_everything: bool = False  # the old behaviour: the file replaces the collection, Vault edits are discarded
    conflicts: str = merge.DEFAULT_ANSWER  # the answer for every card changed on both sides: "vault" or "app"
    use_app_value: frozenset = field(default_factory=frozenset)  # conflict ids answered "app" whatever `conflicts` says


@dataclass
class Prepared:
    """Everything an import (or its preview) works out before touching a row."""
    source: str
    entries: list[CollectionEntry]
    entry_keys: list[str]
    old_rows: list[Entry]
    row_keys: list[str]
    plan: merge.Plan
    baseline: dict | None  # who the base is: the import it came from
    theirs: dict
    result_rows: int
    changes: dict  # the summary of what the collection does (shaped like delta's)


def import_collection(db: Session, user: User, filename: str, content: bytes, options: ImportOptions | None = None) -> Import:
    """Bring the user's collection up to the file's contents, keeping what was changed in the Vault.

    The format (Dragon Shield, Moxfield, generic CSV) is detected from the header. The file is
    a full snapshot of the person's app. Compared with the last imported file, it says what changed in
    their app; only that is applied. Edits made in the Vault since (``vault.owned_changes``) stay, and a card
    changed on both sides is a conflict that keeps the Vault's edit unless ``options`` say otherwise
    (``vault.merge`` has the rules; ``options.replace_everything`` gives the old behaviour). The change to the
    collection is stored on the import (``summary``, and ``changes["merge"]``). Printings are matched to
    Scryfall right away where possible, so prices show immediately:
    - exact matches from the previous import are carried over
    - Scryfall ids in the file are used
    - set + collector number is looked up among the cards the server already knows
    Everything else is resolved by the next daily sync.
    """
    source, entries = read_file(content)
    version = db.scalar(select(User.collection_version).where(User.id == user.id))
    return _replace(db, user, filename, version, _prepare(db, user, source, entries, options or ImportOptions()))


def _baseline(db: Session, user: User) -> tuple[dict | None, dict | None]:
    row = db.get(CollectionBaseline, user.id)
    if row is None:
        return None, None
    source = db.get(Import, row.import_id) if row.import_id else None
    return merge.loaded(row.cards), {"import_id": row.import_id, "filename": source.filename if source else None,
                                     "imported_at": row.created_at.isoformat()}


def _prepare(db: Session, user: User, source: str, entries: list[CollectionEntry], options: ImportOptions) -> Prepared:
    old_rows = user_entries(db, user)
    old = [r.to_collection_entry() for r in old_rows]
    row_keys, entry_keys = [merge.key_string(e) for e in old], [merge.key_string(e) for e in entries]
    ours, theirs = merge.snapshot(old, row_keys), merge.snapshot(entries, entry_keys)
    base, info = _baseline(db, user)
    plan = merge.make_plan(base, theirs, ours, entries_exist=bool(old_rows), replace_everything=options.replace_everything,
                           default=options.conflicts, use_app=options.use_app_value)
    result_rows = sum(1 for ks in entry_keys if ks in plan.take) + sum(1 for ks in row_keys if ks in plan.keep)
    return Prepared(source, entries, entry_keys, old_rows, row_keys, plan, info, theirs, result_rows,
                    merge.summary({k: s["q"] for k, s in ours.items()}, plan.new_copies))


def read_file(content: bytes):
    """The file's format and entries, checked (size, encoding, limits). Raises ImportError_."""
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
    return source, entries


def preview_import(db: Session, user: User, content: bytes, options: ImportOptions | None = None) -> dict:
    """What importing the file would change, without changing anything (an assistant shows it first).

    ``changes`` is what happens to the collection; ``merge`` says how: what your app changed and is applied, which
    edits made in the Vault are kept, and which cards changed on both sides (with the answer each will get)."""
    source, entries = read_file(content)
    prep = _prepare(db, user, source, entries, options or ImportOptions())
    matches = _match_printings(db, entries, prep.old_rows)
    unmatched = [{"row": i, "name": e.name, "set": e.set_code, "number": e.collector_number, "quantity": e.quantity}
                 for i, (e, (sid, _, _)) in enumerate(zip(entries, matches), 1) if sid is None]
    return {"source": source, "rows": len(entries), "copies": sum(e.quantity for e in entries),
            "changes": prep.changes, "merge": merge.describe(prep.plan, prep.baseline),
            "matched_rows": len(entries) - len(unmatched),
            "unmatched_rows": len(unmatched), "unmatched": unmatched[:PREVIEW_UNMATCHED],
            "note": "row is the n-th card row of the file, not counting header lines. "
                    "`changes` is what happens to the collection; `merge` says what comes from the person's app, which "
                    "edits made in the Vault are kept and which cards changed on both sides (conflicts keep the "
                    "Vault's edit unless the person answers otherwise). "
                    "Unmatched rows have no printing the Vault can identify (missing or unknown set and number). "
                    "They are kept and matched later by name, which can pick the wrong printing and price. "
                    "Fix the set code and collector number in the file before importing if you can."}


PREVIEW_UNMATCHED = 100


def _match_printings(db: Session, entries, old_rows) -> list[tuple]:
    """Each entry's (scryfall_id, match_method, price_finish): carried over from the previous import, the id in the
    file, or set + collector number among the printings the server knows; (None, None, None) otherwise."""
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
    out = []
    for e in entries:
        scryfall_id, method, price_finish = known.get(delta.key_of(e), (None, None, None))
        if scryfall_id is None and e.scryfall_id:
            scryfall_id, method = e.scryfall_id, "id"
        elif scryfall_id is None and e.set_code and e.collector_number and printing(e) in by_number:
            scryfall_id, method = by_number[printing(e)], "set_number"
        out.append((scryfall_id, method, price_finish))
    return out


def _replace(db: Session, user: User, filename: str, version, prep: Prepared) -> Import:
    entries, plan = prep.entries, prep.plan
    matches = _match_printings(db, entries, prep.old_rows)
    imp = Import(
        user_id=user.id, filename=filename[:255], source=prep.source, rows=prep.result_rows,
        copies=sum(plan.new_copies.values()), summary=prep.changes,
        changes={"merge": merge.describe(plan, prep.baseline, limit=merge.STORED_LIMIT)},
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
    # The rows of the cards the file wins go; the Vault's edits stay (their rows are only moved into place).
    kept: dict[str, list[Entry]] = defaultdict(list)
    drop: list[int] = []
    for row, ks in zip(prep.old_rows, prep.row_keys):
        if ks in plan.keep:
            kept[ks].append(row)
        else:
            drop.append(row.id)
    if not kept:
        db.execute(delete(Entry).where(Entry.user_id == user.id).execution_options(synchronize_session=False))
    else:
        for i in range(0, len(drop), 5000):
            db.execute(delete(Entry).where(Entry.id.in_(drop[i:i + 5000])).execution_options(synchronize_session=False))
    position, placed = 0, set()
    for e, ks, (scryfall_id, method, price_finish) in zip(entries, prep.entry_keys, matches):
        if ks in plan.take:
            e.scryfall_id = scryfall_id
            db.add(Entry.from_collection_entry(
                e, user_id=user.id, import_id=imp.id, position=position, match_method=method,
                price_finish=price_finish,
            ))
            position += 1
        elif ks in kept and ks not in placed:  # an edited card stays where the file has it
            placed.add(ks)
            for row in kept[ks]:
                row.position, position = position, position + 1
    for ks, rows in kept.items():  # kept cards the file does not have (added in the Vault) go after the file's
        if ks not in placed:
            for row in rows:
                row.position, position = position, position + 1
    # The new file is the base of the next re-import, whatever was answered: what was kept stays kept (vault.merge).
    cards = merge.stored(prep.theirs)
    baseline = db.get(CollectionBaseline, user.id)
    if baseline is None:
        db.add(CollectionBaseline(user_id=user.id, import_id=imp.id, cards=cards))
    else:
        baseline.import_id, baseline.cards, baseline.created_at = imp.id, cards, utcnow()
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


def export_entries(db: Session, user: User, fmt: str, bucket_id: int | None = None) -> list[CollectionEntry]:
    """The collection as entries for ``fmt``.

    Dragon Shield gets the rows exactly as imported, so a Dragon Shield export comes back byte
    for byte. Other apps get:
    - Scryfall's set code and collector number for exactly matched printings (Dragon Shield's
      own codes, like ``GK2_ORZHOV``, mean nothing to Moxfield)
    - the finish the printing actually exists in
    - Scryfall ids, where the format carries them
    """
    rows = user_entries(db, user, bucket_id)
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


def export_collection(db: Session, user: User, fmt: str, bucket_id: int | None = None) -> str:
    return formats.FORMATS[fmt].dumps(export_entries(db, user, fmt, bucket_id))


def export_dragonshield(db: Session, user: User) -> str:
    return export_collection(db, user, "dragonshield")
