"""Reset the collection: the whole inventory or one bucket, with a preview, a backup and an undo (#129, docs/collections.md).

A reset is the same operation as an import with an empty target in replace mode for the selected scope (decision 6, "One import
path"): it runs through ``importer._replace`` with a plan made of no cards, so a card added only in the Vault (in neither the
baseline nor the empty file) is removed too, which a merge would have kept. What it adds to an import:

* a preview of what goes (rows, copies, printings, market value, how many of those were added in the Vault only, the tags and
  notes the cards leave behind) and a ``confirmation`` signed over exactly that, valid 15 minutes and for the collection version
  it was computed against, so what is applied is what was previewed;
* a snapshot of what the reset removes, kept in ``reset_snapshots`` for 7 days (one per person: the latest reset only) so that
  ``undo`` restores the same rows with the same ids, buckets, folders and baselines;
* an entry in the import history (``kind`` "reset", and "reset_undo" for the undo).

The tags and notes are the person's work: by default they stay (shown as not owned, decision 2); the reset clears them only when
asked. The buckets stay, empty.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.orm import Session

from . import importer, merge
from .collection_view import CollectionView
from .importer import ImportOptions
from .models import (Bucket, BucketBaseline, Card, CardAnnotation, CollectionBaseline, Entry, Import, ResetSnapshot, TagAssignment,
                     User, utcnow)
from .prices import compute_values

SNAPSHOT_DAYS = 7  # how long the undo lasts
MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024  # the snapshot's JSON, before compression; over it the reset needs no_undo
TOKEN_SECONDS = 15 * 60
V1 = "/api/v1"
ENTRY_COLUMNS = ("id", "import_id", "position", "name", "set_code", "set_name", "collector_number", "finish", "condition",
                 "language", "folder", "bucket_id", "quantity", "trade_quantity", "purchase_price", "purchase_date",
                 "source_prices", "extra", "scryfall_id", "match_method", "price_finish")
IMPORT_COLUMNS = ("id", "filename", "source", "rows", "copies", "summary", "created_at", "kind", "app", "changes")
TAG_COLUMNS = ("oracle_id", "tag", "source", "source_detail", "vault_metadata", "created_at")
NOTE_COLUMNS = ("oracle_id", "source", "source_detail", "vault_metadata", "created_at", "updated_at")


class ResetError(ValueError):
    """The reset (or its undo) cannot be done as asked. ``status`` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ResetOptions:
    bucket_id: int | None = None  # the bucket to reset; none: the whole inventory
    keep_tags: bool = True  # tags and notes stay (shown as not owned); False clears them
    keep_history: bool = True  # the import history stays; False clears it (the whole inventory only)
    no_undo: bool = False  # keep no snapshot: the reset cannot be undone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _scope_filter(user: User, bucket_id: int | None) -> list:
    return [Entry.user_id == user.id] + ([Entry.bucket_id == bucket_id] if bucket_id is not None else [])


def _oracles(user: User, *, here: bool, bucket_id: int):
    """The oracle ids of the cards owned in the bucket (``here``) or outside it."""
    cond = Entry.bucket_id == bucket_id if here else Entry.bucket_id != bucket_id
    return (select(Card.oracle_id).join(Entry, Entry.scryfall_id == Card.scryfall_id)
            .where(Entry.user_id == user.id, cond, Card.oracle_id.is_not(None)))


def _leaving(user: User, bucket_id: int | None, model) -> list:
    """The condition on a tag or note row: it is about a card the reset leaves not owned (all of them for the whole inventory)."""
    if bucket_id is None:
        return [model.user_id == user.id]
    return [model.user_id == user.id, model.oracle_id.in_(_oracles(user, here=True, bucket_id=bucket_id)),
            model.oracle_id.not_in(_oracles(user, here=False, bucket_id=bucket_id))]


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def preview(db: Session, user: User, options: ResetOptions, secret: str, *, now: float | None = None) -> dict:
    """What the reset would remove and keep, without changing anything; with a ``confirmation`` when it can be applied."""
    return _assess(db, user, options, secret, now)[0]


def _assess(db: Session, user: User, options: ResetOptions, secret: str, now: float | None):
    """The preview's answer, and what apply needs: the plan, the rows, and the state the confirmation is signed over."""
    scope = importer.scope_of(db, user, ImportOptions(bucket_id=options.bucket_id))  # NoSuchBucket: 404, also for another person's
    if scope is not None and not options.keep_history:
        raise ResetError("The import history belongs to the whole inventory: clearing it is only possible when resetting the "
                         "whole inventory (leave keep_history true for one bucket)", 422)
    bucket_id = scope.id if scope else None
    version = db.scalar(select(User.collection_version).where(User.id == user.id)) or 0
    prep = importer._prepare(db, user, "reset", [], ImportOptions(replace_everything=True, bucket_id=bucket_id), scope)
    rows = prep.old_rows
    copies = sum(r.quantity for r in rows)
    view = CollectionView(db, user, bucket_id=bucket_id).summary()
    ours = merge.snapshot([r.to_collection_entry() for r in rows], prep.row_keys)
    base, _ = importer._baseline(db, user, scope)
    if base is None:
        vault_only = {"known": False, "note": "The Vault holds no record of the file last imported for this part of the collection, "
                                              "so it cannot say which copies were added here only."}
    else:
        only = [s for k, s in ours.items() if k not in base]
        vault_only = {"known": True, "cards": len(only), "copies": sum(s["q"] for s in only),
                      "note": "Cards that are in no file you imported (added through an assistant, for example). They are "
                              "removed too, and an export or the undo is the only way back."}
    tags = db.scalars(select(TagAssignment.id).where(*_leaving(user, bucket_id, TagAssignment)).order_by(TagAssignment.id)).all()
    notes = db.scalars(select(CardAnnotation.id).where(*_leaving(user, bucket_id, CardAnnotation)).order_by(CardAnnotation.id)).all()
    tagged_cards = db.scalar(select(func.count(func.distinct(TagAssignment.oracle_id))).where(*_leaving(user, bucket_id, TagAssignment)))
    history = (db.scalar(select(func.count()).select_from(Import).where(Import.user_id == user.id)) or 0) if scope is None else 0
    clearing = not options.keep_tags
    others = importer.untouched(db, user, scope) if scope is not None else None
    effect = bool(rows) or (clearing and (tags or notes)) or (not options.keep_history and history)
    snapshot_bytes = raw_bytes = None
    refusal = None
    if not effect:
        refusal = "There is nothing to reset here: it holds no copies." + (" (No tags or notes to clear either.)" if clearing else "")
    elif not options.no_undo:
        raw, packed = _encode(_capture(db, user, scope, rows, options))
        snapshot_bytes, raw_bytes = len(packed), len(raw)
        if raw_bytes > MAX_SNAPSHOT_BYTES:
            refusal = (f"The undo snapshot of this reset would be {raw_bytes / 1048576:.1f} MB, over the {MAX_SNAPSHOT_BYTES // 1048576} MB "
                       f"limit. Download the export first ({_export_link(bucket_id)}), then reset again with no_undo true: that reset "
                       "cannot be undone.")
    state = _signed_state(rows, tags, notes, history)
    current = db.get(ResetSnapshot, user.id)
    out = {
        "applied": False,
        "scope": {"whole_inventory": scope is None, "bucket": {"id": scope.id, "name": scope.name} if scope else None},
        "removes": {"rows": len(rows), "copies": copies, "printings": view["printings"], "cards": view["cards"],
                    "market_value_usd": view["market_value"], "unmatched_rows": sum(1 for r in rows if r.scryfall_id is None),
                    "added_in_the_vault_only": vault_only,
                    "edited_in_the_vault": {"cards": max(0, prep.plan.discards - (vault_only.get("cards") or 0)) if base is not None else None}},
        "buckets": "Buckets stay, empty: they are your own structure." if scope is None else
                   f"The bucket {scope.name!r} stays, empty, with its name and notes.",
        "tags": {"keep": options.keep_tags, "cards_left_not_owned": tagged_cards, "tag_assignments": len(tags),
                 "removed": 0 if options.keep_tags else len(tags),
                 "note": "Tags and notes are yours and survive, shown as 'not owned', unless you ask to clear them."
                         if options.keep_tags else "Tags on these cards are removed."},
        "notes": {"keep": options.keep_tags, "cards": len(notes), "removed": 0 if options.keep_tags else len(notes)},
        "history": {"keep": options.keep_history, "entries": history, "removed": 0 if options.keep_history else history,
                    "note": "The reset is added to the import history as an entry of its own kind."},
        "backup": {"download": _export_link(bucket_id), "format": "Dragon Shield CSV", "rows": len(rows),
                   "note": "Offer this download before the reset: it is the file you can import again. The reset also keeps a "
                           "snapshot for the undo."},
        "undo": {"available": not options.no_undo and refusal is None, "days": SNAPSHOT_DAYS, "snapshot_bytes": snapshot_bytes,
                 "snapshot_json_bytes": raw_bytes, "limit_bytes": MAX_SNAPSHOT_BYTES,
                 "note": (f"The reset can be undone for {SNAPSHOT_DAYS} days, as long as nothing else changes the collection."
                          if not options.no_undo else "no_undo: nothing is kept, the reset cannot be undone."),
                 "replaces_earlier_snapshot": {"scope": current.summary.get("scope"), "created_at": _iso(current.created_at)}
                 if current is not None and not options.no_undo else None},
        "collection_version": version,
        "ready": refusal is None,
        "note": "Nothing has changed yet. Show this to the person, offer the download, and apply it only after they typed or said "
                "that they want to reset (RESET on the web): send it again with this confirmation.",
    }
    if others is not None:
        out["leaves"] = others
    if refusal:
        out["refused"] = refusal
    else:
        expires = int((now or time.time()) + TOKEN_SECONDS)
        out["confirmation"] = _token(secret, user, options, version, state, expires)
        out["expires_in_seconds"] = TOKEN_SECONDS
    return out, prep, rows, state


def _export_link(bucket_id: int | None) -> str:
    return f"{V1}/collection/export.csv" + (f"?bucket={bucket_id}" if bucket_id is not None else "")


def _signed_state(rows, tags, notes, history) -> dict:
    return {"rows": _digest([(r.id, r.quantity, r.trade_quantity) for r in rows]), "tags": _digest(list(tags)),
            "notes": _digest(list(notes)), "history": history}


def _token(secret: str, user: User, options: ResetOptions, version: int, state: dict, expires: int) -> str:
    body = json.dumps({"u": user.id, "b": options.bucket_id, "kt": options.keep_tags, "kh": options.keep_history,
                       "nu": options.no_undo, "v": version, "s": state, "x": expires}, sort_keys=True, separators=(",", ":"))
    sig = hmac.new(secret.encode(), b"collection-reset:" + body.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{expires}.{sig}".encode()).decode()


# -- the snapshot ---------------------------------------------------------------------------------------------------------

def _row(obj, columns) -> list:
    out = []
    for c in columns:
        v = getattr(obj, c)
        out.append(v.isoformat() if isinstance(v, (date, datetime)) else v)
    return out


def _capture(db: Session, user: User, scope: Bucket | None, rows: list[Entry], options: ResetOptions) -> dict:
    """Everything the reset removes or replaces, as a JSON-ready dict (see ``restore``)."""
    bucket_id = scope.id if scope else None
    payload: dict = {
        "v": 1, "bucket_id": bucket_id,
        "entry_columns": list(ENTRY_COLUMNS), "entries": [_row(r, ENTRY_COLUMNS) for r in rows],
        "collection_baseline": None, "bucket_baselines": [],
    }
    if scope is None:
        b = db.get(CollectionBaseline, user.id)
        payload["collection_baseline"] = None if b is None else {"import_id": b.import_id, "cards": b.cards, "created_at": _iso(b.created_at)}
        payload["bucket_baselines"] = [{"bucket_id": x.bucket_id, "import_id": x.import_id, "cards": x.cards, "created_at": _iso(x.created_at)}
                                       for x in db.scalars(select(BucketBaseline).where(BucketBaseline.user_id == user.id))]
    else:
        x = db.get(BucketBaseline, scope.id)
        payload["bucket_baselines"] = [] if x is None else [{"bucket_id": x.bucket_id, "import_id": x.import_id, "cards": x.cards,
                                                              "created_at": _iso(x.created_at)}]
    if not options.keep_tags:
        bucket = scope.id if scope else None
        payload["tag_columns"], payload["note_columns"] = list(TAG_COLUMNS), list(NOTE_COLUMNS)
        payload["tags"] = [_row(t, TAG_COLUMNS) for t in db.scalars(select(TagAssignment).where(*_leaving(user, bucket, TagAssignment)))]
        payload["notes"] = [_row(n, NOTE_COLUMNS) for n in db.scalars(select(CardAnnotation).where(*_leaving(user, bucket, CardAnnotation)))]
    if not options.keep_history:
        payload["import_columns"] = list(IMPORT_COLUMNS)
        payload["imports"] = [_row(i, IMPORT_COLUMNS) for i in db.scalars(select(Import).where(Import.user_id == user.id).order_by(Import.id))]
    return payload


def _encode(payload: dict) -> tuple[bytes, bytes]:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return raw, zlib.compress(raw, 6)


# -- apply -----------------------------------------------------------------------------------------------------------------

def apply(db: Session, user: User, options: ResetOptions, confirmation: str, secret: str, label: str | None,
          *, now: float | None = None) -> dict:
    try:
        expires_s, _ = base64.urlsafe_b64decode(confirmation.encode()).decode().split(".", 1)
        expires = int(expires_s)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ResetError("That confirmation is not valid: preview again", 400) from exc
    if expires < (now or time.time()):
        raise ResetError("That confirmation has expired: preview again and ask the person")
    seen, prep, rows, state = _assess(db, user, options, secret, now)
    if not seen["ready"]:
        raise ResetError(seen.get("refused") or "This reset is not ready to apply: preview again")
    expected = _token(secret, user, options, seen["collection_version"], state, expires)
    if not hmac.compare_digest(expected.encode(), confirmation.encode()):
        raise ResetError("This is not the reset the person saw, or the collection changed since: preview again")
    scope = prep.scope
    payload = None
    if not options.no_undo:
        raw, packed = _encode(_capture(db, user, scope, rows, options))
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise ResetError("The undo snapshot is over the size limit: reset again with no_undo true after downloading the export")
        payload = (raw, packed)
    name = f"Reset: {scope.name}" if scope else "Reset: the whole collection"
    version = seen["collection_version"]
    tags_removed = notes_removed = history_removed = 0
    if not options.keep_tags:
        # before the rows go: the cards that leave the inventory are found from them (a bucket reset must not clear the tags of a
        # card owned elsewhere). All of it is one transaction: a conflict below undoes this too.
        bucket = scope.id if scope else None
        tags_removed = db.execute(delete(TagAssignment).where(*_leaving(user, bucket, TagAssignment))
                                  .execution_options(synchronize_session=False)).rowcount or 0
        notes_removed = db.execute(delete(CardAnnotation).where(*_leaving(user, bucket, CardAnnotation))
                                   .execution_options(synchronize_session=False)).rowcount or 0
    imp = importer._replace(db, user, name, version, prep)  # raises ImportConflict if another import got in first; claims the version
    imp.kind, imp.source, imp.app = "reset", "reset", (label or None) and label[:200]
    removed = seen["removes"]
    if not options.keep_history:
        history_removed = db.execute(delete(Import).where(Import.user_id == user.id, Import.id != imp.id)
                                     .execution_options(synchronize_session=False)).rowcount or 0
    summary = {"scope": scope.name if scope else "the whole inventory", "bucket_id": scope.id if scope else None,
               "rows": removed["rows"], "copies": removed["copies"], "printings": removed["printings"],
               "market_value_usd": removed["market_value_usd"], "tags_removed": tags_removed, "notes_removed": notes_removed,
               "history_entries_removed": history_removed, "keep_tags": options.keep_tags, "keep_history": options.keep_history}
    imp.changes = {**(imp.changes or {}), "reset": {**summary, "undoable": not options.no_undo}}
    db.execute(delete(ResetSnapshot).where(ResetSnapshot.user_id == user.id).execution_options(synchronize_session=False))
    snapshot = None
    if payload is not None:
        snapshot = ResetSnapshot(user_id=user.id, import_id=imp.id, bucket_id=scope.id if scope else None, version_after=version + 1,
                                 summary=summary, payload=payload[1], raw_bytes=len(payload[0]), created_at=_now(),
                                 expires_at=_now() + timedelta(days=SNAPSHOT_DAYS))
        db.add(snapshot)
    db.flush()
    return {"applied": True, "reset": imp.id, "scope": seen["scope"], "removed": summary,
            "undo": ({"available": True, "until": _iso(snapshot.expires_at), "days": SNAPSHOT_DAYS,
                      "note": "POST /collection/reset/undo restores it, as long as nothing else changes the collection."}
                     if snapshot else {"available": False, "note": "No snapshot was kept (no_undo)."}),
            "backup": seen["backup"], "_links": {"import": {"href": f"{V1}/imports/{imp.id}"}}}


# -- the snapshot, read; the undo -------------------------------------------------------------------------------------------

def current(db: Session, user: User) -> ResetSnapshot | None:
    """The person's undo snapshot, or None when there is none or its 7 days have passed."""
    snap = db.get(ResetSnapshot, user.id)
    return snap if snap is not None and snap.expires_at > _now() else None


def _load(snap: ResetSnapshot) -> dict:
    return json.loads(zlib.decompress(snap.payload))


def blocker(db: Session, user: User, snap: ResetSnapshot, payload: dict | None = None) -> str | None:
    """Why the snapshot cannot be restored now, in words; None when it can. The rule: the collection must be exactly as the reset
    left it (nothing changed it since: the version is the reset's), the scope must still be empty, and every bucket the copies
    lived in must still exist. Anything else makes the restore ambiguous, so it is refused instead of guessed."""
    version = db.scalar(select(User.collection_version).where(User.id == user.id))
    if version != snap.version_after:
        return ("The collection changed since the reset (an import, an edit made through an assistant, or a move of copies), so "
                "restoring the removed copies would be ambiguous. Import the export you downloaded instead.")
    payload = payload or _load(snap)
    bucket_id = payload["bucket_id"]
    if db.scalar(select(func.count()).select_from(Entry).where(*_scope_filter(user, bucket_id))):
        return "There are copies in the reset scope now, so the removed ones cannot be put back without mixing them."
    wanted = {row[ENTRY_COLUMNS.index("bucket_id")] for row in payload["entries"]} | ({bucket_id} if bucket_id else set())
    have = set(db.scalars(select(Bucket.id).where(Bucket.user_id == user.id, Bucket.id.in_(wanted)))) if wanted else set()
    if wanted - have:
        return "A bucket the copies lived in has been deleted since the reset, so they cannot be put back where they were."
    return None


def summary_of(db: Session, user: User) -> dict | None:
    snap = current(db, user)
    if snap is None:
        return None
    reason = blocker(db, user, snap)
    return {"scope": snap.summary.get("scope"), "bucket_id": snap.bucket_id, "reset": snap.import_id, "removed": snap.summary,
            "created_at": _iso(snap.created_at), "expires_at": _iso(snap.expires_at), "snapshot_bytes": len(snap.payload),
            "snapshot_json_bytes": snap.raw_bytes, "can_undo": reason is None, "why_not": reason,
            "_links": {"undo": {"href": f"{V1}/collection/reset/undo"}, "self": {"href": f"{V1}/collection/reset"}}}


def undo(db: Session, user: User, *, confirm: bool, label: str | None = None) -> dict:
    """Without ``confirm``: what the undo would put back (and whether it can). With it: restore, in one step with the version
    claim, and record it in the import history."""
    snap = current(db, user)
    if snap is None:
        raise ResetError("There is no reset to undo: the snapshot is kept for 7 days after a reset, once", 404)
    payload = _load(snap)
    reason = blocker(db, user, snap, payload)
    rows = payload["entries"]
    copies = sum(r[ENTRY_COLUMNS.index("quantity")] for r in rows)
    shown = {"applied": False, "restores": {"rows": len(rows), "copies": copies, "scope": snap.summary.get("scope"),
                                            "tags": len(payload.get("tags", [])), "notes": len(payload.get("notes", [])),
                                            "history_entries": len(payload.get("imports", []))},
             "expires_at": _iso(snap.expires_at), "can_undo": reason is None, "why_not": reason}
    if reason is not None:
        raise ResetError(reason)
    if not confirm:
        return {**shown, "note": "Nothing has changed yet. Send it again with confirm true to put these copies back."}
    reset_id, made, version_after = snap.import_id, dict(snap.summary), snap.version_after  # read now: the row is deleted below
    claimed = db.execute(update(User).where(User.id == user.id, User.collection_version == version_after)
                         .values(collection_version=version_after + 1).execution_options(synchronize_session=False)).rowcount
    if not claimed:
        raise ResetError("The collection changed meanwhile: nothing was restored")
    bucket_id = payload["bucket_id"]
    scope = db.get(Bucket, bucket_id) if bucket_id else None
    restored_imports = _restore_imports(db, user, payload)
    existing_imports = set(db.scalars(select(Import.id).where(Import.user_id == user.id)))
    reset_import = db.get(Import, reset_id) if reset_id else None
    undo_import = Import(user_id=user.id, filename=f"Undo of the reset of {made.get('scope')}"[:255], source="reset",
                         rows=len(rows), copies=copies, kind="reset_undo", app=(label or None) and label[:200],
                         summary={"added": 0, "removed": 0, "increased": 0, "decreased": 0, "unchanged": 0, "copies_in": copies,
                                  "copies_out": 0, "reset_undone": True},
                         changes={"reset": {**made, "undoes": reset_id},
                                  **({"bucket": {"id": scope.id, "name": scope.name}} if scope else {})})
    db.add(undo_import)
    db.flush()
    existing_imports |= {undo_import.id} | restored_imports
    cols = payload["entry_columns"]
    for values in rows:
        data = dict(zip(cols, values))
        if data["import_id"] not in existing_imports:
            data["import_id"] = None
        if data["purchase_date"]:
            data["purchase_date"] = date.fromisoformat(data["purchase_date"])
        db.add(Entry(user_id=user.id, **data))
    _restore_baselines(db, user, payload, scope)
    for key, model, columns in (("tags", TagAssignment, "tag_columns"), ("notes", CardAnnotation, "note_columns")):
        _restore_marks(db, user, model, payload.get(columns), payload.get(key, []))
    if reset_import is not None:
        reset_import.changes = {**(reset_import.changes or {}), "undone_by": undo_import.id}
    db.execute(delete(ResetSnapshot).where(ResetSnapshot.user_id == user.id).execution_options(synchronize_session=False))
    db.flush()
    compute_values(db, date.today(), user.id, commit=False)
    return {**shown, "applied": True, "undone": reset_id, "change_set": undo_import.id,
            "_links": {"import": {"href": f"{V1}/imports/{undo_import.id}"}}}


def _restore_imports(db: Session, user: User, payload: dict) -> set[int]:
    done: set[int] = set()
    cols = payload.get("import_columns")
    for values in payload.get("imports", []):
        data = dict(zip(cols, values))
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        db.add(Import(user_id=user.id, **data))
        done.add(data["id"])
    db.flush()
    return done


def _restore_baselines(db: Session, user: User, payload: dict, scope: Bucket | None) -> None:
    def when(text):
        return datetime.fromisoformat(text) if text else utcnow()

    def known(import_id):
        return import_id if import_id and db.get(Import, import_id) is not None else None

    if scope is None:
        db.execute(delete(CollectionBaseline).where(CollectionBaseline.user_id == user.id).execution_options(synchronize_session=False))
        db.execute(delete(BucketBaseline).where(BucketBaseline.user_id == user.id).execution_options(synchronize_session=False))
        db.expire_all()
        b = payload["collection_baseline"]
        if b is not None:
            db.add(CollectionBaseline(user_id=user.id, import_id=known(b["import_id"]), cards=b["cards"], created_at=when(b["created_at"])))
    else:
        db.execute(delete(BucketBaseline).where(BucketBaseline.bucket_id == scope.id).execution_options(synchronize_session=False))
        db.expire_all()
    for x in payload["bucket_baselines"]:
        if db.get(Bucket, x["bucket_id"]) is not None:
            db.add(BucketBaseline(bucket_id=x["bucket_id"], user_id=user.id, import_id=known(x["import_id"]), cards=x["cards"],
                                  created_at=when(x["created_at"])))


def _restore_marks(db: Session, user: User, model, columns, rows) -> None:
    """Tags or notes the reset cleared, put back unless the person has made one of the same key since (theirs stays)."""
    for values in rows:
        data = dict(zip(columns, values))
        for k in ("created_at", "updated_at"):
            if data.get(k):
                data[k] = datetime.fromisoformat(data[k])
        key = [model.user_id == user.id, model.oracle_id == data["oracle_id"]] + ([model.tag == data["tag"]] if "tag" in data else [])
        if db.scalar(select(model.id).where(*key)) is None:
            db.add(model(user_id=user.id, **data))
