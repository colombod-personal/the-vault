"""Collection files too big for a chat: the assistant asks for a one-time upload link, the person picks the file on a
plain page, the file waits (staged, not imported), the assistant shows the preview, and the person confirms.

- ``POST /api/v1/uploads`` (write): a ticket and the link to give the person. Only the ticket's hash is stored. With
  ``bucket_id`` the upload goes into that bucket only (#124): the link is bound to it, the preview and the apply use it, and
  the page cannot change it. Without, the page offers the person a picker of their own buckets when they have more than one.
- ``GET /upload?ticket=`` and ``POST /upload``: the page. The ticket is the only credential; it works for one hour,
  for this person only, and a new file replaces the staged one (to upload a fixed file).
- ``GET /api/v1/uploads/{id}`` (read): waiting, or the same preview as an import (changes, unmatched rows) with the
  ``content_hash`` of the file previewed.
- ``POST /api/v1/uploads/{id}/apply?content_hash=`` (write): imports the staged file and deletes it, but only if it is the
  file that was previewed (#352: the link can be used again until it expires, so what is stored when apply runs may not be
  what the person was shown; the hash covers the bucket too). Like every import it applies only what changed in the person's
  app and keeps edits made in the Vault (``vault.merge``).
- At most 5 open links per person and 10 started a minute; expired files are deleted by the daily job (``vault.retention``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from typing import Annotated

from dataclasses import replace

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .api.idempotency import idempotent
from .api.import_params import BucketId, answer_options
from .api.schemas import MAX_ID
from .importer import (MAX_UPLOAD_BYTES, ImportConflict, ImportError_, ImportOptions, NoSuchBucket, import_collection,
                       preview_import)
from .models import Bucket, Import, StagedUpload, User
from .oauth_routes import esc, page
from .ratelimit import per_user

V1 = "/api/v1"
LIFETIME = timedelta(hours=1)
MAX_OPEN = 5  # links a person can have open at once
STARTS_PER_MINUTE = 10


def _hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def content_hash(content: bytes, bucket_id: int | None = None) -> str:
    """The file's identity in a preview and in the apply that must match it. Into a bucket (#124) the bucket is part of it: the
    link can be used again, so a changed bucket must not pass for the one that was previewed. For the whole collection it is the
    file's own hash, as it was."""
    if bucket_id is None:
        return hashlib.sha256(content).hexdigest()
    # a hash of the file's hash, so no file whose bytes happen to end like a bucket marker can pass for another scope (#124 review)
    return hashlib.sha256(b"bucket:%d:" % bucket_id + hashlib.sha256(content).digest()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _live(db: Session, ticket: str) -> StagedUpload | None:
    staged = db.scalar(select(StagedUpload).where(StagedUpload.ticket_hash == _hash(ticket[:200])))
    return staged if staged is not None and staged.expires_at > _now() else None


def _preview_html(preview: dict) -> str:
    changes = preview.get("changes") or {}
    rows = "".join(f"<li>Row {esc(u['row'])}: {esc(u['name'])} ({esc(u['set'] or '?')} {esc(u['number'] or '?')})</li>"
                   for u in preview["unmatched"][:20])
    more = preview["unmatched_rows"] - min(20, len(preview["unmatched"]))
    n = lambda count, one, many: f"{count:,} {one if count == 1 else many}"  # noqa: E731
    removed = changes.get("removed", 0)
    warning = (f"<p><strong>Importing this file would remove {n(removed, 'card', 'cards')} "
               f"({n(changes.get('copies_out', 0), 'copy', 'copies')}) from your collection.</strong> "
               "Your app's file says they are gone; edits you made through your assistant are kept.</p>") if removed else ""
    bucket, left = preview.get("bucket"), preview.get("untouched") or {}
    target = (f"<p>Into the bucket <strong>{esc(bucket['name'])}</strong> only: {n(left.get('copies', 0), 'copy', 'copies')} in "
              f"{n(left.get('buckets', 0), 'other bucket', 'other buckets')} are left as they are.</p>") if bucket else ""
    merge = preview.get("merge") or {}
    kept, conflicts = (merge.get("kept_vault_edits") or {}).get("count", 0), (merge.get("conflicts") or {}).get("count", 0)
    kept_note = (f"<p>{n(kept, 'card you edited through your assistant is', 'cards you edited through your assistant are')} "
                 "kept.</p>" if kept else "") + (
        f"<p>{n(conflicts, 'card changed', 'cards changed')} both in your app and through your assistant: your assistant "
        "will ask you which to keep (the edit made here is kept unless you say otherwise).</p>" if conflicts else "")
    return (f"<p>{n(preview['rows'], 'row', 'rows')}, {n(preview['copies'], 'copy', 'copies')} "
            f"({esc(preview['source'])}).</p>" + target + warning + kept_note +
            "<ul>"
            f"<li>Added: {n(changes.get('added', 0), 'card', 'cards')}</li>"
            f"<li>Removed: {n(removed, 'card', 'cards')}</li>"
            f"<li>More copies: {n(changes.get('increased', 0), 'card', 'cards')}; "
            f"fewer copies: {n(changes.get('decreased', 0), 'card', 'cards')}</li>"
            f"<li>Unchanged: {n(changes.get('unchanged', 0), 'card', 'cards')}</li></ul>"
            f"<p>{n(preview['matched_rows'], 'row matches', 'rows match')} a known printing; "
            f"{n(preview['unmatched_rows'], 'row does', 'rows do')} not.</p>"
            + (f"<ul>{rows}</ul>" + (f"<p>and {more} more.</p>" if more > 0 else "") if rows else ""))


UploadId = Annotated[int, Path(ge=1, le=MAX_ID)]  # a bigger number can't exist (and would overflow the column)


def build_router(get_db, current_user, settings) -> APIRouter:
    router = APIRouter()

    def bucket_ref(db: Session, staged: StagedUpload) -> dict | None:
        bucket = db.get(Bucket, staged.bucket_id) if staged.bucket_id is not None else None
        return {"id": bucket.id, "name": bucket.name} if bucket is not None else None

    @router.post(f"{V1}/uploads", tags=["imports"], status_code=201,
                 summary="A one-time link for the person to upload a collection file (staged, not imported); with `bucket_id` "
                         "the file goes into that bucket only")
    def start_upload(request: Request, bucket_id: BucketId = None, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
        per_user(request, "upload start", user.id, STARTS_PER_MINUTE, db)
        if bucket_id is not None and db.scalar(select(Bucket.id).where(Bucket.id == bucket_id, Bucket.user_id == user.id)) is None:
            raise HTTPException(404, "Bucket not found")  # another person's id is the same 404
        db.execute(delete(StagedUpload).where(StagedUpload.expires_at <= _now()))
        open_links = db.scalar(select(func.count()).select_from(StagedUpload).where(StagedUpload.user_id == user.id))
        if open_links >= MAX_OPEN:
            db.commit()
            raise HTTPException(409, f"You already have {MAX_OPEN} open upload links (each works for one hour). Use one of them "
                                "or wait for one to expire.")
        ticket = secrets.token_urlsafe(32)
        staged = StagedUpload(user_id=user.id, ticket_hash=_hash(ticket), expires_at=_now() + LIFETIME,
                              bucket_id=bucket_id, bucket_bound=bucket_id is not None)
        db.add(staged)
        db.commit()
        return {"id": staged.id, "url": f"{settings.base_url}/upload?ticket={ticket}",
                "expires_at": staged.expires_at.isoformat(), "bucket": bucket_ref(db, staged),
                "next": "Give the person the link. When they say they uploaded the file, call get_staged_upload."}

    def owned(db: Session, user: User, upload_id: int) -> StagedUpload:
        staged = db.get(StagedUpload, upload_id)
        if staged is None or staged.user_id != user.id or staged.expires_at <= _now():
            raise HTTPException(404, "No such upload (links work for one hour)")
        return staged

    def expect_bucket(db: Session, staged: StagedUpload, asked: int | None) -> None:
        """``bucket_id`` on a read or an apply is a check, not a choice: the bucket is the one the upload is bound to."""
        if asked is not None and asked != staged.bucket_id:
            bound = bucket_ref(db, staged)
            raise HTTPException(409, "This upload goes into " + (f"the bucket {bound['name']!r} (id {bound['id']})" if bound
                                else "the whole collection, not a bucket") + f", not bucket {asked}")

    @router.get(f"{V1}/uploads/{{upload_id}}", tags=["imports"],
                summary="A staged upload: waiting, or its preview (`bucket_id`, if sent, must be the bucket it is bound to)")
    def get_upload(upload_id: UploadId, bucket_id: BucketId = None, user: User = Depends(current_user),
                   db: Session = Depends(get_db), options: ImportOptions = Depends(answer_options)) -> dict:
        staged = owned(db, user, upload_id)
        expect_bucket(db, staged, bucket_id)
        ref = bucket_ref(db, staged)
        if staged.content is None:
            return {"id": staged.id, "status": "waiting", "expires_at": staged.expires_at.isoformat(), "bucket": ref}
        try:
            preview = preview_import(db, user, staged.content, replace(options, bucket_id=staged.bucket_id))
        except NoSuchBucket as exc:
            raise HTTPException(404, str(exc)) from exc
        except ImportError_ as exc:
            return {"id": staged.id, "status": "unreadable", "filename": staged.filename, "detail": str(exc)}
        return {"id": staged.id, "status": "uploaded", "filename": staged.filename, "bucket": ref,
                "content_hash": content_hash(staged.content, staged.bucket_id), **preview}

    @router.post(f"{V1}/uploads/{{upload_id}}/apply", tags=["imports"], status_code=201,
                 summary="Import a staged upload (applies what changed in the person's app, keeps edits made in the Vault), "
                         "then delete the staged file")
    def apply_upload(request: Request, upload_id: UploadId, bucket_id: BucketId = None, user: User = Depends(current_user),
                     db: Session = Depends(get_db), options: ImportOptions = Depends(answer_options),
                     hash: str = Query(..., alias="content_hash", pattern=r"^[0-9a-f]{64}$",
                                       description="The content_hash of the preview: only that file is imported")):
        def run():
            staged = owned(db, user, upload_id)
            expect_bucket(db, staged, bucket_id)
            if staged.content is None:
                raise HTTPException(409, "Nothing uploaded yet")
            if not hmac.compare_digest(content_hash(staged.content, staged.bucket_id), hash):
                raise HTTPException(409, "The file (or its bucket) was uploaded again after the preview: preview it again, and "
                                    "import only what the person has seen")
            ref = bucket_ref(db, staged)
            try:
                imp: Import = import_collection(db, user, staged.filename or "upload.csv", staged.content,
                                                replace(options, bucket_id=staged.bucket_id))
            except ImportConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except NoSuchBucket as exc:
                raise HTTPException(404, str(exc)) from exc
            except ImportError_ as exc:
                raise HTTPException(400, str(exc)) from exc
            db.delete(staged)
            return {"import_id": imp.id, "rows": imp.rows, "copies": imp.copies, "changes": imp.summary,
                    "merge": (imp.changes or {}).get("merge"), "bucket": ref}

        return idempotent(request, db, user, 201, run)

    @router.get("/upload", include_in_schema=False)
    def upload_page(ticket: str = "", db: Session = Depends(get_db)):
        staged = _live(db, ticket) if ticket else None
        if staged is None:
            return page("Upload link expired", "<h1>This upload link has expired</h1>"
                        "<p>Ask your assistant for a new one. Links work for one hour.</p>", status=404)
        return page("Upload your collection file", (
            "<h1>Upload your collection file</h1><p>Your assistant asked for this file. Nothing is imported yet: "
            "after you upload it, your assistant shows you what would change, and imports it only when you say so.</p>"
            '<form method="post" action="/upload" enctype="multipart/form-data">'
            f'<input type="hidden" name="ticket" value="{esc(ticket)}">' + _where(db, staged) +
            '<p><input type="file" name="file" accept=".csv,.txt,text/csv,text/plain" required></p>'
            "<p><button>Upload</button></p></form>"))

    def _where(db: Session, staged: StagedUpload) -> str:
        """Where the file goes: the bucket the link was made for, or a picker of this person's own buckets (only theirs, only when
        there is a choice), or nothing: the whole collection."""
        if staged.bucket_bound:
            bound = bucket_ref(db, staged)
            return (f"<p>This file goes into the bucket <strong>{esc(bound['name'])}</strong> only; every other bucket is left "
                    "as it is.</p>") if bound else ""
        mine = list(db.scalars(select(Bucket).where(Bucket.user_id == staged.user_id).order_by(Bucket.position, Bucket.id)))
        if len(mine) < 2:
            return ""
        options = "".join(f'<option value="{b.id}"{" selected" if b.id == staged.bucket_id else ""}>{esc(b.name)}</option>'
                          for b in mine)
        return ('<p><label>Where does this file go? <select name="bucket">'
                '<option value="">Whole collection (the default)</option>'
                f"{options}</select></label></p><p>A bucket keeps the rest of your collection as it is.</p>")

    @router.post("/upload", include_in_schema=False)
    async def upload_file(ticket: str = Form(""), bucket: str = Form(""), file: UploadFile = File(...),
                          db: Session = Depends(get_db)):
        staged = _live(db, ticket) if ticket else None
        if staged is None:
            return page("Upload link expired", "<h1>This upload link has expired</h1>"
                        "<p>Ask your assistant for a new one.</p>", status=404)
        chosen, asked = staged.bucket_id, bucket.strip()
        if staged.bucket_bound:  # made for a bucket by the assistant: the page cannot send it elsewhere
            if asked and asked != str(staged.bucket_id):
                return page("Wrong bucket", "<h1>This link is for another bucket</h1><p>This upload link was made for one bucket. "
                            "Ask your assistant for a new link to send the file somewhere else.</p>", status=400)
        else:
            chosen = None
            if asked:  # one of this person's own buckets, nobody else's
                number = int(asked) if asked.isascii() and asked.isdecimal() and len(asked) < 10 else 0
                found = db.scalar(select(Bucket.id).where(Bucket.id == number, Bucket.user_id == staged.user_id)) if number else None
                if found is None:
                    return page("Unknown bucket", "<h1>That bucket was not found</h1><p>Go back and pick one of your buckets, "
                                "or the whole collection.</p>", status=400)
                chosen = found
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return page("File too large", "<h1>The file is too large</h1><p>20 MB at most.</p>", status=413)
        staged.content, staged.filename, staged.uploaded_at = content, (file.filename or "upload.csv")[:255], _now()
        staged.bucket_id = chosen
        db.commit()
        user = db.get(User, staged.user_id)
        try:
            summary = _preview_html(preview_import(db, user, content, ImportOptions(bucket_id=staged.bucket_id)))
        except ImportError_ as exc:
            summary = f"<p>The file could not be read: {esc(exc)}</p>"
        return page("File received", "<h1>File received</h1><p>Nothing has been imported. Go back to your assistant: "
                    "it will show you this and ask before importing.</p>" + summary)

    return router
