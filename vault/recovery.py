"""Recoverable removals: what is deleted is kept for 30 days and can be put back (vault-wxy.18).

Why: an assistant that holds the Write permission can delete a deck with one call that carries ``confirm: true``, and the server cannot
tell that from the person's yes. Instead of trying to prove a human, the removal is made recoverable. Each removal writes a
``recovery_items`` row in the **same transaction** as the delete (both happen or neither), the person sees it in the web app (Account,
Recently deleted) and an assistant can list and restore it with ``list_recently_deleted`` and ``restore_deleted``.

* **Window**: 30 days from the removal (``DAYS``). ``restore`` refuses an item past ``expires_at`` even while its row still exists. The
  rows are deleted by the daily retention job (``prune_expired``, which runs inside the price-sync job) and, whatever that job does, by
  the person's own next listing or removal (``drop_expired``). Account erasure removes them (``ON DELETE CASCADE``, ``privacy.personal_data``).
* **A restore keeps the copy** (``restored_at``, ``restored_deck_id``) until ``expires_at``: the restored deck can be edited and deleted
  again by an app without losing the original (a second delete is an item of its own), and the original can be restored again once the
  restored deck is gone. While the restored deck still exists a second restore is refused (no duplicates).
* **Space**: at most ``MAX_ITEMS`` items and ``MAX_BYTES`` stored bytes a person. When full, a removal made by an assistant (an OAuth app or
  a personal access token) is refused with 409 and nothing is deleted. The person's own removal makes room only by dropping the oldest
  items an **app** made (and says which): first one already restored, then one with a newer item of the same name, then the oldest, which is
  dropped only when it is more than ``PROTECTED_DAYS`` old (otherwise the person is told to choose in the Account panel). If only the person's
  own items are left it is refused with a plain sentence. Nothing of the person's is ever dropped silently, and a loop of an app's deletes can
  never push out the person's own items or the deck the app deleted last week.
* **Locks, always in this order**: the account (``lock_account``: ``FOR NO KEY UPDATE`` on the user row, which every deck creation and
  every removal or restore takes, so the deck count and the space are checked one at a time per person and a restore can never
  deadlock with a foreign-key insert), then the deck, then the recovery item.
* **Who may do what**: restore needs the write scope (a tool can do it); removing an item for good is the person's alone
  (``account_user``: no token has the ``account`` scope). A deck's shares are not reopened by a restore: it never widens who can see.

Only decks today (``kind`` "deck"); the table's kinds are ready for the copies an import replaced, a tag and a share.
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from . import locks
from .models import Deck, DeckVersion, RecoveryItem, Share, User

DAYS = 30
MAX_ITEMS = 200
MAX_BYTES = 8 * 1024 * 1024  # stored (compressed) bytes a person may keep
MAX_ITEM_JSON_BYTES = 20 * 1024 * 1024  # what one item may hold when it is written
MAX_UNPACKED_BYTES = 64 * 1024 * 1024  # what a reader will decompress: more is refused, never expanded
PROTECTED_DAYS = 7  # an item an app deleted this recently is never dropped to make room: the person is asked to choose instead
FORMAT = 1  # the payload's ``v``; a reader refuses any other


class RecoveryError(ValueError):
    """The removal cannot be kept or the item cannot be put back. ``status`` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


UNREADABLE = "This item cannot be read any more, so it cannot be put back. It is deleted when its 30 days are over."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _when(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def lock_account(db: Session, user: User) -> None:
    """Take the person's account lock (``FOR NO KEY UPDATE`` on the user row) until the transaction ends. Deck creation, removal and
    restore all take it first, so the count of decks and the recovery space are read and changed one request at a time. It does not
    conflict with the ``FOR KEY SHARE`` a foreign-key insert takes, so it cannot deadlock with one. The wait is bounded like every other
    account lock (``vault.locks.LOCK_TIMEOUT``, ``SET LOCAL`` for the transaction): past it the caller gets the usual 503 with Retry-After."""
    db.execute(text(f"SET LOCAL lock_timeout = '{locks.LOCK_TIMEOUT}'"))
    db.execute(select(User.id).where(User.id == user.id).with_for_update(key_share=True))


def _pack(payload: dict) -> tuple[int, bytes]:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return len(raw), zlib.compress(raw, 6)


def _unpack(item: RecoveryItem) -> dict:
    try:
        reader = zlib.decompressobj()
        raw = reader.decompress(item.payload, MAX_UNPACKED_BYTES)
        if reader.unconsumed_tail:  # more than the cap: refuse, do not expand it
            raise ValueError("too big")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("v") != FORMAT:
            raise ValueError("unknown format")
        return payload
    except (zlib.error, ValueError, TypeError, MemoryError, RecursionError):
        raise RecoveryError(UNREADABLE, 409) from None


def drop_expired(db: Session, user: User) -> int:
    """The caller's own expired items, deleted now (the daily job does everyone's)."""
    return db.execute(delete(RecoveryItem).where(RecoveryItem.user_id == user.id, RecoveryItem.expires_at <= _now())
                      .execution_options(synchronize_session=False)).rowcount or 0


def prune_expired(db: Session) -> int:
    """Every person's expired items: the retention job's part (the 30 days stated in the privacy notice)."""
    return db.execute(delete(RecoveryItem).where(RecoveryItem.expires_at <= _now())
                      .execution_options(synchronize_session=False)).rowcount or 0


def _victim(db: Session, user: User) -> RecoveryItem:
    """The app-made item to drop so the person's own removal fits, or ``RecoveryError``. Only items an app made are ever candidates.
    In this order: one that was restored already (its deck is back), then one with a newer item of the same name (a duplicate), then
    the oldest. The oldest is dropped only when its 7 days are over: an app that deleted the one deck that matters and then filled the
    space with junk must not be able to have it dropped by the person's next delete, so the person is asked to choose instead."""
    apps = db.execute(select(RecoveryItem.id, RecoveryItem.label, RecoveryItem.restored_at, RecoveryItem.created_at)
                      .where(RecoveryItem.user_id == user.id, RecoveryItem.source == "assistant").order_by(RecoveryItem.id)).all()
    if not apps:
        raise RecoveryError(
            "Nothing was deleted: the 30-day recovery space is full of decks you deleted yourself. Remove some for good under "
            "Account, Recently deleted (they cannot be restored after that), then delete again.", 409)
    newest = dict(db.execute(select(RecoveryItem.label, func.max(RecoveryItem.id)).where(RecoveryItem.user_id == user.id)
                             .group_by(RecoveryItem.label)).all())
    chosen = (next((a for a in apps if a.restored_at is not None), None)
              or next((a for a in apps if newest.get(a.label, a.id) > a.id), None))
    if chosen is None:
        chosen = apps[0]
        if _now() - chosen.created_at < timedelta(days=PROTECTED_DAYS):
            raise RecoveryError(
                f"Nothing was deleted: the 30-day recovery space is full, and the oldest item to drop (“{chosen.label}”, deleted by an app "
                f"less than {PROTECTED_DAYS} days ago) may be the one you want back. Look at Account, Recently deleted, remove something "
                "for good (it cannot be restored after that), then delete again.", 409)
    return db.get(RecoveryItem, chosen.id)


def keep(db: Session, user: User, kind: str, label: str, summary: dict, payload: dict, *,
         assistant: str | None) -> tuple[RecoveryItem, list[dict]]:
    """Store a removal that is about to happen, in the caller's transaction; returns the item and the older items dropped to make
    room (always empty for an assistant). ``assistant`` is the app's name when an assistant is removing it, None for the person.
    Raises ``RecoveryError`` (409) when it cannot be kept: the caller must not remove anything then."""
    lock_account(db, user)
    drop_expired(db, user)
    raw_len, packed = _pack({"v": FORMAT, **payload})
    if raw_len > MAX_ITEM_JSON_BYTES or len(packed) > MAX_BYTES:
        raise RecoveryError("This is too big to keep for the 30-day recovery, so it was not removed.", 413)
    count, total = db.execute(select(func.count(), func.coalesce(func.sum(RecoveryItem.stored_bytes), 0))
                              .where(RecoveryItem.user_id == user.id)).one()
    dropped: list[dict] = []
    while count >= MAX_ITEMS or total + len(packed) > MAX_BYTES:
        if assistant is not None:
            raise RecoveryError(
                "Nothing was deleted: the 30-day recovery space for this account is full, and an app may not make room by "
                "discarding earlier items. The person can remove some for good in the Vault web app (Account, Recently deleted).", 409)
        victim = _victim(db, user)
        dropped.append({"name": victim.label, "deleted_by": victim.source_detail, "deleted_at": _iso(victim.created_at)})
        total -= victim.stored_bytes
        count -= 1
        db.delete(victim)
        db.flush()
    now = _now()
    item = RecoveryItem(user_id=user.id, kind=kind, label=label[:200], summary=summary, payload=packed, raw_bytes=raw_len,
                        stored_bytes=len(packed), source="assistant" if assistant is not None else "person",
                        source_detail=assistant, created_at=now, expires_at=now + timedelta(days=DAYS))
    db.add(item)
    db.flush()
    return item, dropped


def listing(db: Session, user: User) -> list[RecoveryItem]:
    """The caller's items that can still be put back, newest first. The caller commits (this deletes the expired ones)."""
    drop_expired(db, user)
    return list(db.scalars(select(RecoveryItem).where(RecoveryItem.user_id == user.id).order_by(RecoveryItem.id.desc())))


def live_decks(db: Session, user: User, items: list[RecoveryItem]) -> set[int]:
    """The ids, among the decks the items were restored as, that exist now."""
    ids = {i.restored_deck_id for i in items if i.restored_deck_id is not None}
    return set(db.scalars(select(Deck.id).where(Deck.user_id == user.id, Deck.id.in_(ids)))) if ids else set()


def describe(item: RecoveryItem, live: set[int] | None = None) -> dict:
    restored = None
    if item.restored_at is not None:
        restored = {"at": _iso(item.restored_at), "deck_id": item.restored_deck_id,
                    "deck_exists": item.restored_deck_id in (live or set())}
    return {"id": item.id, "kind": item.kind, "name": item.label, "summary": item.summary,
            "deleted_by": {"who": item.source, "app": item.source_detail}, "deleted_at": _iso(item.created_at),
            "restorable_until": _iso(item.expires_at), "restored": restored,
            "restore": "restore_deleted with this item_id (tool), or Restore under Account, Recently deleted (web app)"}


def _item(db: Session, user: User, item_id: int, *, lock: bool = False) -> RecoveryItem:
    query = select(RecoveryItem).where(RecoveryItem.id == item_id, RecoveryItem.user_id == user.id)  # another person's id is a 404
    item = db.scalar(query.with_for_update() if lock else query)
    if item is None:
        raise RecoveryError("Nothing to restore with that id: it may be removed for good or past its 30 days.", 404)
    if item.expires_at <= _now():
        raise RecoveryError(f"Its {DAYS} days are over, so it can no longer be restored.", 404)
    return item


def purge(db: Session, user: User, item_id: int) -> None:
    """Remove one item for good (the person's own act: the route needs a sign-in that may manage the account)."""
    item = db.scalar(select(RecoveryItem).where(RecoveryItem.id == item_id, RecoveryItem.user_id == user.id))
    if item is None:
        raise RecoveryError("Not found", 404)
    db.delete(item)


# -- decks ----------------------------------------------------------------------------------------------------------------------

def keep_deck(db: Session, user: User, deck: Deck, *, assistant: str | None) -> tuple[RecoveryItem, list[dict]]:
    """Keep the deck, its saved versions and the count of its shares, before the caller deletes it. ``deck`` must be the row the
    caller read ``FOR UPDATE`` after taking the account lock, so what is kept is what is deleted. Shares are counted, not kept: a
    restore never reopens access, so no invite token or grantee is stored."""
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
    """Put an item back. The copy stays (marked restored) until its 30 days are over. The caller commits. ``RecoveryError`` says why
    not (404 gone or expired, 409 no room, already restored, unreadable or changed at the same moment)."""
    lock_account(db, user)  # the same lock deck creation takes: the count below cannot change meanwhile
    item = _item(db, user, item_id, lock=True)
    if item.kind != "deck":  # the other kinds are not built yet
        raise RecoveryError("This kind of item cannot be restored yet.", 409)
    if item.restored_deck_id is not None:
        name = db.scalar(select(Deck.name).where(Deck.id == item.restored_deck_id, Deck.user_id == user.id))
        if name is not None:
            raise RecoveryError(f"Already restored as the deck “{name}” (id {item.restored_deck_id}). If that deck is deleted "
                                "again, this original can be restored again.", 409)
    try:
        with db.begin_nested():  # a savepoint: a failure leaves nothing half put back
            out = _restore_deck(db, user, item, max_decks)
    except RecoveryError:  # a reason of its own (no room, unreadable): said as it is
        raise
    except IntegrityError:
        raise RecoveryError("This could not be put back because something changed at the same moment. Try again.", 409) from None
    except DBAPIError:  # a value the database refuses (too long, not a number): the copy is not what this code wrote
        raise RecoveryError(UNREADABLE, 409) from None
    except (KeyError, TypeError, ValueError, AttributeError):  # a copy that is not shaped as this code wrote it
        raise RecoveryError(UNREADABLE, 409) from None
    item.restored_at, item.restored_deck_id = _now(), out["deck"]["id"]
    db.flush()
    return out


def _restore_deck(db: Session, user: User, item: RecoveryItem, max_decks: int) -> dict:
    data = _unpack(item)
    if db.scalar(select(func.count()).select_from(Deck).where(Deck.user_id == user.id)) >= max_decks:
        raise RecoveryError(f"You have {max_decks} saved decks, the most the Vault keeps. Delete one first, then restore this.", 409)
    d = data["deck"]
    # the same ids as before when they are free, so a link to the deck works again; another copy of the same deck gets new ones
    ids = {"id": d["id"]} if db.get(Deck, d["id"]) is None else {}
    deck = Deck(**ids, user_id=user.id, name=str(d["name"])[:200], text=d["text"], source_url=d["source_url"],
                source_author=d["source_author"], source_fetched_at=_when(d["source_fetched_at"]), format=d["format"],
                created_at=_when(d["created_at"]), updated_at=_when(d["updated_at"]))
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
