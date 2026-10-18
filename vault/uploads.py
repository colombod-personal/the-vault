"""Collection files too big for a chat: the assistant asks for an upload link (good for one hour, not single-use), the person
picks the file on a plain page, the file waits (staged, not imported), the assistant shows the preview, and the person confirms.

- ``POST /api/v1/uploads`` (any scope, read-only included: it imports nothing, vault.app.READ_ONLY_POSTS): a ticket and the
  link to give the person. Only the ticket's hash is stored. With
  ``bucket_id`` the upload goes into that bucket only (#124): the link is bound to it, the preview and the apply use it, and
  the page cannot change it. Without, the page offers the person a picker of their own buckets when they have more than one.
- ``GET /upload?ticket=`` and ``POST /upload``: the page. The ticket is the only credential; it works for one hour,
  for this person only, and a new file replaces the staged one.
- ``GET /api/v1/uploads/{id}`` (read): waiting, or the same preview as an import (changes, unmatched rows) with the
  ``content_hash`` of the file previewed.
- ``POST /api/v1/uploads/{id}/apply?content_hash=`` (write): imports the staged file and deletes it, but only if it is the
  file that was previewed (#352: the link can be used again until it expires, so what is stored when apply runs may not be
  what the person was shown; the hash covers the bucket too). Like every import it applies only what changed in the person's
  app and keeps edits made in the Vault (``vault.merge``).
- At most 5 open links per person: a sixth replaces the oldest one that holds no file (never one that holds a file; when all five
  do, 409 listing them); 10 started a minute (a retry makes another link and uses a slot); each file posted to
  the page counts against its own per-link allowance (``import_limit`` a minute for each link, 429, not the person's /imports
  counter); expired files are deleted by the daily
  job (``vault.retention``). A link is not single-use: it works until it expires and a second file replaces the first. Links
  are not attributed to an app and there is no list or cancel yet (a read-only app can hold the five and use the starts of a
  minute, the same as a write app could: docs/mcp-oauth-threat-model.md, "The upload link for a read-only connection").
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from typing import Annotated

from dataclasses import replace

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from . import config, locks
from .api.idempotency import idempotent
from .api.import_params import BucketId, answer_options
from .api.schemas import MAX_ID
from .importer import (ImportConflict, ImportError_, ImportOptions, NoSuchBucket, import_collection,
                       preview_import)
from .models import Bucket, Import, StagedUpload, User
from .oauth_routes import esc, page
from .ratelimit import per_user

V1 = "/api/v1"
LIFETIME = timedelta(hours=1)
MAX_OPEN = 5  # links a person can have open at once
STARTS_PER_MINUTE = 10
INTAKE_MARKER = "intake-"  # the start of the ticket_hash of a file the chat app handed over (vault/intake.py): no link, not counted as one
UPLOAD_LOCK_CLASS = 7301  # pg_advisory_xact_lock(class, user id): one start at a time per person (a number no other lock uses)


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
    why = {"not_found": "not in the card catalog yet, kept as written", "later": "matched by the daily update"}
    ruled = "".join(f"<li>Row {esc(r['row'])}: {esc(r['name'])} ({esc(r['set'] or '?')} {esc(r['number'] or '?')}): "
                    f"{esc(r['range'])}</li>" for r in [x for x in (preview.get("found_by_rule") or []) if x.get("candidates", 1) > 1][:20])
    rows = "".join(f"<li>Row {esc(u['row'])}: {esc(u['name'])} ({esc(u['set'] or '?')} {esc(u['number'] or '?')}): "
                   f"{esc(why.get(u.get('why'), 'kept as written'))}</li>" for u in preview["unmatched"][:20])
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
            f"<p>{esc(preview.get('matching_line') or '')}</p>"
            + (f"<details><summary>Cards that could be several printings (nothing for you to do)</summary>"
               f"<ul>{ruled}</ul></details>" if ruled else "")
            + (f"<details><summary>{n(preview['unmatched_rows'], 'row', 'rows')} without a printing yet "
               "(nothing for you to do)</summary>"
               f"<ul>{rows}</ul>" + (f"<p>and {more} more.</p>" if more > 0 else "") + "</details>" if rows else ""))


def _supersede_oldest_waiting(db: Session, user_id: int) -> int | None:
    """At the cap of open links: remove the person's oldest link that holds no file, and return its id (``None`` when every
    link holds one). One statement, so what is checked is what is removed: the ``content IS NULL`` test is made on the row
    under its lock, so a file that was committed a moment earlier keeps its link, and one that is being written (its row is
    locked) is skipped for the next oldest. The removed row answers exactly as an expired one does (the page and
    ``/uploads/{id}`` both say no such link), so the replacement tells nothing about the old one to whoever holds its ticket.
    The caller commits the removal together with the new link."""
    oldest = (select(StagedUpload.id).where(StagedUpload.user_id == user_id, StagedUpload.content.is_(None),
                                            StagedUpload.ticket_hash.not_like(INTAKE_MARKER + "%"))  # a file in parts is not a link
              .order_by(StagedUpload.expires_at, StagedUpload.id).limit(1).with_for_update(skip_locked=True).scalar_subquery())
    gone = db.execute(delete(StagedUpload).where(StagedUpload.id == oldest, StagedUpload.content.is_(None),
                                                 StagedUpload.user_id == user_id)
                      .returning(StagedUpload.id).execution_options(synchronize_session=False)).first()
    return gone[0] if gone else None


def _hm(when: datetime | None) -> str:
    return when.astimezone(timezone.utc).strftime("%H:%M UTC") if when is not None else "?"


def _held_files(db: Session, user_id: int) -> str:
    """The person's own links with a file uploaded through them, for the plain refusal: the assistant has no list, and a new chat
    has no ids. Size and time only, never the file's name: whoever holds a link's ticket chose that name, and the sentence is read
    by assistants (get_staged_upload answers it as data, in its own field)."""
    rows = db.execute(select(StagedUpload.id, func.length(StagedUpload.content).label("size"), StagedUpload.uploaded_at,
                             StagedUpload.expires_at)
                      .where(StagedUpload.user_id == user_id, StagedUpload.content.is_not(None),
                             StagedUpload.ticket_hash.not_like(INTAKE_MARKER + "%"))  # a dropped file is not a link
                      .order_by(StagedUpload.expires_at, StagedUpload.id)).all()
    return "; ".join(f"id {r.id} ({r.size:,} bytes, uploaded {_hm(r.uploaded_at)}, link ends {_hm(r.expires_at)})" for r in rows)


UploadId = Annotated[int, Path(ge=1, le=MAX_ID)]  # a bigger number can't exist (and would overflow the column)


def build_router(get_db, current_user, settings) -> APIRouter:
    router = APIRouter()

    def bucket_ref(db: Session, staged: StagedUpload) -> dict | None:
        bucket = db.get(Bucket, staged.bucket_id) if staged.bucket_id is not None else None
        return {"id": bucket.id, "name": bucket.name} if bucket is not None else None

    @router.post(f"{V1}/uploads", tags=["imports"], status_code=201,
                 summary="A link, good for one hour, for the person to upload a collection file (staged, not imported); with `bucket_id` "
                         "the file goes into that bucket only")
    def start_upload(request: Request, bucket_id: BucketId = None, user: User = Depends(current_user),
                     db: Session = Depends(get_db)) -> dict:
        per_user(request, "upload start", user.id, STARTS_PER_MINUTE, db)  # commits: the transaction below starts after it
        if bucket_id is not None and db.scalar(select(Bucket.id).where(Bucket.id == bucket_id, Bucket.user_id == user.id)) is None:
            raise HTTPException(404, "Bucket not found")  # another person's id is the same 404
        # One start at a time per person, to the end of this transaction: the count, the replacement and the insert are then
        # one decision. Without it, six simultaneous starts at three open links all counted three and left six rows, and two
        # at one waiting link and four held files could both find the same waiting row (one then answered a false 409).
        # Bounded wait (vault.locks: 5 s, then the usual 503 with Retry-After, as for every other lock in the Vault): a stalled
        # transaction of this person's must not hold a request and a database connection for as long as it likes. SET LOCAL ends
        # with the transaction, so it also bounds the row waits below.
        db.execute(text(f"SET LOCAL lock_timeout = '{locks.LOCK_TIMEOUT}'"))
        db.execute(select(func.pg_advisory_xact_lock(UPLOAD_LOCK_CLASS, user.id)))
        db.execute(delete(StagedUpload).where(StagedUpload.expires_at <= _now()))
        open_links = db.scalar(select(func.count()).select_from(StagedUpload).where(
            StagedUpload.user_id == user.id, StagedUpload.ticket_hash.not_like(INTAKE_MARKER + "%")))  # links only: a dropped file has its own row
        replaced = None
        if open_links >= MAX_OPEN:
            replaced = _supersede_oldest_waiting(db, user.id)
            if replaced is None:  # every link has a file uploaded through it: never replace one of those
                held = _held_files(db, user.id)
                db.commit()
                raise HTTPException(409, f"You already have {MAX_OPEN} open upload links and a file has been uploaded through "
                                    "each one (by the person, or by the app that made the link, which holds the link's address), so "
                                    "none can be replaced. The files are not imported and nothing is lost. To see what one would "
                                    "change, call get_staged_upload with an id listed here; import one with confirm_staged_upload "
                                    "only if the person recognises it and says yes. Otherwise the links end on their own (one hour "
                                    "from when each was made); there is no discard yet. Uploaded: " + held)
        ticket = secrets.token_urlsafe(32)
        staged = StagedUpload(user_id=user.id, ticket_hash=_hash(ticket), expires_at=_now() + LIFETIME,
                              bucket_id=bucket_id, bucket_bound=bucket_id is not None)
        db.add(staged)
        db.commit()  # the replacement and the new link are one transaction: if the insert fails, the old link is still there
        answer = {"id": staged.id, "url": f"{settings.base_url}/upload?ticket={ticket}",
                  "expires_at": staged.expires_at.isoformat(), "bucket": bucket_ref(db, staged),
                  "next": "Give the person the link. When they say they uploaded the file, call get_staged_upload: its "
                          "matching_line says how the rows matched, and nothing in it is for the person to fix."}
        if replaced is not None:
            answer["replaced_upload_id"] = replaced
            answer["next"] += (f" The person had {MAX_OPEN} open links, so the oldest one that held no file (id {replaced}) no "
                               "longer works: use this link only.")
        return answer

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
    async def upload_file(request: Request, ticket: str = Form(""), bucket: str = Form(""), file: UploadFile = File(...),
                          db: Session = Depends(get_db)):
        staged = _live(db, ticket) if ticket else None
        if staged is None:
            return page("Upload link expired", "<h1>This upload link has expired</h1>"
                        "<p>Ask your assistant for a new one.</p>", status=404)
        # Plain values, read once: the session commits below (the rate counter) and the link may be replaced meanwhile, so no
        # attribute of the row is read after this point.
        staged_id, owner_id, bound, bound_bucket = staged.id, staged.user_id, staged.bucket_bound, staged.bucket_id
        # Up to IMPORT_MAX_BYTES of parsing and matching, limited to IMPORT_LIMIT a minute per link, on a counter of its own: the shared
        # per-person "import" counter counts refused tries too, so whoever still holds a link (an app the person has since
        # disconnected) could loop here and keep the person's own /imports at 429. A made-up ticket was refused above and is
        # counted nowhere. A person is bounded by 5 links x IMPORT_LIMIT, and a link that is being looped is replaced by a new one.
        try:
            per_user(request, "upload page", staged_id, settings.import_limit, db, kind="ticket")
        except HTTPException as exc:
            limited = page("Too many uploads", "<h1>Too many uploads just now</h1><p>Wait a minute, then upload the file "
                           "again with the same link. If this keeps happening, ask your assistant for a new link.</p>", status=429)
            limited.headers["Retry-After"] = (exc.headers or {}).get("Retry-After", "60")
            return limited
        chosen, asked = bound_bucket, bucket.strip()
        if bound:  # made for a bucket by the assistant: the page cannot send it elsewhere
            if asked and asked != str(bound_bucket):
                return page("Wrong bucket", "<h1>This link is for another bucket</h1><p>This upload link was made for one bucket. "
                            "Ask your assistant for a new link to send the file somewhere else.</p>", status=400)
        else:
            chosen = None
            if asked:  # one of this person's own buckets, nobody else's
                number = int(asked) if asked.isascii() and asked.isdecimal() and len(asked) < 10 else 0
                found = db.scalar(select(Bucket.id).where(Bucket.id == number, Bucket.user_id == owner_id)) if number else None
                if found is None:
                    return page("Unknown bucket", "<h1>That bucket was not found</h1><p>Go back and pick one of your buckets, "
                                "or the whole collection.</p>", status=400)
                chosen = found
        limit = config.import_max_bytes()
        content = await file.read(limit + 1)
        if len(content) > limit:
            return page("File too large", f"<h1>The file is too large</h1><p>{config.size_text(limit)} at most, and about 4 MB on "
                        "mtgvault.cards, where the hosting platform refuses a bigger request first.</p>", status=413)
        # One conditional UPDATE, not an attribute write: the link may have been replaced (a sixth link at the cap removes the
        # oldest one that held no file, see _supersede_oldest_waiting) or may have expired while the file was being read. If no
        # row matched, the file is not kept and the person is told so, never "File received".
        stored = db.execute(update(StagedUpload).where(StagedUpload.id == staged_id, StagedUpload.expires_at > _now())
                            .values(content=content, filename=(file.filename or "upload.csv")[:255], uploaded_at=_now(),
                                    bucket_id=chosen).execution_options(synchronize_session=False))
        if stored.rowcount != 1:
            db.rollback()
            return page("Upload link expired", "<h1>This upload link has expired</h1>"
                        "<p>Ask your assistant for a new one.</p>", status=404)
        db.commit()
        user = db.get(User, owner_id)
        try:
            summary = _preview_html(preview_import(db, user, content, ImportOptions(bucket_id=chosen)))
        except ImportError_ as exc:
            summary = f"<p>The file could not be read: {esc(exc)}</p>"
        return page("File received", "<h1>File received</h1><p>Nothing has been imported. Go back to your assistant: "
                    "it will show you this and ask before importing.</p>" + summary)

    return router
