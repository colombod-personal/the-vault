"""Buckets under ``/api/v1/collection/buckets`` (#123, docs/collections.md): the places a person's copies live in.

A bucket per Dragon Shield folder and an "Unsorted" one are made by the importer; a person can also make their own, rename them,
and delete one that is empty (copies are moved out first, by an explicit move). ``entries.folder`` is never rewritten: it is the
source file's word for the place, kept so the CSV round-trip stays byte-identical, and the bucket is the Vault's grouping of it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import bucket_moves
from ..models import Bucket, Entry, User
from . import schemas as S
from .hal import link, page_body, paginate
from .idempotency import idempotent

V1 = "/api/v1"
MAX_MADE = 100  # buckets a person creates by hand; the ones made from a file's folders are not counted (docs/collections.md)
Id = Annotated[int, Path(ge=1, le=S.MAX_ID)]


class BucketIn(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="The bucket's name; unique per person, ignoring case")


class BucketPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    position: int | None = Field(None, ge=0, le=100_000, description="Where it sits in the list")


class BucketItem(S.Hal):
    id: int
    name: str
    kind: str = Field(description="default (Unsorted), folder (from a file's folder) or made (by a person)")
    position: int
    copies: int = Field(description="Copies in this bucket; the buckets add up to the inventory")
    entries: int = Field(description="Rows (a printing, finish, condition and language each) in this bucket")
    vault_metadata: dict
    created_at: str


class BucketPage(S.Page):
    items: list[BucketItem]


class MoveLine(BaseModel):
    card_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$", description="The id of a printing, from /collection/cards?bucket=")
    quantity: int = Field(ge=1, le=100_000, description="How many copies to move")


class MoveIn(BaseModel):
    to: int = Field(ge=1, le=S.MAX_ID, description="The bucket that takes the copies")
    lines: list[MoveLine] = Field(min_length=1, max_length=50)
    confirm: bool | None = Field(None, description=f"A move of more than {bucket_moves.CONFIRM_ABOVE} copies is only shown "
                                 "(applied: false) until it is sent again with confirm true")


def build_router(get_db, current_user) -> APIRouter:
    router = APIRouter(prefix=V1 + "/collection/buckets", tags=["buckets"])

    def counts(db: Session, user: User, ids: list[int]) -> dict[int, tuple[int, int]]:
        rows = db.execute(select(Entry.bucket_id, func.coalesce(func.sum(Entry.quantity), 0), func.count())
                          .where(Entry.user_id == user.id, Entry.bucket_id.in_(ids)).group_by(Entry.bucket_id)).all()
        return {bucket_id: (int(copies), int(n)) for bucket_id, copies, n in rows}

    def item(b: Bucket, counted: dict[int, tuple[int, int]]) -> dict:
        copies, rows = counted.get(b.id, (0, 0))
        return {"id": b.id, "name": b.name, "kind": b.kind, "position": b.position, "copies": copies, "entries": rows,
                "vault_metadata": b.vault_metadata, "created_at": b.created_at.isoformat(),
                "_links": {"self": link(f"{V1}/collection/buckets/{b.id}"), "collection": link(f"{V1}/collection")}}

    def owned(db: Session, user: User, bucket_id: int) -> Bucket:
        bucket = db.scalar(select(Bucket).where(Bucket.id == bucket_id, Bucket.user_id == user.id))
        if bucket is None:
            raise HTTPException(404, "Bucket not found")  # another person's id is the same 404
        return bucket

    def named(db: Session, user: User, name: str, *, besides: int | None = None) -> Bucket | None:
        query = select(Bucket).where(Bucket.user_id == user.id, func.lower(Bucket.name) == name.lower())
        if besides is not None:
            query = query.where(Bucket.id != besides)
        return db.scalar(query)

    @router.get("", response_model=BucketPage, summary="The places your copies are grouped in")
    def list_buckets(request: Request, cursor: str | None = None, limit: int | None = None,
                     user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(Bucket).where(Bucket.user_id == user.id)))
        page, nxt = paginate(rows, lambda b: (b.position, b.id), lambda b: b.id, cursor=cursor, limit=limit)
        counted = counts(db, user, [b.id for b in page])
        return page_body(request, [item(b, counted) for b in page], nxt, len(rows), limit=limit)

    @router.get("/{bucket_id}", response_model=BucketItem)
    def get_bucket(bucket_id: Id, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        bucket = owned(db, user, bucket_id)
        return item(bucket, counts(db, user, [bucket.id]))

    @router.post("", response_model=BucketItem, status_code=201, summary="Make a bucket")
    def create_bucket(request: Request, body: BucketIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "A bucket needs a name")

        def run():
            if named(db, user, name) is not None:
                raise HTTPException(409, f"You already have a bucket called {name!r} (names ignore case)")
            made = db.scalar(select(func.count()).select_from(Bucket).where(Bucket.user_id == user.id, Bucket.kind == "made"))
            if made >= MAX_MADE:
                raise HTTPException(409, f"You can make at most {MAX_MADE} buckets by hand; delete one first")
            top = db.scalar(select(func.coalesce(func.max(Bucket.position), -1)).where(Bucket.user_id == user.id))
            bucket = Bucket(user_id=user.id, name=name, kind="made", position=top + 1)
            db.add(bucket)
            try:
                db.flush()
            except IntegrityError:  # the same name made twice at once: the unique index decides
                db.rollback()
                raise HTTPException(409, f"You already have a bucket called {name!r} (names ignore case)") from None
            return item(bucket, {})

        return idempotent(request, db, user, 201, run)

    @router.patch("/{bucket_id}", response_model=BucketItem, summary="Rename a bucket or move it in the list")
    def patch_bucket(bucket_id: Id, body: BucketPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
        bucket = owned(db, user, bucket_id)
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(422, "A bucket needs a name")
            if named(db, user, name, besides=bucket.id) is not None:
                raise HTTPException(409, f"You already have a bucket called {name!r} (names ignore case)")
            bucket.name = name  # the Vault's label only: entries.folder, and so the export, is not rewritten
        if body.position is not None:
            bucket.position = body.position
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "That name is taken (names ignore case)") from None
        return item(bucket, counts(db, user, [bucket.id]))

    @router.delete("/{bucket_id}", summary="Delete an empty bucket")
    def delete_bucket(bucket_id: Id, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        bucket = owned(db, user, bucket_id)
        held = db.scalar(select(func.count()).select_from(Entry).where(Entry.bucket_id == bucket.id, Entry.user_id == user.id))
        if held:
            # Copies leave a bucket by an explicit move, which rewrites their folder and is recorded as a change (docs/collections.md):
            # deleting does not move them behind anyone's back.
            raise HTTPException(409, f"{bucket.name!r} still holds {held} rows of copies: move them to another bucket first")
        db.delete(bucket)
        db.commit()
        return {"deleted": True, "_links": {"buckets": link(f"{V1}/collection/buckets")}}

    @router.post("/{bucket_id}/move", summary="Move copies from this bucket to another (rewrites their folder; recorded as a change)")
    def move(request: Request, bucket_id: Id, body: MoveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        source = owned(db, user, bucket_id)
        target = owned(db, user, body.to)
        if target.id == source.id:
            raise HTTPException(422, "to must be a different bucket")

        def run():
            try:
                pieces, asked = bucket_moves.plan(db, user, source, [(l.card_id, l.quantity) for l in body.lines])
            except bucket_moves.MoveError as exc:
                raise HTTPException(exc.status, str(exc)) from None
            copies = sum(p.take for p in pieces)
            names = {}
            for p in pieces:
                names[p.entry.name] = names.get(p.entry.name, 0) + p.take
            shown = {"from": {"id": source.id, "name": source.name}, "to": {"id": target.id, "name": target.name},
                     "copies": copies, "cards": [{"name": n, "copies": c} for n, c in sorted(names.items())]}
            if copies > bucket_moves.CONFIRM_ABOVE and body.confirm is not True:
                return {**shown, "applied": False, "note": f"This moves {copies} copies, so it is shown first: send it again with "
                        "confirm true to apply it. It rewrites their folder to the target's name and is recorded as a change."}
            imp = bucket_moves.apply(db, user, source, target, pieces, None)
            return {**shown, "applied": True, "change_set": imp.id,
                    "_links": {"source": link(f"{V1}/collection/buckets/{source.id}"), "target": link(f"{V1}/collection/buckets/{target.id}"),
                               "import": link(f"{V1}/imports/{imp.id}")}}

        return idempotent(request, db, user, 200, run)

    return router
