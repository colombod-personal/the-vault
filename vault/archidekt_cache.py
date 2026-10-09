"""Public Archidekt decks, read through a cache (issue #133, docs/compliance.md).

A read within ``TTL`` is served from the database; a refresh asks Archidekt again unless the copy is under
``MIN_REFRESH`` old. Each call to Archidekt and each cache hit is logged as a count, so we can see whether a cap is
ever needed. No cap exists: the owner's rule is a cap only if needed, and then only behind this cache.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from .models import ArchidektDeckCache

log = logging.getLogger(__name__)
TTL = timedelta(minutes=10)
MIN_REFRESH = timedelta(seconds=60)
RETENTION = timedelta(days=7)


class Rate:
    """Counts, per process, the calls to Archidekt (cache misses) and the cache hits of the last minute, so the log shows how
    often Archidekt is really asked and how much the cache saves (#133). Only counts: no deck id, no person. Each Vercel
    instance counts its own, so a sum over instances is read from the log lines, not from here."""

    WINDOW = 60.0

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._calls: deque[float] = deque()
        self._hits: deque[float] = deque()
        self._guard = threading.Lock()

    def note(self, kind: str) -> dict:
        """Record a ``miss`` (a call to Archidekt) or a ``hit``; returns the last minute's counts and the hit rate."""
        now = self._clock()
        with self._guard:
            (self._calls if kind == "miss" else self._hits).append(now)
            for q in (self._calls, self._hits):
                while q and now - q[0] > self.WINDOW:
                    q.popleft()
            calls, hits = len(self._calls), len(self._hits)
        return {"calls_last_minute": calls, "hits_last_minute": hits,
                "hit_rate": round(hits / (calls + hits), 2) if calls + hits else None}


RATE = Rate()
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
         now: datetime | None = None, budget: Callable[[], None] | None = None) -> dict:
    """The deck's JSON plus ``vault_cache``: where it came from and how old it is.

    The call to Archidekt can take seconds, so no database connection is held while it runs (the transaction that read the
    cache is ended first; under parallel use the held connections were what starved the pool, #169). And one instance
    asks Archidekt once for a deck however many agents want it at the same moment: the others wait for that answer and read it
    from the cache, as a polite client should.

    ``budget`` is called only when a call to Archidekt is about to be made (a hit costs Archidekt nothing, so it is never
    limited): it raises an HTTPException (429) past the person's allowance. Then the copy we hold is served, however old, and
    only a deck we hold no copy of is refused (#353)."""
    now = now or datetime.now(timezone.utc)
    row = _fresh(db, deck_id, refresh, now)
    if row is None:
        db.commit()  # ends the read's transaction: the connection goes back to the pool while Archidekt answers
        with _lock_for(deck_id):
            row = _fresh(db, deck_id, refresh, now)
            if row is None:
                db.commit()
                if budget is not None:
                    try:
                        budget()
                    except HTTPException:
                        held = db.get(ArchidektDeckCache, deck_id, populate_existing=True)
                        if held is None:
                            raise
                        log.info("archidekt deck cache=hit(over budget) %s", _counts(RATE.note("hit")))
                        return _answer(held.data, held.fetched_at, now, from_cache=True)
                log.info("archidekt deck cache=miss %s", _counts(RATE.note("miss")))  # one request to archidekt.com
                data = fetch(deck_id)
                stamp = now
                stmt = postgresql.insert(ArchidektDeckCache).values(deck_id=deck_id, data=data, fetched_at=stamp)
                db.execute(stmt.on_conflict_do_update(index_elements=["deck_id"], set_={"data": data, "fetched_at": stamp}))
                db.execute(delete(ArchidektDeckCache).where(ArchidektDeckCache.fetched_at < stamp - RETENTION))
                db.commit()
                return _answer(data, stamp, stamp, from_cache=False)
    log.info("archidekt deck cache=hit %s", _counts(RATE.note("hit")))
    return _answer(row.data, row.fetched_at, now, from_cache=True)


def _counts(c: dict) -> str:
    return f"calls_last_minute={c['calls_last_minute']} hits_last_minute={c['hits_last_minute']} hit_rate={c['hit_rate']}"


def _answer(data: dict, fetched_at: datetime, now: datetime, *, from_cache: bool) -> dict:
    return {**data, "vault_cache": {"from_cache": from_cache, "fetched_at": fetched_at.isoformat(),
                                    "age_seconds": int((now - fetched_at).total_seconds())}}
