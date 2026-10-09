"""Deck independence under ``/api/v1/decks/overlap`` (#165, docs/deck-independence.md): can the saved decks all be built at the same
time from the copies owned, which cards are contested, which deck lacks what, and what a copy costs to end the borrowing.

``GET /decks/overlap`` keeps the fields it always had (``decks_checked``, ``shared_cards``, ``short_cards``, ``cards``) and adds
bounded data: ``summary``, ``allocation``, the counts and the first page of ``decks``. The three lists that grow with the number of
decks (``decks``, ``contested``, ``purchases``) are each a cursor-paged subresource of their own; the allocation is computed across
every deck before a page is cut, so the page size never changes who lacks what. ``priority`` (deck ids, in order) replaces the
default order; an id that is not the caller's answers 404, like every deck route.

Registered before the main router so ``/decks/overlap`` is matched before ``/decks/{deck_id}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import deck_independence as di
from ..importer import user_entries
from ..models import Deck, User
from ..ratelimit import per_user
from . import schemas as S
from .hal import link, page_body, paginate, url

V1 = "/api/v1"
OVERLAP = V1 + "/decks/overlap"
RATE = 60  # overlap reads a minute per person: three lists to page, each page recomputes the allocation
ROOT_DECKS = 25  # decks on the root's first page
MAX_PRIORITY = 200
NOTE = ("Basic lands are left out. 'short' is how many more copies you need to have every deck "
        "built at the same time; any printing you own counts.")


def parse_priority(raw: list[str]) -> list[int]:
    """Deck ids from ``priority=3,1,2`` (or repeated ``priority=3&priority=1``), in order, without repeats."""
    ids: list[int] = []
    for part in ",".join(raw).split(","):
        part = part.strip()
        if not part:
            continue
        if not (part.isascii() and part.isdigit()) or len(part) > 12 or len(ids) >= MAX_PRIORITY:
            raise HTTPException(400, f"priority is a list of deck ids, in order (e.g. 3,1,2); '{part[:20]}' is not one, or there are more than {MAX_PRIORITY}")
        if int(part) not in ids:
            ids.append(int(part))
    return ids


def build_router(get_db, current_user) -> APIRouter:
    router = APIRouter(prefix=OVERLAP, tags=["decks"])
    PRIORITY = Query([], description="Deck ids, in the order they take contested copies (comma-separated or repeated). A deck that is not "
                     "yours answers 404. Decks not named follow, the one closest to complete first. Not stored")

    def computed(request: Request, db: Session, user: User, raw_priority: list[str]) -> tuple[di.Analysis, list[int]]:
        per_user(request, "deck independence", user.id, RATE, db)
        priority = parse_priority(raw_priority)
        decks = list(db.scalars(select(Deck).where(Deck.user_id == user.id).order_by(Deck.name)))
        if priority:
            mine = {d.id for d in decks}
            if any(i not in mine for i in priority):  # another person's deck and a missing one are indistinguishable
                raise HTTPException(404, "No saved deck with that id in priority (list_decks shows yours)")
        return di.analyse(db, user.id, decks, user_entries(db, user), priority), priority

    def deck_item(d: dict) -> dict:
        return {**d, "_links": {"self": link(f"{V1}/decks/{d['id']}")}}

    def extras(a: di.Analysis) -> dict:
        return {"allocation": a.allocation, "prices_date": a.prices_date}

    def keep(priority: list[int], limit: int | None) -> dict:
        return {"limit": limit, "priority": ",".join(map(str, priority)) or None}

    @router.get("", response_model=S.DeckOverlap, response_model_by_alias=True,
                summary="Cards in more than one saved deck, and whether each deck can be built at the same time as the others from the copies you own")
    def deck_overlap(request: Request, limit: int | None = Query(None, ge=1, le=500, description=f"Decks on the first page (default {ROOT_DECKS})"),
                     priority: list[str] = PRIORITY, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        a, order = computed(request, db, user, priority)
        page, nxt = paginate(a.decks, lambda d: (d["order"],), lambda d: d["id"], cursor=None, limit=limit or ROOT_DECKS)
        own = keep(order, limit)
        links = {"self": link(url(request, OVERLAP, **own)),
                 "decks": link(url(request, OVERLAP + "/decks", **keep(order, None))),
                 "contested": link(url(request, OVERLAP + "/contested", **keep(order, None))),
                 "purchases": link(url(request, OVERLAP + "/purchases", **keep(order, None)))}
        if nxt:
            links["next"] = link(url(request, OVERLAP + "/decks", **own, cursor=nxt))
        return {"decks_checked": a.decks_checked, **a.legacy, "note": NOTE, "decks_analysed": a.decks_analysed,
                "decks_skipped_count": a.decks_skipped_count, "summary": a.summary, **extras(a),
                "decks": [deck_item(d) for d in page], "_links": links}

    @router.get("/decks", response_model=S.OverlapDeckPage, response_model_by_alias=True,
                summary="The per-deck answer, one record per saved deck in allocation order (paged)")
    def overlap_decks(request: Request, cursor: str | None = None, limit: int | None = None, priority: list[str] = PRIORITY,
                      user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        a, order = computed(request, db, user, priority)
        page, nxt = paginate(a.decks, lambda d: (d["order"],), lambda d: d["id"], cursor=cursor, limit=limit)
        return {**page_body(request, [deck_item(d) for d in page], nxt, len(a.decks), **keep(order, limit)), **extras(a)}

    @router.get("/contested", response_model=S.OverlapContestedPage, response_model_by_alias=True,
                summary="The contested cards: some copies are owned, fewer than the decks need together (paged)")
    def overlap_contested(request: Request, cursor: str | None = None, limit: int | None = None, priority: list[str] = PRIORITY,
                          user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        a, order = computed(request, db, user, priority)
        page, nxt = paginate(a.contested, lambda c: (-c["global_deficit"], c["card"].lower()), lambda c: c["card"].lower(),
                             cursor=cursor, limit=limit)
        return {**page_body(request, page, nxt, len(a.contested), **keep(order, limit)), **extras(a)}

    @router.get("/purchases", response_model=S.OverlapPurchasePage | S.OverlapPurchaseText, response_model_by_alias=True,
                summary="What to buy so every deck can be built at the same time, cheapest first (paged); format=text is the paste-ready list")
    def overlap_purchases(request: Request, cursor: str | None = None, limit: int | None = None,
                          format: str = Query("json", pattern="^(json|text)$", description="`text`: one page of the paste-ready list "
                                              "(`<copies> <card>` per line); fetch pages until there is no `next` and join them"),
                          priority: list[str] = PRIORITY, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        a, order = computed(request, db, user, priority)
        page, nxt = paginate(a.purchases, lambda p: (p["cost"] is None, p["cost"] or 0.0, p["card"].lower()),
                             lambda p: p["card"].lower(), cursor=cursor, limit=limit)
        as_text = format == "text"
        body = page_body(request, page, nxt, len(a.purchases), **keep(order, limit), format="text" if as_text else None)
        if as_text:  # one chunk of the paste-ready list instead of the rows
            del body["items"]
            body.update(format="text", text="\n".join(f"{p['global_deficit']} {p['card']}" for p in page))
        return {**body, **extras(a)}

    return router
