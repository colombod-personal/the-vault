"""Safe retries for POSTs: the ``Idempotency-Key`` header.

A client on a flaky connection may never see the answer to a POST, so it doesn't know whether
the work happened. If it sends ``Idempotency-Key: <unique id>`` (a UUID per operation), it can
retry with the same key. The first answer is stored for 24 hours and replayed, with
``Idempotent-Replayed: true``, instead of importing or creating twice. The same key on a
different endpoint is refused with 422.

The key is reserved (committed) before the work starts, so of two requests arriving at once
only one does the work; the other gets 409 with ``Retry-After`` until the first has answered.
The work and the stored answer are committed together (the endpoints leave their work
uncommitted for this); a request that fails, even while saving its answer, undoes its work and
releases its key, so it can be retried. Stored answers never hold secrets:
an endpoint that returns one passes ``redact`` (what to store) and ``replay`` (how to rebuild
the answer, e.g. by minting a fresh invite link).
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
PENDING = 0  # status of a reserved key whose request hasn't answered yet


def idempotent(request: Request, db: Session, user: User, status: int, compute: Callable[[], Any], *,
               redact: Callable[[Any], Any] | None = None,
               replay: Callable[[Any], Any] | None = None) -> JSONResponse:
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
    reservation = IdempotentRequest(user_id=user.id, key=key, endpoint=endpoint, status=PENDING, body={})
    db.add(reservation)
    try:
        db.commit()
    except IntegrityError:  # the key is known: answer like the first request
        db.rollback()
        stored = db.scalar(select(IdempotentRequest).where(IdempotentRequest.user_id == user.id,
                                                           IdempotentRequest.key == key))
        if stored is None:  # released a moment ago by a failed first attempt
            raise HTTPException(409, "A request with this Idempotency-Key just failed; retry it",
                                headers={"Retry-After": "1"}) from None
        return _replay(stored, endpoint, replay)
    reservation_id = reservation.id
    try:
        body = jsonable_encoder(compute())  # the work, left uncommitted
        row = db.get(IdempotentRequest, reservation_id)
        row.status, row.body = status, jsonable_encoder(redact(body)) if redact else body
        db.commit()  # the work and its stored answer together: both or neither
    except BaseException:
        db.rollback()  # undo the work, then release the key so a retry runs again
        db.execute(delete(IdempotentRequest).where(IdempotentRequest.id == reservation_id))
        db.commit()
        raise
    return JSONResponse(body, status_code=status)


def _replay(stored: IdempotentRequest, endpoint: str, replay: Callable[[Any], Any] | None) -> JSONResponse:
    if stored.endpoint != endpoint:
        raise HTTPException(422, "This Idempotency-Key was already used for a different request")
    if stored.status == PENDING:
        raise HTTPException(409, "A request with this Idempotency-Key is still being processed. Retry shortly; "
                                 "if this persists, the first attempt was interrupted: check the result and use a new key",
                            headers={"Retry-After": "2"})
    body = jsonable_encoder(replay(stored.body)) if replay else stored.body
    return JSONResponse(body, status_code=stored.status, headers={"Idempotent-Replayed": "true"})
