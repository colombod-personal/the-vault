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
  one plain sentence, and its shape (type, length and a masked prefix: only the known sentinel chat_upload as it is) goes to the log, never the value of a web address.
* The person's import allowance (``IMPORT_LIMIT`` a minute) counts every try, on a counter of its own.
* **A file sent as text in parts** (``text``, ``part``, ``parts``, ``lines``, ``sum``; ``vault.file_parts``) for a coding assistant that
  was given only the file's local path: nothing is fetched, so there is no new network surface. Every part is stored as it arrives
  (``staged_upload_parts``, keyed by the staged upload and the part's number, with the part's hash), the same part again is a no-op,
  a different text for the same number replaces it until the file is complete, and the answer to a part names what is still
  missing. When every part is there the line count, the checksum and the text are checked, and only then is anything previewed;
  a mismatch deletes the parts and answers one plain sentence. The assembled file goes the same way as a fetched one
  (``stage`` rules: one at a time per person, no ticket, the same advisory lock), and the confirm is the same
  ``confirm_staged_upload``. One upload counts once against ``IMPORT_LIMIT`` (when its first part makes the staged upload); the parts
  count against ``IMPORT_PART_LIMIT`` a minute, on a counter of their own.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from . import config, file_fetch, file_parts, locks, observability
from .api.idempotency import idempotent
from .api.schemas import MAX_ID
from .importer import ImportError_, ImportOptions, NoSuchBucket, preview_import
from .models import Bucket, StagedUpload, StagedUploadPart, User
from .ratelimit import per_user
from .uploads import INTAKE_MARKER, LIFETIME, UPLOAD_LOCK_CLASS, content_hash

V1 = "/api/v1"
MARKER = INTAKE_MARKER  # the start of a fetched row's ticket_hash: 7 + 56 hex characters = 63, never a sha256 digest

SAY = {
    "cannot_hand_over": file_fetch.CANNOT_HAND_OVER,
    "fetch_failed": "The Vault could not get the file from this app just now. The collection was not changed. Call this tool "
                    "once more with the same file; if it fails again, the person can bring the file in with 'Import CSV' on "
                    "the Vault website, which works with the same file.",
    "busy": "The Vault is fetching other files right now. The collection was not changed. Call this tool once more in a moment.",
    "too_large": lambda: f"The file is bigger than the {config.size_text(config.import_max_bytes())} the Vault takes, so it was not "
                         "read and the collection was not changed.",
    "not_text": "The file is not a CSV or a text export, so the Vault did not read it. The collection was not changed.",
}

# What a file sent in parts is told when it cannot be taken: one plain sentence for the person, and for the model what to do.
PART_SAY = {
    "bad_part": "The Vault could not use this part of the file (a part needs its text, its number, the number of parts, the file's "
                "line count and its checksum), so nothing was staged and the collection was not changed.",
    "mismatch": "The Vault did not get the whole file in this app just now (what arrived did not match the file's own line count or "
                "checksum), so nothing was staged and the collection was not changed.",
    "why": {"lines": "the line count did not match", "checksum": "the checksum did not match", "text": "the text was not valid"},
}


class FileIntake(BaseModel):
    """``file`` is deliberately untyped: what a host sends for a file it cannot hand over (a string, a path, nothing) must reach
    the handler to be answered in one sentence, not fail validation."""

    file: Any = None
    bucket_id: int | None = Field(None, ge=1, le=MAX_ID)
    # A file sent as text in parts (vault.file_parts). Untyped numbers: a wrong one is answered in a sentence, not a schema error.
    text: str | None = None
    part: Any = None
    parts: Any = None
    lines: Any = None
    sum: Any = None


class IntakeAnswer(BaseModel):
    """What the tool answers, for OpenAPI. ``status`` is ``uploaded`` (staged, with ``id``, ``content_hash`` and the preview's own
    fields: rows, copies, changes, merge, matched and unmatched rows ...), or one of ``cannot_receive``, ``fetch_failed``, ``busy``,
    ``too_large``, ``not_text``, ``unreadable``, ``bad_part``, ``mismatch``: then ``say`` is the sentence to give the person and nothing
    was changed; or ``part_received`` (a file sent in parts is not complete: ``missing`` names the parts still to send)."""

    model_config = ConfigDict(extra="allow")  # the preview's fields are the import preview's, as GET /uploads/{id} answers them

    status: str
    changed: bool = False
    say: str | None = None
    detail: str | None = None
    id: int | None = None
    filename: str | None = None
    expires_at: str | None = None
    bucket: dict | None = None
    content_hash: str | None = None
    next: str | None = None
    part: int | None = None
    parts: int | None = None
    have: int | None = None
    missing: list[int] | None = None


def plain_reason(exc: Exception) -> str:
    """Why a file could not be read, in words the Vault wrote. The importer's message is not repeated: for a bad cell the library
    quotes it, and the sentence is read by an assistant."""
    text = str(exc)
    if text.startswith("File is not UTF-8"):
        return "it is not UTF-8 text"
    if text.startswith("File is too large"):
        return "it is too large"
    return "no cards could be read from its columns and cells"


class Declined(Exception):
    """An answer that is not a staged file (the app cannot hand it over, the fetch failed, the file is unreadable): raised out of
    the idempotent work so it is not stored under the key, and answered as it is. A retry with the same key then runs again."""

    def __init__(self, payload: dict):
        super().__init__("declined")
        self.payload = payload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def staged_payload(db: Session, staged: StagedUpload, preview: dict) -> dict:
    """The answer for a staged file: the same for a file fetched from an address and one assembled from parts."""
    bucket = db.get(Bucket, staged.bucket_id) if staged.bucket_id is not None else None
    return {"id": staged.id, "status": "uploaded", "changed": False, "filename": staged.filename,
            "expires_at": staged.expires_at.isoformat(),
            "bucket": {"id": bucket.id, "name": bucket.name} if bucket is not None else None,
            "content_hash": content_hash(staged.content, staged.bucket_id), **preview,
            "next": "Nothing has been imported. Show the person what would change and the few things that deserve a look, "
                    "and ask for one yes. On their yes, call confirm_staged_upload with this id as upload_id, confirm true "
                    "and this content_hash."}


def build_router(get_db, current_user, settings) -> APIRouter:
    router = APIRouter()

    def log(request: Request, outcome: str, **fields) -> None:
        state = request.scope.get("state", {})
        level = logging.INFO if outcome in ("staged", "part") else logging.WARNING
        observability.event(level, "file_intake", request_id=state.get("request_id", ""), outcome=outcome,
                            **{k: v for k, v in fields.items() if v is not None})

    def answer(request: Request, kind: str, **fields) -> dict:
        """A refusal as the model reads it: what happened in a word, one plain sentence, and that nothing changed."""
        log(request, kind, **fields)
        say = SAY[kind]() if callable(SAY[kind]) else SAY[kind]
        out = {"status": "cannot_receive" if kind == "cannot_hand_over" else kind, "say": say, "changed": False}
        if kind == "cannot_hand_over":
            out["next"] = file_parts.steps()  # for a coding assistant that has the file's path: read it in parts, never for the person
        return out

    @router.post(f"{V1}/uploads/from-file", tags=["imports"], response_model=IntakeAnswer, response_model_exclude_none=True,
                 summary="Stage a collection file the chat app hands over by address (fetched by the Vault) or as text in "
                         "ordered parts (previewed, not imported)")
    def stage_from_file(request: Request, body: FileIntake, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
        if body.bucket_id is not None and db.scalar(
                select(Bucket.id).where(Bucket.id == body.bucket_id, Bucket.user_id == user.id)) is None:
            raise HTTPException(404, "Bucket not found")  # another person's id is the same 404
        if body.file is None and body.text is not None:
            return stage_parts(request, body, user, db)
        per_user(request, "file intake", user.id, settings.import_limit, db)  # every try counts, on a counter of its own
        fetcher: file_fetch.FileFetcher = request.app.state.file_fetcher

        def run() -> dict:
            """The work, left uncommitted for ``idempotent`` (Idempotency-Key, CLAUDE.md): a retry of a request whose answer was
            lost replays that answer, whatever was dropped in between, and fetches nothing. Only a staged file is stored under
            the key: every other answer is raised as ``Declined`` so a retry runs again."""
            try:
                handoff = file_fetch.read_handoff(body.file)
                fetcher.host_of(handoff.url)
            except file_fetch.Refused as exc:
                # The shape of what arrived, so the first real run says what the host really sends (never a path or query).
                raise Declined(answer(request, exc.kind, reason=exc.reason, host=exc.host,
                                      shape=file_fetch.shape_of(body.file))) from None
            try:
                fetched = fetcher.fetch(handoff)  # no database statement has run since the key was reserved: no connection held
            except file_fetch.Refused as exc:
                raise Declined(answer(request, exc.kind, reason=exc.reason, host=exc.host, status=exc.status)) from None
            try:
                preview = preview_import(db, user, fetched.content, ImportOptions(bucket_id=body.bucket_id))
            except NoSuchBucket as exc:
                raise HTTPException(404, str(exc)) from exc
            except ImportError_ as exc:
                log(request, "unreadable", bytes=len(fetched.content), content_type=fetched.content_type)
                reason = plain_reason(exc)  # a fixed phrase: the library's message can quote a cell of the file
                raise Declined({"status": "unreadable", "changed": False, "detail": reason,
                                "say": f"The Vault could not read this file as a collection export ({reason}). "
                                       "The collection was not changed."}) from None
            staged = stage(db, user, fetched.content, file_fetch.clean_filename(handoff.file_name), body.bucket_id)
            log(request, "staged", bytes=len(fetched.content), content_type=fetched.content_type, rows=preview["rows"],
                source=preview["source"])
            return staged_payload(db, staged, preview)

        try:
            return idempotent(request, db, user, 200, run)
        except Declined as declined:
            return JSONResponse(declined.payload)

    # -- a file sent as text in ordered parts ------------------------------------------------------------------------------

    def bad_part(request: Request, why: str, **fields) -> JSONResponse:
        log(request, "bad_part", reason=why, **fields)
        return JSONResponse({"status": "bad_part", "changed": False, "say": PART_SAY["bad_part"]})

    def stage_parts(request: Request, body: FileIntake, user: User, db: Session):
        numbers = {"part": body.part, "parts": body.parts, "lines": body.lines, "sum": body.sum}
        if any(isinstance(v, bool) or not isinstance(v, int) for v in numbers.values()):
            return bad_part(request, "numbers_missing", given=sorted(k for k, v in numbers.items() if v is not None))
        part, parts, lines, total_sum = body.part, body.parts, body.lines, body.sum
        if not (1 <= part <= parts <= file_parts.MAX_PARTS and 0 <= lines and 0 <= total_sum <= 0xFFFFFFFF):
            return bad_part(request, "numbers_out_of_range", parts=min(max(parts, 0), 10**6))
        try:
            data = file_parts.to_bytes(body.text)
        except UnicodeError:
            return bad_part(request, "text_not_valid")
        limit = config.import_max_bytes()
        if len(data) > limit:
            return JSONResponse(answer(request, "too_large", bytes=len(data), parts=parts))
        key = file_parts.set_key(user.id, total_sum, parts, lines, body.bucket_id)
        known = db.scalar(select(StagedUpload.id).where(StagedUpload.ticket_hash == key, StagedUpload.user_id == user.id))
        if known is None:  # a new upload: it counts once, here, not once per part
            per_user(request, "file intake", user.id, settings.import_limit, db)
        per_user(request, "file intake part", user.id, settings.import_part_limit, db)  # the parts have a counter of their own

        def run() -> dict:
            db.execute(text(f"SET LOCAL lock_timeout = '{locks.LOCK_TIMEOUT}'"))
            db.execute(select(func.pg_advisory_xact_lock(UPLOAD_LOCK_CLASS, user.id)))  # one decision at a time per person
            now = _now()
            db.execute(delete(StagedUpload).where(StagedUpload.expires_at <= now))
            row = db.scalar(select(StagedUpload).where(StagedUpload.ticket_hash == key, StagedUpload.user_id == user.id))
            if row is not None and row.content is not None:  # complete already: a part sent again changes nothing
                preview = preview_import(db, user, row.content, ImportOptions(bucket_id=row.bucket_id))
                return staged_payload(db, row, preview)
            if row is None:  # one file in progress at a time: an earlier unfinished one is dropped with its parts
                db.execute(delete(StagedUpload).where(StagedUpload.user_id == user.id, StagedUpload.content.is_(None),
                                                      StagedUpload.ticket_hash.like(MARKER + "%")))
                row = StagedUpload(user_id=user.id, ticket_hash=key, expires_at=now + LIFETIME, bucket_id=body.bucket_id,
                                   bucket_bound=body.bucket_id is not None)
                db.add(row)
                db.flush()
            digest = file_parts.part_hash(data)
            existing = db.get(StagedUploadPart, (row.id, part))
            if existing is None:
                db.add(StagedUploadPart(staged_upload_id=row.id, part_index=part, part_hash=digest, content=data))
            elif existing.part_hash != digest:  # a different text for this number replaces it (the file is not complete)
                existing.part_hash, existing.content = digest, data
            db.flush()
            held = db.scalar(select(func.coalesce(func.sum(func.length(StagedUploadPart.content)), 0))
                             .where(StagedUploadPart.staged_upload_id == row.id))
            if held > limit:  # nothing is kept of a file over the limit
                db.delete(row)
                db.flush()
                return answer(request, "too_large", bytes=int(held), parts=parts)
            have = set(db.scalars(select(StagedUploadPart.part_index).where(StagedUploadPart.staged_upload_id == row.id)))
            missing = [i for i in range(1, parts + 1) if i not in have]
            if missing:
                log(request, "part", part=part, parts=parts, have=len(have))
                shown = missing[:file_parts.MAX_LISTED]
                return {"status": "part_received", "changed": False, "id": row.id, "part": part, "parts": parts,
                        "have": len(have), "missing": shown,
                        "next": f"Part {part} of {parts} is stored; {file_parts.ranges(shown)}"
                                f"{' (and more)' if len(missing) > len(shown) else ''} {'is' if len(shown) == 1 else 'are'} still "
                                "missing. Send them with the same parts, lines and sum. Nothing is previewed until every part is "
                                "here, and nothing has been imported."}
            chunks = (c for (c,) in db.execute(select(StagedUploadPart.content).where(StagedUploadPart.staged_upload_id == row.id)
                                               .order_by(StagedUploadPart.part_index)))
            try:
                content = file_parts.assemble(chunks, lines, total_sum)
            except file_parts.Mismatch as exc:  # a truncated or altered read must never become a partial import
                db.delete(row)
                db.flush()
                log(request, "mismatch", reason=exc.reason, parts=parts)
                return {"status": "mismatch", "changed": False, "detail": exc.reason, "say": PART_SAY["mismatch"],
                        "next": f"Read the file again in parts half the size ({PART_SAY['why'][exc.reason]}) and send every "
                                "part again; the parts sent so far were discarded."}
            if len(content) > limit:
                db.delete(row)
                db.flush()
                return answer(request, "too_large", bytes=len(content), parts=parts)
            try:
                preview = preview_import(db, user, content, ImportOptions(bucket_id=row.bucket_id))
            except NoSuchBucket as exc:
                raise HTTPException(404, str(exc)) from exc
            except ImportError_ as exc:
                db.delete(row)
                db.flush()
                log(request, "unreadable", bytes=len(content), parts=parts)
                reason = plain_reason(exc)
                return {"status": "unreadable", "changed": False, "detail": reason,
                        "say": f"The Vault could not read this file as a collection export ({reason}). "
                               "The collection was not changed."}
            row.content, row.filename, row.uploaded_at, row.expires_at = content, file_parts.FILE_NAME, now, now + LIFETIME
            db.execute(delete(StagedUploadPart).where(StagedUploadPart.staged_upload_id == row.id))
            db.execute(delete(StagedUpload).where(StagedUpload.user_id == user.id, StagedUpload.id != row.id,
                                                  StagedUpload.ticket_hash.like(MARKER + "%")))  # one dropped file at a time
            db.flush()
            log(request, "staged", bytes=len(content), rows=preview["rows"], source=preview["source"], parts=parts)
            return staged_payload(db, row, preview)

        return idempotent(request, db, user, 200, run)

    return router


def stage(db: Session, user: User, content: bytes, filename: str, bucket_id: int | None) -> StagedUpload:
    """Store the fetched file for the person: one fetched file at a time (an identical one is the same row), no ticket.

    One decision at a time per person, to the end of this transaction (the same advisory lock the upload links use, so a start
    and a drop are ordered too): without it two simultaneous drops both found no row and each inserted one, which broke "one
    fetched file" and "a duplicate call is the same upload id". Bounded wait, then the usual 503 with Retry-After. Does not commit:
    ``idempotent`` commits the staged row and the stored answer together."""
    db.execute(text(f"SET LOCAL lock_timeout = '{locks.LOCK_TIMEOUT}'"))
    db.execute(select(func.pg_advisory_xact_lock(UPLOAD_LOCK_CLASS, user.id)))
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
    db.flush()  # the id; the caller commits (with the stored answer of its Idempotency-Key), so the advisory lock is held until then
    return kept
