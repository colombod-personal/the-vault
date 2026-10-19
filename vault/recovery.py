"""Recoverable removals: what is deleted is kept for 30 days and can be put back (vault-wxy.18).

Why: an assistant that holds the Write permission can delete a deck with one call that carries ``confirm: true``, and the server cannot
tell that from the person's yes. Instead of trying to prove a human, the removal is made recoverable. Each removal writes a
``recovery_items`` row in the **same transaction** as the delete (both happen or neither), the person sees it in the web app (Account,
Recently deleted) and an assistant can list and restore it with ``list_recently_deleted`` and ``restore_deleted``.

* **Window**: 30 days from the removal (``DAYS``). ``restore`` refuses an item past ``expires_at`` even while its row still exists; the
  daily retention job (``prune_expired``) deletes expired rows, and listing or storing deletes the caller's own expired rows too, so a
  failed job never stretches the window. Account erasure removes the rows (``ON DELETE CASCADE`` and ``privacy.personal_data``).
* **Space**: at most ``MAX_ITEMS`` items and ``MAX_BYTES`` stored bytes a person. When full, a removal made by an assistant (an OAuth
  app or a personal access token) is refused with 409 and nothing is deleted: a loop of removals can never push the earlier,
  good items out. The person in the web app is not blocked: the oldest items make room (the owner's own hands).
* **Who may do what**: restore needs the write scope (a tool can do it); removing an item for good is the person's alone
  (``account_user``: no token has the ``account`` scope). A deck's shares are not reopened by a restore: it never widens who can see.

Only decks today (``kind`` "deck"); the table's kinds are ready for the copies an import replaced, a tag and a share.
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import Deck, DeckVersion, RecoveryItem, Share, User

DAYS = 30
MAX_ITEMS = 200
MAX_BYTES = 8 * 1024 * 1024  # stored (compressed) bytes a person may keep
MAX_ITEM_JSON_BYTES = 20 * 1024 * 1024
FORMAT = 1  # the payload's ``v``; a reader refuses any other


class RecoveryError(ValueError):
    """The removal cannot be kept or the item cannot be put back. ``status`` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _when(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _pack(payload: dict) -> tuple[int, bytes]:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return len(raw), zlib.compress(raw, 6)


def _unpack(item: RecoveryItem) -> dict:
    try:
        payload = json.loads(zlib.decompress(item.payload))
        if not isinstance(payload, dict) or payload.get("v") != FORMAT:
            raise ValueError("unknown format")
        return payload
    except (zlib.error, ValueError, TypeError, MemoryError, RecursionError):
        raise RecoveryError("This item cannot be read any more, so it cannot be put back. It is deleted when its 30 days are over.",
                            409) from None


def drop_expired(db: Session, user: User) -> int:
    """The caller's own expired items, deleted now (the daily job does everyone's)."""
    return db.execute(delete(RecoveryItem).where(RecoveryItem.user_id == user.id, RecoveryItem.expires_at <= _now())
                      .execution_options(synchronize_session=False)).rowcount or 0


def prune_expired(db: Session) -> int:
    """Every person's expired items: the retention job's part (the 30 days stated in the privacy notice)."""
    return db.execute(delete(RecoveryItem).where(RecoveryItem.expires_at <= _now())
                      .execution_options(synchronize_session=False)).rowcount or 0


def keep(db: Session, user: User, kind: str, label: str, summary: dict, payload: dict, *, assistant: str | None) -> RecoveryItem:
    """Store a removal that is about to happen, in the caller's transaction. ``assistant`` is the app's name when an assistant is
    removing it (it is then refused, not given room, when the person's space is full); None for the person.
    Raises ``RecoveryError`` (409) when it cannot be kept: the caller must not remove anything then."""
    # one removal at a time per person, so two at once cannot both pass the space check
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    drop_expired(db, user)
    raw_len, packed = _pack({"v": FORMAT, **payload})
    if raw_len > MAX_ITEM_JSON_BYTES or len(packed) > MAX_BYTES:
        raise RecoveryError("This is too big to keep for the 30-day recovery, so it was not removed.", 413)
    count, total = db.execute(select(func.count(), func.coalesce(func.sum(RecoveryItem.stored_bytes), 0))
                              .where(RecoveryItem.user_id == user.id)).one()
    while count >= MAX_ITEMS or total + len(packed) > MAX_BYTES:
        if assistant is not None or count == 0:
            raise RecoveryError(
                "Nothing was deleted: the 30-day recovery space for this account is full, and an app may not make room by "
                "discarding earlier items. The person can empty it in the Vault web app (Account, Recently deleted).", 409)
        oldest = db.scalar(select(RecoveryItem).where(RecoveryItem.user_id == user.id).order_by(RecoveryItem.id).limit(1))
        total -= oldest.stored_bytes
        count -= 1
        db.delete(oldest)
    now = _now()
    item = RecoveryItem(user_id=user.id, kind=kind, label=label[:200], summary=summary, payload=packed, raw_bytes=raw_len,
                        stored_bytes=len(packed), source="assistant" if assistant is not None else "person",
                        source_detail=assistant, created_at=now, expires_at=now + timedelta(days=DAYS))
    db.add(item)
    db.flush()
    return item


def listing(db: Session, user: User) -> list[RecoveryItem]:
    """The caller's items that can still be put back, newest first. The caller commits (this deletes the expired ones)."""
    drop_expired(db, user)
    return list(db.scalars(select(RecoveryItem).where(RecoveryItem.user_id == user.id).order_by(RecoveryItem.id.desc())))


def describe(item: RecoveryItem) -> dict:
    return {"id": item.id, "kind": item.kind, "name": item.label, "summary": item.summary,
            "deleted_by": {"who": item.source, "app": item.source_detail}, "deleted_at": _iso(item.created_at),
            "restorable_until": _iso(item.expires_at),
            "restore": "restore_deleted with this item_id (tool), or Restore under Account, Recently deleted (web app)"}


def _item(db: Session, user: User, item_id: int, *, lock: bool = False) -> RecoveryItem:
    query = select(RecoveryItem).where(RecoveryItem.id == item_id, RecoveryItem.user_id == user.id)  # another person's id is a 404
    item = db.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise RecoveryError("Nothing to restore with that id: it may already be restored, removed for good, or past its 30 days.", 404)
    if item.expires_at <= _now():
        raise RecoveryError(f"Its {DAYS} days are over, so it can no longer be restored.", 404)
    return item


def purge(db: Session, user: User, item_id: int) -> None:
    """Remove one item for good (the person's own act: the route needs a sign-in that may manage the account)."""
    db.delete(_item_any(db, user, item_id))


def _item_any(db: Session, user: User, item_id: int) -> RecoveryItem:
    item = db.scalar(select(RecoveryItem).where(RecoveryItem.id == item_id, RecoveryItem.user_id == user.id))
    if item is None:
        raise RecoveryError("Not found", 404)
    return item


# -- decks ----------------------------------------------------------------------------------------------------------------------

def keep_deck(db: Session, user: User, deck: Deck, *, assistant: str | None) -> RecoveryItem:
    """Keep the deck, its saved versions and the count of its shares, before the caller deletes it. Shares are counted, not kept:
    a restore never reopens access, so no invite token or grantee is stored."""
    versions = list(db.scalars(select(DeckVersion).where(DeckVersion.deck_id == deck.id).order_by(DeckVersion.id)))
    shares = list(db.scalars(select(Share).where(Share.deck_id == deck.id)))
    ended = {"active": sum(1 for s in shares if s.grantee_id), "pending": sum(1 for s in shares if not s.grantee_id)}
    payload = {
        "deck": {"id": deck.id, "name": deck.name, "text": deck.text, "source_url": deck.source_url,
                 "source_author": deck.source_author, "source_fetched_at": _iso(deck.source_fetched_at), "format": deck.format,
                 "viewed_version_id": deck.viewed_version_id, "created_at": _iso(deck.created_at), "updated_at": _iso(deck.updated_at)},
        "versions": [{"id": v.id, "text": v.text, "fingerprint": v.fingerprint, "source": v.source, "created_at": _iso(v.created_at)}
                     for v in versions],
    }
    summary = {"format": deck.format, "saved_versions": len(versions), "shares_ended": ended,
               "shares_note": "Shares and invites for this deck ended when it was deleted and are not reopened by a restore."
               if ended["active"] or ended["pending"] else None}
    return keep(db, user, "deck", deck.name or "Untitled deck", summary, payload, assistant=assistant)


def restore(db: Session, user: User, item_id: int, *, max_decks: int) -> dict:
    """Put an item back and use it up. The caller commits. ``RecoveryError`` says why not (404 gone or expired, 409 no room)."""
    item = _item(db, user, item_id, lock=True)
    if item.kind != "deck":  # the other kinds are not built yet
        raise RecoveryError("This kind of item cannot be restored yet.", 409)
    out = _restore_deck(db, user, item, max_decks)
    db.delete(item)
    db.flush()
    return out


def _restore_deck(db: Session, user: User, item: RecoveryItem, max_decks: int) -> dict:
    data = _unpack(item)
    if db.scalar(select(func.count()).select_from(Deck).where(Deck.user_id == user.id)) >= max_decks:
        raise RecoveryError(f"You have {max_decks} saved decks, the most the Vault keeps. Delete one first, then restore this.", 409)
    d = data["deck"]
    # the same ids as before when they are free (they always are: an id is never reused), so a link to the deck works again
    ids = {"id": d["id"]} if db.get(Deck, d["id"]) is None else {}
    deck = Deck(**ids, user_id=user.id, name=d["name"], text=d["text"], source_url=d["source_url"], source_author=d["source_author"],
                source_fetched_at=_when(d["source_fetched_at"]), format=d["format"], created_at=_when(d["created_at"]),
                updated_at=_when(d["updated_at"]))
    db.add(deck)
    db.flush()
    new_ids: dict[int, int] = {}
    for v in data["versions"]:
        same = {"id": v["id"]} if db.get(DeckVersion, v["id"]) is None else {}
        row = DeckVersion(**same, deck_id=deck.id, text=v["text"], fingerprint=v["fingerprint"], source=v["source"],
                          created_at=_when(v["created_at"]))
        db.add(row)
        db.flush()
        new_ids[v["id"]] = row.id
    deck.viewed_version_id = new_ids.get(d["viewed_version_id"])
    db.flush()
    ended = (item.summary or {}).get("shares_ended") or {}
    return {"restored": True, "kind": "deck", "deck": {"id": deck.id, "name": deck.name},
            "versions_restored": len(new_ids),
            "shares": ("Its shares and invites ended when it was deleted and are not reopened: share it again from the Vault web "
                       "app." if ended.get("active") or ended.get("pending") else None)}
