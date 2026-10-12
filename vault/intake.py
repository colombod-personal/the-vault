"""A collection file dropped in the chat, staged by the Vault and previewed in the same chat (vault-0n9.3).

``POST /api/v1/uploads/from-file`` (any scope, read-only included: it imports nothing, ``vault.app.READ_ONLY_POSTS``). The
chat app hands the Vault the attached file as ``{download_url, file_id, mime_type?, file_name?}`` (the MCP tool
``import_collection_file`` declares ``_meta["openai/fileParams"]``); the Vault fetches it under the rules of
``vault.file_fetch``, stages the bytes exactly as an upload through the link does (a ``StagedUpload`` of the person), and
answers with the same preview as ``GET /uploads/{id}`` plus the ``content_hash`` the person's yes is bound to. **Nothing in the
collection changes**: the import is ``POST /uploads/{id}/apply``, write scope, only for that ``content_hash``.

* A person holds one fetched file at a time: a new one replaces the earlier (an identical one is the same row, so a host's
  duplicate call gives the same id). Fetched rows are told apart by ``ticket_hash`` starting ``intake-``, a value no ticket can
  hash to, so there is no link for them.
* ``file`` that is not an object with a web address (the string a phone app sends, a local path, a missing key) is answered with
  one plain sentence, and its shape (type, a 12-character prefix, length) goes to the log, never the value of a web address.
* The person's import allowance (``IMPORT_LIMIT`` a minute) counts every try, on a counter of its own.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import file_fetch, observability
from .api.schemas import MAX_ID
from .importer import ImportError_, ImportOptions, NoSuchBucket, preview_import
from .models import Bucket, StagedUpload, User
from .ratelimit import per_user
from .uploads import LIFETIME, content_hash

V1 = "/api/v1"
MARKER = "intake-"  # the start of a fetched row's ticket_hash: 7 + 56 hex characters = 63, never a sha256 digest

SAY = {
    "cannot_hand_over": file_fetch.CANNOT_HAND_OVER,
    "fetch_failed": "The Vault could not get the file from this app just now. The collection was not changed. Call this tool "
                    "once more with the same file; if it fails again, the person can bring the file in with 'Import CSV' on "
                    "the Vault website, which works with the same file.",
    "busy": "The Vault is fetching other files right now. The collection was not changed. Call this tool once more in a moment.",
    "too_large": "The file is bigger than the 20 MB the Vault takes, so it was not read. The collection was not changed.",
    "not_text": "The file is not a CSV or a text export, so the Vault did not read it. The collection was not changed.",
}


class FileIntake(BaseModel):
    """``file`` is deliberately untyped: what a host sends for a file it cannot hand over (a string, a path, nothing) must reach
    the handler to be answered in one sentence, not fail validation."""

    file: Any = None
    bucket_id: int | None = Field(None, ge=1, le=MAX_ID)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_router(get_db, current_user, settings) -> APIRouter:
    router = APIRouter()

    def log(request: Request, outcome: str, **fields) -> None:
        state = request.scope.get("state", {})
        level = logging.INFO if outcome == "staged" else logging.WARNING
        observability.event(level, "file_intake", request_id=state.get("request_id", ""), outcome=outcome,
                            **{k: v for k, v in fields.items() if v is not None})

    def answer(request: Request, kind: str, **fields) -> dict:
        """A refusal as the model reads it: what happened in a word, one plain sentence, and that nothing changed."""
        log(request, kind, **fields)
        return {"status": "cannot_receive" if kind == "cannot_hand_over" else kind, "say": SAY[kind], "changed": False}

    @router.post(f"{V1}/uploads/from-file", tags=["imports"],
                 summary="Stage a collection file the chat app hands over by address (fetched by the Vault, previewed, not imported)")
    def stage_from_file(request: Request, body: FileIntake, user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> dict:
        if body.bucket_id is not None and db.scalar(
                select(Bucket.id).where(Bucket.id == body.bucket_id, Bucket.user_id == user.id)) is None:
            raise HTTPException(404, "Bucket not found")  # another person's id is the same 404
        per_user(request, "file intake", user.id, settings.import_limit, db)  # every try counts, on a counter of its own
        fetcher: file_fetch.FileFetcher = request.app.state.file_fetcher
        try:
            handoff = file_fetch.read_handoff(body.file)
            fetcher.host_of(handoff.url)
        except file_fetch.Refused as exc:
            # The shape of what arrived, so the first real run says what the host really sends (never an address's path or query).
            return answer(request, exc.kind, reason=exc.reason, host=exc.host, shape=file_fetch.shape_of(body.file))
        db.commit()  # the connection goes back to the pool while the file's host answers
        try:
            fetched = fetcher.fetch(handoff)
        except file_fetch.Refused as exc:
            return answer(request, exc.kind, reason=exc.reason, host=exc.host, status=exc.status)
        try:
            preview = preview_import(db, user, fetched.content, ImportOptions(bucket_id=body.bucket_id))
        except NoSuchBucket as exc:
            raise HTTPException(404, str(exc)) from exc
        except ImportError_ as exc:
            log(request, "unreadable", bytes=len(fetched.content), content_type=fetched.content_type)
            return {"status": "unreadable", "changed": False, "detail": str(exc),
                    "say": f"The Vault could not read this file as a collection export ({exc}). The collection was not changed."}
        staged = stage(db, user, fetched.content, file_fetch.clean_filename(handoff.file_name), body.bucket_id)
        log(request, "staged", bytes=len(fetched.content), content_type=fetched.content_type, rows=preview["rows"],
            source=preview["source"])
        bucket = db.get(Bucket, staged.bucket_id) if staged.bucket_id is not None else None
        return {"id": staged.id, "status": "uploaded", "changed": False, "filename": staged.filename,
                "expires_at": staged.expires_at.isoformat(),
                "bucket": {"id": bucket.id, "name": bucket.name} if bucket is not None else None,
                "content_hash": content_hash(staged.content, staged.bucket_id), **preview,
                "next": "Nothing has been imported. Show the person what would change and the few things that deserve a look, "
                        "and ask for one yes. On their yes, call confirm_staged_upload with this id as upload_id, confirm true "
                        "and this content_hash."}

    return router


def stage(db: Session, user: User, content: bytes, filename: str, bucket_id: int | None) -> StagedUpload:
    """Store the fetched file for the person: one fetched file at a time (an identical one is the same row), no ticket."""
    now = _now()
    db.execute(delete(StagedUpload).where(StagedUpload.expires_at <= now))
    wanted = content_hash(content, bucket_id)
    kept: StagedUpload | None = None
    for row in db.scalars(select(StagedUpload).where(StagedUpload.user_id == user.id, StagedUpload.ticket_hash.like(MARKER + "%"))):
        if (kept is None and row.content is not None and row.bucket_id == bucket_id
                and content_hash(row.content, row.bucket_id) == wanted):
            kept = row
        else:
            db.delete(row)  # the earlier fetched file, replaced
    if kept is None:
        kept = StagedUpload(user_id=user.id, ticket_hash=MARKER + secrets.token_hex(28), filename=filename, content=content,
                            uploaded_at=now, bucket_id=bucket_id, bucket_bound=bucket_id is not None)
        db.add(kept)
    kept.expires_at = now + LIFETIME
    db.commit()
    return kept
