"""The deck ideas lab under ``/api/v1/decks/{id}/ideas`` (#163, docs/deck-ideas-lab-design.md): what the collection covers of a
saved deck, what is missing or borrowed, and for a card which owned card could stand in.

Read-only and the person's own account only: another person's deck answers 404, and there is no route under ``/shared/{id}/...``
(decks and collections are separate share kinds, and the allocation is computed from the caller's own saved decks). Every number is
computed in ``vault.deck_ideas`` from the allocation of ``vault.deck_independence``; nothing here asks a model.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog_queries as q
from .. import combos, deck_ideas as di, deck_overview
from .. import deck_tools as dt
from .. import provenance as prov
from ..models import Deck, User
from ..ratelimit import per_user
from ..sharing import owned_deck
from . import schemas as S
from .hal import link, page_body, paginate, url

V1 = "/api/v1"
RATE = 120  # reads a minute per person: each lane and each card's alternatives is a request, and each recomputes the allocation
ALTERNATIVES_PAGE = 25
Id = Annotated[int, Path(ge=1, le=S.MAX_ID)]
LaneName = Literal[di.LANES]
FormatName = Literal[dt.FORMATS]
USED = ("oracle_cards", "oracle_tags", "oracle_prices")
NO_ROLE = ("The Vault knows no role for this card yet, so it cannot look for cards that do the same job. That is not the same as "
           "'you own nothing like it'.")
NONE_FOUND = "You own nothing else that does this job in this deck's colours and format."
NO_SAME_JOB = ("You own nothing else that does exactly this job in this deck's colours and format; the cards listed are similar, with a "
               "difference, and each says what differs.")
USED_BY_ALTERNATIVES = ("oracle_cards", "oracle_prices")  # the roles are the Vault's own rules over the Oracle text: no tags


def filtered_note(counts: dict, colours: list[str], fmt: str) -> str | None:
    """What the colour, format and copy filters removed, in a sentence (nothing is hidden: the cards are counted, not named)."""
    bits = []
    if counts.get("colour_identity"):
        bits.append(f"{counts['colour_identity']} outside this deck's colour identity ({''.join(colours) or 'colourless'})")
    if counts.get("format"):
        bits.append(f"{counts['format']} not legal in {fmt}")
    if counts.get("in_deck"):
        bits.append(f"{counts['in_deck']} already in the deck up to the format's limit, or held only by this deck")
    return f"Owned cards that do this job but were not offered: {', '.join(bits)}." if bits else None


def build_router(get_db, current_user, transport=None) -> APIRouter:
    router = APIRouter(prefix=V1 + "/decks/{deck_id}/ideas", tags=["decks"])

    def loaded(request: Request, db: Session, user: User, deck_id: int):
        """The deck (404 unless it is the caller's), all their saved decks, the deck matched to the catalog, the catalog's sources."""
        per_user(request, "deck ideas", user.id, RATE, db)
        deck = owned_deck(db, user, deck_id)
        sources = q.sources(db)
        if "oracle_cards" not in sources:
            raise HTTPException(503, "The card catalog has not been loaded yet, so this cannot be computed.")
        decks = list(db.scalars(select(Deck).where(Deck.user_id == user.id).order_by(Deck.name)))
        try:
            resolved = dt.resolve(db, dt.parse(deck.text))
        except dt.DeckError as exc:
            raise HTTPException(400, str(exc)) from exc
        return deck, decks, resolved, sources

    def deck_block(deck: Deck, resolved: dt.Resolved) -> dict:
        """Which deck an answer is about (#216): its name first, then format, commander(s), card count and colour identity."""
        known = {e.line.name.lower(): list(e.card.color_identity or []) for e in resolved.section("commander") if e.card}
        out = {"id": deck.id, "name": deck.name, "overview": deck_overview.overview(deck.text, deck.format, known)}
        credit = deck_overview.archidekt_credit(deck)
        if credit:
            out["credit"] = credit
            deck_overview.refresh_note(deck, out["overview"])
        return out

    def provenance(sources: dict, what: str, *extra: prov.Provenance, used: tuple = USED) -> list[prov.Provenance]:
        """Scryfall's card data, roles (Scryfall Tagger) and prices are the inputs; the Vault computes the allocation and the match."""
        inputs = [prov.for_catalog(n, sources.get(n)) for n in used if n in sources] + list(extra)
        return [prov.computed(what, inputs, as_of=date.today())]

    @router.get("", response_model=S.DeckIdeas, response_model_by_alias=True,
                summary="A saved deck in role lanes: what the collection covers, what is missing, what another deck holds")
    def deck_ideas(request: Request, deck_id: Id,
                   lane: LaneName | None = Query(None, description="Only this lane (ramp, draw, removal, sweeper, counterspell, tutor, recursion, "
                                                 "sacrifice_outlet, other, lands): the one to page with `cursor`"),
                   limit: int | None = Query(None, ge=1, le=100, description=f"Cards a lane shows on a page (default {di.LANE_PAGE})"),
                   cursor: str | None = Query(None, max_length=500, description="`_links.next` of one lane (needs `lane`)"),
                   include_combos: bool = Query(False, description="Also ask Commander Spellbook for the deck's combos (lines between its "
                                                "cards). The deck's card names are sent to Commander Spellbook only when this is true"),
                   user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        if cursor and not lane:
            raise HTTPException(400, "A cursor pages one lane: give `lane` as well")
        deck, decks, resolved, sources = loaded(request, db, user, deck_id)
        built = di.ideas(db, user.id, deck, decks, resolved)
        size = limit or di.LANE_PAGE
        path = request.url.path
        lanes = []
        for entry in built["lanes"]:
            if lane and entry["lane"] != lane:
                continue
            items = entry.pop("all")
            page, nxt = paginate(items, lambda r: (di.STATUS_ORDER[r["status"]], r["card"].lower()), lambda r: r["card"].lower(),
                                 cursor=cursor if lane else None, limit=size)
            own = {"lane": entry["lane"], "limit": limit}
            links = {"self": link(url(request, path, **own, cursor=cursor if lane else None))}
            if nxt:
                links["next"] = link(url(request, path, **own, cursor=nxt))
            lanes.append({**entry, "items": page, "count": len(page), "total": len(items), "next_cursor": nxt, "_links": links})
        extra, combo_block = [], None
        if include_combos:
            combo_block = {"checked": False, "reason": "Commander Spellbook could not be asked"}
            try:
                found = combos.ask([(e.name, e.line.quantity) for e in resolved.played() if e.line.section != "commander"],
                                   [e.name for e in resolved.section("commander")], transport)
                combo_block = di.combo_edges(found, built["rows"])
                extra.append(prov.source("Commander Spellbook", origin="combos written by its community",
                                         url="https://commanderspellbook.com", as_of=date.today(), wizards_material=True))
            except combos.ComboServiceError as exc:  # down, slow, rate-limited or breaker open: the rest of the answer stands
                combo_block = {"checked": False, "reason": str(exc)}
        base = f"{V1}/decks/{deck.id}"
        return {"deck": deck_block(deck, resolved), "summary": built["summary"], "allocation": built["allocation"],
                "roles_note": di.ROLES_NOTE, "lanes_note": di.LANE_NOTE, "borrow_note": di.BORROW_NOTE, "lanes": lanes,
                "combos": combo_block, "prices_date": built["prices_date"],
                "provenance": provenance(sources, "deck ideas: what the collection covers of the deck", *extra),
                "_links": {"self": link(url(request, path, lane=lane, limit=limit, include_combos="true" if include_combos else None)),
                           "deck": link(base), "alternatives": link(f"{base}/ideas/alternatives", title="Add ?card=NAME (and format)"),
                           "overlap": link(f"{V1}/decks/overlap"), "purchases": link(f"{V1}/decks/overlap/purchases")}}

    @router.get("/alternatives", response_model=S.DeckAlternatives, response_model_by_alias=True,
                summary="Owned cards that do the same job as a card in this deck (or a similar one, with the difference said), in the deck's colours and format")
    def deck_alternatives(request: Request, deck_id: Id,
                          card: str = Query(min_length=1, max_length=300, description="The card to find a stand-in for (it need not be in the deck)"),
                          format: FormatName | None = Query(None, description="The format whose legality and copy limit apply (default: the "
                                                            "format set on the deck, else commander)"),
                          limit: int | None = Query(None, ge=1, le=100, description=f"Alternatives on a page (default {ALTERNATIVES_PAGE})"),
                          cursor: str | None = Query(None, max_length=500),
                          user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck, decks, resolved, sources = loaded(request, db, user, deck_id)
        target, close = q.find_card(db, name=card)
        if target is None:
            hint = f" Did you mean: {', '.join(c.name for c in close[:3])}?" if close else ""
            raise HTTPException(404, f"No card named {card[:80]!r} in the card catalog.{hint}")
        stored = (deck.format or "").lower()
        fmt, origin = (format, "request") if format else (stored, "deck") if stored in dt.FORMATS else ("commander", "default")
        found = di.alternatives(db, user.id, deck, decks, resolved, target, fmt)
        rows = found["rows"]
        page, nxt = paginate(rows, lambda r: r["_sort"], lambda r: r["oracle_id"], cursor=cursor, limit=limit or ALTERNATIVES_PAGE)
        for r in page:
            r.pop("_sort")
            r["_links"] = {"scryfall": link(r["scryfall_uri"])} if r["scryfall_uri"] else {}
        buy, price_day = di.price_page(db, user.id, page, target)
        message = (NO_ROLE if found["reason"] == "no_role" else NONE_FOUND if found["reason"] == "none_found"
                   else NO_SAME_JOB if not found["tiers"]["same_job"] else None)
        body = page_body(request, page, nxt, len(rows), card=card, format=format, limit=limit)
        return {**body, "deck": deck_block(deck, resolved), "card": {**found["target"], "buy": buy}, "format": fmt, "format_from": origin,
                "color_identity": found["color_identity"], "reason": found["reason"], "message": message, "tiers": found["tiers"],
                "filtered_out": found["filtered_out"], "filtered_note": filtered_note(found["filtered_out"], found["color_identity"], fmt),
                "roles_version": found["roles_version"], "roles_note": di.ALT_ROLES_NOTE, "prices_date": price_day,
                "provenance": provenance(sources, f"owned cards that do the same job as a card in a deck: the Vault's own rules over the Oracle text, "
                                         f"version {found['roles_version']}", used=USED_BY_ALTERNATIVES),
                "_links": {**body["_links"], "deck": link(f"{V1}/decks/{deck.id}"), "ideas": link(f"{V1}/decks/{deck.id}/ideas"),
                           **({"scryfall": link(target.scryfall_uri)} if target.scryfall_uri else {}),
                           "purchases": link(f"{V1}/decks/overlap/purchases")}}

    return router
