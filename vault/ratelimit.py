"""Rate limits for the sign-in endpoints, kept in the database (``rate_hits``).

Serverless instances share no memory, so each request counts itself with one upsert in a fixed
one-minute window per bucket and client IP, and is refused with 429 over the limit. The key is a
hash of the bucket and IP keyed with SESSION_SECRET, so no address is stored, and a window's
first request deletes windows more than a few minutes old.

The client IP comes from Vercel's ``x-forwarded-for`` (first entry) or ``x-real-ip`` only on
Vercel, whose edge sets them; elsewhere a client could forge them, so the socket's peer is used.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from .config import Settings
from .models import RateHit

WINDOW = 60
KEEP = 5  # windows kept before they are deleted


def client_ip(request: Request, settings: Settings) -> str:
    if settings.on_vercel:
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded or request.headers.get("x-real-ip"):
            return forwarded or request.headers["x-real-ip"].strip()
    return request.client.host if request.client else ""


def hit(db: Session, key: str, minute: int) -> int:
    """Count one request; returns the window's count so far. One statement, so concurrent
    requests never lose a count. The caller commits."""
    insert = postgresql.insert if db.bind.dialect.name == "postgresql" else sqlite.insert
    stmt = insert(RateHit).values(key=key, minute=minute, hits=1)
    stmt = stmt.on_conflict_do_update(index_elements=["key", "minute"], set_={"hits": RateHit.hits + 1})
    return db.execute(stmt.returning(RateHit.hits)).scalar_one()


def limited(bucket: str, *, verify: bool = False) -> list:
    """Route ``dependencies`` limiting ``bucket``: AUTH_VERIFY_RATE_LIMIT for steps that check
    a credential or redeem a token (``verify``), else AUTH_RATE_LIMIT."""

    def check(request: Request) -> None:
        settings: Settings = request.app.state.settings
        allowed = settings.auth_verify_rate_limit if verify else settings.auth_rate_limit
        now = time.time()
        minute = int(now // WINDOW)
        key = hmac.new(settings.session_secret.encode(), f"{bucket}:{client_ip(request, settings)}".encode(),
                       hashlib.sha256).hexdigest()
        with request.app.state.db.sessions() as db:
            count = hit(db, key, minute)
            if count == 1:
                db.execute(delete(RateHit).where(RateHit.minute < minute - KEEP))
            db.commit()
        if count > allowed:
            raise HTTPException(429, "Too many sign-in attempts. Try again in a minute.",
                                headers={"Retry-After": str(max(1, math.ceil((minute + 1) * WINDOW - now)))})

    return [Depends(check)]
