"""Public Archidekt decks, read through a cache (issue #133, docs/compliance.md).

A read within ``TTL`` is served from the database; a refresh asks Archidekt again unless the copy is under
``MIN_REFRESH`` old. Each call to Archidekt and each cache hit is logged as a count, so we can see whether a cap is
ever needed. No cap exists: the owner's rule is a cap only if needed, and then only behind this cache.
"""

from __future__ import annotations

import logging
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


def read(db: Session, deck_id: int, fetch: Callable[[int], dict], *, refresh: bool = False,
         now: datetime | None = None) -> dict:
    """The deck's JSON plus ``vault_cache``: where it came from and how old it is."""
    now = now or datetime.now(timezone.utc)
    row = db.get(ArchidektDeckCache, deck_id)
    if row is not None:
        age = now - row.fetched_at
        if age < (MIN_REFRESH if refresh else TTL):
            log.info("archidekt deck cache=hit")
            return _answer(row.data, row.fetched_at, now, from_cache=True)
    log.info("archidekt deck cache=miss")  # one request to archidekt.com
    data = fetch(deck_id)
    stmt = postgresql.insert(ArchidektDeckCache).values(deck_id=deck_id, data=data, fetched_at=now)
    db.execute(stmt.on_conflict_do_update(index_elements=["deck_id"], set_={"data": data, "fetched_at": now}))
    db.execute(delete(ArchidektDeckCache).where(ArchidektDeckCache.fetched_at < now - RETENTION))
    db.commit()
    return _answer(data, now, now, from_cache=False)


def _answer(data: dict, fetched_at: datetime, now: datetime, *, from_cache: bool) -> dict:
    return {**data, "vault_cache": {"from_cache": from_cache, "fetched_at": fetched_at.isoformat(),
                                    "age_seconds": int((now - fetched_at).total_seconds())}}
