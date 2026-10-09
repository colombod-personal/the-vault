"""Tags under ``/api/v1/collection/tags`` (#127, docs/collections.md section 2): a person's own labels on cards.

A tag exists while a card has it (there is nothing to create first: tagging a card makes the tag). It is keyed by the card's oracle
id, so it survives imports. Each assignment says who wrote it (``person`` or ``assistant`` with the app's name): an assistant's tag
is never shown as the person's own. Tagging or untagging more than 25 cards at once is shown first and applied only with confirm.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import tags as T
from ..models import TagAssignment, User
from . import schemas as S
from .hal import link, page_body, paginate
from .idempotency import idempotent

V1 = "/api/v1"
Tag = Annotated[str, Path(pattern=r"^[a-z0-9:-]{1,40}$", description="A tag: lower case letters, digits, '-' and ':'")]


class TagItem(S.Hal):
    tag: str
    cards: int = Field(description="Cards with this tag, owned or not")
    by_source: dict = Field(description="How many of those assignments were written by the person, an assistant or the system")


class TagDetail(TagItem):
    owned_cards: int = Field(description="Of those, cards you have a copy of now; the rest are tagged but not owned")
    assistants: list[str] = Field(description="The apps that wrote this tag on some cards")


class TagPage(S.Page):
    items: list[TagItem]


class TagPatch(BaseModel):
    name: str = Field(min_length=1, max_length=40, description="The new name; a card that already has it keeps one tag")


class CardsIn(BaseModel):
    card_ids: list[str] = Field(min_length=1, max_length=200, description="Ids of printings from /collection/cards (a tag is on "
                                "the card, so every printing of a card gives the same tag)")
    confirm: bool | None = Field(None, description=f"More than {T.CONFIRM_ABOVE} cards is only shown (applied: false) until it "
                                 "is sent again with confirm true")


def build_router(get_db, current_user) -> APIRouter:
    router = APIRouter(prefix=V1 + "/collection/tags", tags=["tags"])

    def checked(tag: str) -> str:
        try:
            return T.normalize(tag)
        except T.TagError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    def summary(db: Session, user: User, names: list[str]) -> dict[str, dict]:
        out = {n: {"cards": 0, "by_source": {"person": 0, "assistant": 0, "system": 0}} for n in names}
        for tag, source, n in db.execute(select(TagAssignment.tag, TagAssignment.source, func.count()).where(
                TagAssignment.user_id == user.id, TagAssignment.tag.in_(names)).group_by(TagAssignment.tag, TagAssignment.source)):
            out[tag]["cards"] += n
            out[tag]["by_source"][source] = n
        return out

    def item(tag: str, counted: dict) -> dict:
        return {"tag": tag, **counted, "_links": {"self": link(f"{V1}/collection/tags/{tag}"),
                                                  "cards": link(f"{V1}/collection/cards?tag={tag}")}}

    @router.get("", response_model=TagPage, summary="Your tags, with how many cards each has")
    def list_tags(request: Request, q: str | None = None, cursor: str | None = None, limit: int | None = None,
                  user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        names = list(db.scalars(select(TagAssignment.tag).where(TagAssignment.user_id == user.id).distinct()))
        if q:
            needle = q.strip().lower()[:40]
            names = [n for n in names if needle in n]
        page, nxt = paginate(names, lambda n: (n,), lambda n: n, cursor=cursor, limit=limit)
        counted = summary(db, user, page)
        return page_body(request, [item(n, counted[n]) for n in page], nxt, len(names), q=q, limit=limit)

    @router.get("/{tag}", response_model=TagDetail, summary="One tag")
    def get_tag(tag: Tag, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        counted = summary(db, user, [tag])[tag]
        if counted["cards"] == 0:
            raise HTTPException(404, "Tag not found")
        ids = T.oracle_ids_for(db, user, tag)
        apps = sorted(set(db.scalars(select(TagAssignment.source_detail).where(
            TagAssignment.user_id == user.id, TagAssignment.tag == tag, TagAssignment.source == "assistant"))) - {None})
        return {**item(tag, counted), "owned_cards": T.owned_count(db, user, ids), "assistants": apps}

    @router.patch("/{tag}", response_model=TagItem, summary="Rename a tag on every card that has it")
    def rename_tag(request: Request, tag: Tag, body: TagPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
        new = checked(body.name)
        if new == tag:
            raise HTTPException(422, "That is already its name")

        def run():
            try:
                T.rename(db, user, tag, new)
            except T.TagError as exc:
                raise HTTPException(exc.status, str(exc)) from None
            return item(new, summary(db, user, [new])[new])

        return idempotent(request, db, user, 200, run)

    @router.delete("/{tag}", summary="Remove a tag from every card (the cards stay)")
    def delete_tag(tag: Tag, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        rows = list(db.scalars(select(TagAssignment).where(TagAssignment.user_id == user.id, TagAssignment.tag == tag)))
        if not rows:
            raise HTTPException(404, "Tag not found")
        for row in rows:
            db.delete(row)
        db.commit()
        return {"deleted": True, "tag": tag, "cards": len(rows), "_links": {"tags": link(f"{V1}/collection/tags")}}

    def resolved(db: Session, user: User, body: CardsIn):
        try:
            return T.resolve(db, user, body.card_ids)
        except T.TagError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    def shown(tag: str, cards: list, **extra) -> dict:
        return {"tag": tag, "cards": [{"id": c[0], "name": c[2]} for c in cards], "count": len(cards), **extra}

    @router.post("/{tag}/cards", summary="Tag cards (a tag exists while a card has it)")
    def tag_cards(request: Request, tag: Tag, body: CardsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        cards = resolved(db, user, body)

        def run():
            if len(cards) > T.CONFIRM_ABOVE and body.confirm is not True:
                return shown(tag, cards, applied=False, note=f"This tags {len(cards)} cards, so it is shown first: send it again "
                             "with confirm true to apply it.")
            who = T.writer_of(db, getattr(request.state, "bearer", None))
            try:
                added, already = T.assign(db, user, tag, cards, who)
            except T.TagError as exc:
                raise HTTPException(exc.status, str(exc)) from None
            return shown(tag, cards, applied=True, added=added, already_tagged=already, written_by=who.source,
                         _links={"tag": link(f"{V1}/collection/tags/{tag}"), "cards": link(f"{V1}/collection/cards?tag={tag}")})

        return idempotent(request, db, user, 200, run)

    @router.post("/{tag}/cards/remove", summary="Take a tag off cards (the cards and their other tags stay)")
    def untag_cards(request: Request, tag: Tag, body: CardsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        cards = resolved(db, user, body)

        def run():
            if len(cards) > T.CONFIRM_ABOVE and body.confirm is not True:
                return shown(tag, cards, applied=False, note=f"This takes the tag off {len(cards)} cards, so it is shown first: "
                             "send it again with confirm true to apply it.")
            removed = T.remove(db, user, tag, [c[1] for c in cards])
            return shown(tag, cards, applied=True, removed=removed, not_tagged=len(cards) - removed)

        return idempotent(request, db, user, 200, run)

    return router
