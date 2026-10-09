"""Reset the collection under ``/api/v1/collection/reset`` (#129, docs/collections.md): the whole inventory or one bucket.

``POST /collection/reset`` without ``confirmation`` previews (changes nothing) and returns a confirmation signed over exactly that;
with it, resets. ``POST /collection/reset/undo`` and ``GET /collection/reset`` are the undo of the latest reset (kept 7 days).
Every route needs the write scope, the preview included: it is the first step of a destructive action, and it is not listed in
``READ_ONLY_POSTS`` (tests/test_collection_reset.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import collection_reset, tokens
from ..config import Settings
from ..importer import ImportConflict, NoSuchBucket
from ..models import User
from ..ratelimit import per_user
from . import schemas as S
from .idempotency import idempotent

V1 = "/api/v1"


class ResetIn(BaseModel):
    bucket_id: int | None = Field(None, ge=1, le=S.MAX_ID, description="Reset this bucket only (from /collection/buckets); omit for "
                                  "the whole inventory. Another person's id is a 404")
    keep_tags: bool = Field(True, description="Keep the tags and notes (they are yours and stay, shown as not owned). false clears "
                            "them: for a bucket, those of the cards that no longer exist anywhere in the inventory")
    keep_history: bool = Field(True, description="Keep the import history. false clears it (whole inventory only)")
    no_undo: bool = Field(False, description="Keep no snapshot: the reset cannot be undone. Needed when the snapshot would be over "
                          f"{collection_reset.MAX_SNAPSHOT_BYTES // 1048576} MB")
    confirmation: str | None = Field(None, min_length=8, max_length=600, description="From the preview the person agreed to; "
                                     "without it the call only previews")


class UndoIn(BaseModel):
    confirm: bool = Field(False, description="true restores the removed copies; without it the call only shows what it would restore")


def build_router(get_db, current_user, settings: Settings) -> APIRouter:
    router = APIRouter(prefix=V1 + "/collection/reset", tags=["reset"])

    def options_of(body: ResetIn) -> collection_reset.ResetOptions:
        return collection_reset.ResetOptions(body.bucket_id, body.keep_tags, body.keep_history, body.no_undo)

    def failing(exc: Exception) -> HTTPException:
        if isinstance(exc, NoSuchBucket):
            return HTTPException(404, str(exc))
        if isinstance(exc, ImportConflict):
            return HTTPException(409, str(exc))
        return HTTPException(exc.status, str(exc))  # a ResetError

    @router.post("", summary="Reset the collection (the whole inventory, or one bucket): without a confirmation, preview what it "
                 "would remove; with the preview's confirmation, do it. Kept 7 days for an undo")
    def reset(request: Request, body: ResetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        per_user(request, "reset", user.id, 10, db)
        options = options_of(body)
        if not body.confirmation:
            try:
                return collection_reset.preview(db, user, options, settings.session_secret)
            except (NoSuchBucket, collection_reset.ResetError) as exc:
                raise failing(exc) from exc
        label = tokens.app_label(db, getattr(request.state, "bearer", None))

        def run():
            try:
                return collection_reset.apply(db, user, options, body.confirmation, settings.session_secret, label)
            except (NoSuchBucket, ImportConflict, collection_reset.ResetError) as exc:
                raise failing(exc) from exc

        return idempotent(request, db, user, 200, run)

    @router.get("", summary="The reset that can be undone: what it removed, when the undo ends, and whether it can be done now (404 if none)")
    def get_reset(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        answer = collection_reset.summary_of(db, user)
        if answer is None:
            raise HTTPException(404, "There is no reset to undo (a reset can be undone for 7 days)")
        return answer

    @router.post("/undo", summary="Undo the latest reset: without confirm, show what it would put back; with confirm true, restore it")
    def undo(request: Request, body: UndoIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        per_user(request, "reset", user.id, 10, db)
        label = tokens.app_label(db, getattr(request.state, "bearer", None))
        if not body.confirm:
            try:
                return collection_reset.undo(db, user, confirm=False, label=label)
            except collection_reset.ResetError as exc:
                raise failing(exc) from exc

        def run():
            try:
                return collection_reset.undo(db, user, confirm=True, label=label)
            except collection_reset.ResetError as exc:
                raise failing(exc) from exc

        return idempotent(request, db, user, 200, run)

    return router
