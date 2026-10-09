"""Collection files too big for a chat: the assistant asks for a one-time upload link, the person picks the file on a
plain page, the file waits (staged, not imported), the assistant shows the preview, and the person confirms.

- ``POST /api/v1/uploads`` (write): a ticket and the link to give the person. Only the ticket's hash is stored.
- ``GET /upload?ticket=`` and ``POST /upload``: the page. The ticket is the only credential; it works for one hour,
  for this person only, and a new file replaces the staged one (to upload a fixed file).
- ``GET /api/v1/uploads/{id}`` (read): waiting, or the same preview as an import (changes, unmatched rows).
- ``POST /api/v1/uploads/{id}/apply`` (write): imports the staged file and deletes it. Like every import it applies
  only what changed in the person's app and keeps edits made in the Vault (``vault.merge``).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Request, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .api.idempotency import idempotent
from .api.import_params import import_options
from .api.schemas import MAX_ID
from .importer import MAX_UPLOAD_BYTES, ImportConflict, ImportError_, ImportOptions, import_collection, preview_import
from .models import Import, StagedUpload, User
from .oauth_routes import esc, page

V1 = "/api/v1"
LIFETIME = timedelta(hours=1)


def _hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


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
    merge = preview.get("merge") or {}
    kept, conflicts = (merge.get("kept_vault_edits") or {}).get("count", 0), (merge.get("conflicts") or {}).get("count", 0)
    kept_note = (f"<p>{n(kept, 'card you edited through your assistant is', 'cards you edited through your assistant are')} "
                 "kept.</p>" if kept else "") + (
        f"<p>{n(conflicts, 'card changed', 'cards changed')} both in your app and through your assistant: your assistant "
        "will ask you which to keep (the edit made here is kept unless you say otherwise).</p>" if conflicts else "")
    return (f"<p>{n(preview['rows'], 'row', 'rows')}, {n(preview['copies'], 'copy', 'copies')} "
            f"({esc(preview['source'])}).</p>" + warning + kept_note +
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

    @router.post(f"{V1}/uploads", tags=["imports"], status_code=201,
                 summary="A one-time link for the person to upload a collection file (staged, not imported)")
    def start_upload(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        db.execute(delete(StagedUpload).where(StagedUpload.expires_at <= _now()))
        ticket = secrets.token_urlsafe(32)
        staged = StagedUpload(user_id=user.id, ticket_hash=_hash(ticket), expires_at=_now() + LIFETIME)
        db.add(staged)
        db.commit()
        return {"id": staged.id, "url": f"{settings.base_url}/upload?ticket={ticket}",
                "expires_at": staged.expires_at.isoformat(),
                "next": "Give the person the link. When they say they uploaded the file, call get_staged_upload."}

    def owned(db: Session, user: User, upload_id: int) -> StagedUpload:
        staged = db.get(StagedUpload, upload_id)
        if staged is None or staged.user_id != user.id or staged.expires_at <= _now():
            raise HTTPException(404, "No such upload (links work for one hour)")
        return staged

    @router.get(f"{V1}/uploads/{{upload_id}}", tags=["imports"], summary="A staged upload: waiting, or its preview")
    def get_upload(upload_id: UploadId, user: User = Depends(current_user), db: Session = Depends(get_db),
                   options: ImportOptions = Depends(import_options)) -> dict:
        staged = owned(db, user, upload_id)
        if staged.content is None:
            return {"id": staged.id, "status": "waiting", "expires_at": staged.expires_at.isoformat()}
        try:
            preview = preview_import(db, user, staged.content, options)
        except ImportError_ as exc:
            return {"id": staged.id, "status": "unreadable", "filename": staged.filename, "detail": str(exc)}
        return {"id": staged.id, "status": "uploaded", "filename": staged.filename, **preview}

    @router.post(f"{V1}/uploads/{{upload_id}}/apply", tags=["imports"], status_code=201,
                 summary="Import a staged upload (applies what changed in the person's app, keeps edits made in the Vault), "
                         "then delete the staged file")
    def apply_upload(request: Request, upload_id: UploadId, user: User = Depends(current_user),
                     db: Session = Depends(get_db), options: ImportOptions = Depends(import_options)):
        def run():
            staged = owned(db, user, upload_id)
            if staged.content is None:
                raise HTTPException(409, "Nothing uploaded yet")
            try:
                imp: Import = import_collection(db, user, staged.filename or "upload.csv", staged.content, options)
            except ImportConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except ImportError_ as exc:
                raise HTTPException(400, str(exc)) from exc
            db.delete(staged)
            return {"import_id": imp.id, "rows": imp.rows, "copies": imp.copies, "changes": imp.summary,
                    "merge": (imp.changes or {}).get("merge")}

        return idempotent(request, db, user, 201, run)

    @router.get("/upload", include_in_schema=False)
    def upload_page(ticket: str = "", db: Session = Depends(get_db)):
        if not ticket or _live(db, ticket) is None:
            return page("Upload link expired", "<h1>This upload link has expired</h1>"
                        "<p>Ask your assistant for a new one. Links work for one hour.</p>", status=404)
        return page("Upload your collection file", (
            "<h1>Upload your collection file</h1><p>Your assistant asked for this file. Nothing is imported yet: "
            "after you upload it, your assistant shows you what would change, and imports it only when you say so.</p>"
            '<form method="post" action="/upload" enctype="multipart/form-data">'
            f'<input type="hidden" name="ticket" value="{esc(ticket)}">'
            '<p><input type="file" name="file" accept=".csv,.txt,text/csv,text/plain" required></p>'
            "<p><button>Upload</button></p></form>"))

    @router.post("/upload", include_in_schema=False)
    async def upload_file(ticket: str = Form(""), file: UploadFile = File(...), db: Session = Depends(get_db)):
        staged = _live(db, ticket) if ticket else None
        if staged is None:
            return page("Upload link expired", "<h1>This upload link has expired</h1>"
                        "<p>Ask your assistant for a new one.</p>", status=404)
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return page("File too large", "<h1>The file is too large</h1><p>20 MB at most.</p>", status=413)
        staged.content, staged.filename, staged.uploaded_at = content, (file.filename or "upload.csv")[:255], _now()
        db.commit()
        user = db.get(User, staged.user_id)
        try:
            summary = _preview_html(preview_import(db, user, content))
        except ImportError_ as exc:
            summary = f"<p>The file could not be read: {esc(exc)}</p>"
        return page("File received", "<h1>File received</h1><p>Nothing has been imported. Go back to your assistant: "
                    "it will show you this and ask before importing.</p>" + summary)

    return router
