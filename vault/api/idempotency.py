"""Safe retries for POSTs: the ``Idempotency-Key`` header.

A client on a flaky connection may never see the answer to a POST, so it doesn't know whether
the work happened. If it sends ``Idempotency-Key: <unique id>`` (a UUID per operation), it can
retry with the same key. The first answer is stored for 24 hours and replayed, with
``Idempotent-Replayed: true``, instead of importing or creating twice. The same key on a
different endpoint is refused with 422.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import IdempotentRequest, User

TTL = timedelta(hours=24)


def idempotent(request: Request, db: Session, user: User, status: int, compute: Callable[[], Any]) -> JSONResponse:
    key = request.headers.get("idempotency-key")
    if not key:
        body = jsonable_encoder(compute())
        db.commit()
        return JSONResponse(body, status_code=status)
    if len(key) > 100:
        raise HTTPException(400, "Idempotency-Key is at most 100 characters")
    endpoint = f"{request.method} {request.url.path}"[:120]
    db.execute(delete(IdempotentRequest).where(IdempotentRequest.user_id == user.id,
                                               IdempotentRequest.created_at < datetime.now(timezone.utc) - TTL))
    stored = db.scalar(select(IdempotentRequest).where(IdempotentRequest.user_id == user.id, IdempotentRequest.key == key))
    if stored is None:
        body = jsonable_encoder(compute())
        db.add(IdempotentRequest(user_id=user.id, key=key, endpoint=endpoint, status=status, body=body))
        try:
            db.commit()
            return JSONResponse(body, status_code=status)
        except IntegrityError:  # the same key arrived twice at once: answer like the first one
            db.rollback()
            stored = db.scalar(select(IdempotentRequest).where(IdempotentRequest.user_id == user.id,
                                                               IdempotentRequest.key == key))
    if stored.endpoint != endpoint:
        raise HTTPException(422, "This Idempotency-Key was already used for a different request")
    return JSONResponse(stored.body, status_code=stored.status, headers={"Idempotent-Replayed": "true"})
