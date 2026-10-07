"""Public Archidekt decks, read through a cache (issue #133, docs/compliance.md).

A read within ``TTL`` is served from the database; a refresh asks Archidekt again unless the copy is under
``MIN_REFRESH`` old. Each call to Archidekt and each cache hit is logged as a count, so we can see whether a cap is
ever needed. No cap exists: the owner's rule is a cap only if needed, and then only behind this cache.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from .models import ArchidektDeckCache

log = logging.getLogger(__name__)
TTL = timedelta(minutes=10)
MIN_REFRESH = timedelta(seconds=60)
RETENTION = timedelta(days=7)


_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(deck_id: int) -> threading.Lock:
    with _locks_guard:
        if len(_locks) > 512:  # decks nobody is reading now
            for key in [k for k, v in _locks.items() if not v.locked()]:
                del _locks[key]
        return _locks.setdefault(deck_id, threading.Lock())


def _fresh(db: Session, deck_id: int, refresh: bool, now: datetime) -> ArchidektDeckCache | None:
    row = db.get(ArchidektDeckCache, deck_id, populate_existing=True)  # as it is now, not as this session saw it earlier
    if row is not None and now - row.fetched_at < (MIN_REFRESH if refresh else TTL):
        return row
    return None


def read(db: Session, deck_id: int, fetch: Callable[[int], dict], *, refresh: bool = False,
         now: datetime | None = None) -> dict:
    """The deck's JSON plus ``vault_cache``: where it came from and how old it is.

    The call to Archidekt can take seconds, so no database connection is held while it runs (the transaction that read the
    cache is ended first; under parallel use the held connections were what starved the pool, #169). And one instance
    asks Archidekt once for a deck however many agents want it at the same moment: the others wait for that answer and read it
    from the cache, as a polite client should."""
    now = now or datetime.now(timezone.utc)
    row = _fresh(db, deck_id, refresh, now)
    if row is None:
        db.commit()  # ends the read's transaction: the connection goes back to the pool while Archidekt answers
        with _lock_for(deck_id):
            row = _fresh(db, deck_id, refresh, now)
            if row is None:
                db.commit()
                log.info("archidekt deck cache=miss")  # one request to archidekt.com
                data = fetch(deck_id)
                stamp = now
                stmt = postgresql.insert(ArchidektDeckCache).values(deck_id=deck_id, data=data, fetched_at=stamp)
                db.execute(stmt.on_conflict_do_update(index_elements=["deck_id"], set_={"data": data, "fetched_at": stamp}))
                db.execute(delete(ArchidektDeckCache).where(ArchidektDeckCache.fetched_at < stamp - RETENTION))
                db.commit()
                return _answer(data, stamp, stamp, from_cache=False)
    log.info("archidekt deck cache=hit")
    return _answer(row.data, row.fetched_at, now, from_cache=True)


def _answer(data: dict, fetched_at: datetime, now: datetime, *, from_cache: bool) -> dict:
    return {**data, "vault_cache": {"from_cache": from_cache, "fetched_at": fetched_at.isoformat(),
                                    "age_seconds": int((now - fetched_at).total_seconds())}}
