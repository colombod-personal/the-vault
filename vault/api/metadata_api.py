"""``vault_metadata`` of a card and of a bucket (#127, docs/collections.md section 7, vault/metadata.py).

``GET .../metadata`` shows every namespace and who wrote each; ``PUT .../metadata`` replaces the caller's own namespace (``user``
for the person, ``ai.<app>`` for an assistant) and nothing else; ``DELETE`` removes it. A card's metadata is on the card (its oracle
id, so every printing), kept when the last copy leaves; a bucket's is on the bucket.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import metadata as M
from .. import tags as T
from ..models import Bucket, CardAnnotation, User
from . import schemas as S
from .hal import link
from .idempotency import idempotent

V1 = "/api/v1"
BucketId = Annotated[int, Path(ge=1, le=S.MAX_ID)]
CardId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$", description="A printing's id from /collection/cards")]


class MetadataIn(BaseModel):
    data: dict = Field(description="Replaces your namespace (`user` for the person, `ai.<app>` for an assistant); {} removes it. "
                       f"The whole document is at most {M.MAX_BYTES} bytes and {M.MAX_DEPTH} levels deep")


class MetadataOut(S.Hal):
    version: int
    namespaces: dict = Field(description="Every namespace: `user`, `ai.<app>` per assistant, `system`")
    written: dict = Field(description="Per namespace, who wrote it last and when")
    you_write: str = Field(description="The namespace a PUT or DELETE from this connection changes")


def build_router(get_db, current_user) -> APIRouter:
    router = APIRouter(prefix=V1 + "/collection", tags=["metadata"])

    def writer(request: Request, db: Session) -> tuple[T.Writer, str]:
        who = T.writer_of(db, getattr(request.state, "bearer", None))
        return who, M.namespace_for(who.source, who.detail)

    def answer(doc: dict, namespace: str, links: dict) -> dict:
        try:
            return {**M.view(doc), "you_write": namespace, "_links": links}
        except M.MetadataError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    def card_target(db: Session, user: User, card_id: str) -> str:
        try:
            return T.resolve(db, user, [card_id])[0][1]
        except T.TagError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    def annotation(db: Session, user: User, oracle_id: str, *, lock: bool) -> CardAnnotation | None:
        query = select(CardAnnotation).where(CardAnnotation.user_id == user.id, CardAnnotation.oracle_id == oracle_id)
        return db.scalar(query.with_for_update() if lock else query)

    def save(db: Session, change) -> None:
        try:
            change()
            db.flush()
        except IntegrityError:  # the database's own size and shape check said no
            db.rollback()
            raise HTTPException(413, f"The metadata of one thing is at most {M.MAX_BYTES} bytes in all") from None

    def put(doc: dict, namespace: str, body: MetadataIn, who: T.Writer) -> dict:
        try:
            return M.write(doc, namespace, body.data, by=who.detail or "the person")
        except M.MetadataError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    # -- cards ------------------------------------------------------------------------------------
    def card_links(card_id: str) -> dict:
        return {"self": link(f"{V1}/collection/cards/{card_id}/metadata"), "card": link(f"{V1}/collection/cards/{card_id}")}

    @router.get("/cards/{card_id}/metadata", response_model=MetadataOut, summary="What the person and their assistants keep on a card")
    def get_card_metadata(request: Request, card_id: CardId, user: User = Depends(current_user), db: Session = Depends(get_db)):
        oracle_id = card_target(db, user, card_id)
        row = annotation(db, user, oracle_id, lock=False)
        return answer(row.vault_metadata if row else {"version": M.CURRENT}, writer(request, db)[1], card_links(card_id))

    def write_card(request: Request, card_id: str, body: MetadataIn | None, user: User, db: Session):
        oracle_id = card_target(db, user, card_id)
        who, namespace = writer(request, db)

        def run():
            db.execute(postgresql.insert(CardAnnotation).values(user_id=user.id, oracle_id=oracle_id, source="person",
                                                                vault_metadata={"version": M.CURRENT}).on_conflict_do_nothing())
            row = annotation(db, user, oracle_id, lock=True)
            doc = put(row.vault_metadata, namespace, body or MetadataIn(data={}), who)
            row.source, row.source_detail = who.source, who.detail
            save(db, lambda: setattr(row, "vault_metadata", doc))
            out = answer(doc, namespace, card_links(card_id))
            if set(doc) <= {"version"}:  # nothing left on the card: no empty row kept
                db.delete(row)
            return out

        return idempotent(request, db, user, 200, run)

    @router.put("/cards/{card_id}/metadata", response_model=MetadataOut, summary="Replace your namespace of a card's metadata")
    def put_card_metadata(request: Request, card_id: CardId, body: MetadataIn, user: User = Depends(current_user),
                          db: Session = Depends(get_db)):
        return write_card(request, card_id, body, user, db)

    @router.delete("/cards/{card_id}/metadata", response_model=MetadataOut, summary="Remove your namespace of a card's metadata")
    def delete_card_metadata(request: Request, card_id: CardId, user: User = Depends(current_user), db: Session = Depends(get_db)):
        return write_card(request, card_id, None, user, db)

    # -- buckets ----------------------------------------------------------------------------------
    def bucket_links(bucket_id: int) -> dict:
        return {"self": link(f"{V1}/collection/buckets/{bucket_id}/metadata"), "bucket": link(f"{V1}/collection/buckets/{bucket_id}")}

    def bucket_of(db: Session, user: User, bucket_id: int, *, lock: bool) -> Bucket:
        query = select(Bucket).where(Bucket.id == bucket_id, Bucket.user_id == user.id)
        bucket = db.scalar(query.with_for_update() if lock else query)
        if bucket is None:
            raise HTTPException(404, "Bucket not found")  # another person's id is the same 404
        return bucket

    @router.get("/buckets/{bucket_id}/metadata", response_model=MetadataOut, summary="What the person and their assistants keep on a bucket")
    def get_bucket_metadata(request: Request, bucket_id: BucketId, user: User = Depends(current_user), db: Session = Depends(get_db)):
        return answer(bucket_of(db, user, bucket_id, lock=False).vault_metadata, writer(request, db)[1], bucket_links(bucket_id))

    def write_bucket(request: Request, bucket_id: int, body: MetadataIn | None, user: User, db: Session):
        bucket_of(db, user, bucket_id, lock=False)  # 404 before anything else
        who, namespace = writer(request, db)

        def run():
            bucket = bucket_of(db, user, bucket_id, lock=True)
            doc = put(bucket.vault_metadata, namespace, body or MetadataIn(data={}), who)
            save(db, lambda: setattr(bucket, "vault_metadata", doc))
            return answer(doc, namespace, bucket_links(bucket_id))

        return idempotent(request, db, user, 200, run)

    @router.put("/buckets/{bucket_id}/metadata", response_model=MetadataOut, summary="Replace your namespace of a bucket's metadata")
    def put_bucket_metadata(request: Request, bucket_id: BucketId, body: MetadataIn, user: User = Depends(current_user),
                            db: Session = Depends(get_db)):
        return write_bucket(request, bucket_id, body, user, db)

    @router.delete("/buckets/{bucket_id}/metadata", response_model=MetadataOut, summary="Remove your namespace of a bucket's metadata")
    def delete_bucket_metadata(request: Request, bucket_id: BucketId, user: User = Depends(current_user), db: Session = Depends(get_db)):
        return write_bucket(request, bucket_id, None, user, db)

    return router
