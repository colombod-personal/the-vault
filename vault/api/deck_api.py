"""Deck analysis under ``/api/v1/decks``: statistics, legality, upgrade candidates, the validator that
checks a proposed list of changes, and the shopping list. All computed from the stored catalog by
``vault.deck_tools``; no model is involved. Each answer is labelled ``computed`` (by the Vault) and
lists the sources and versions it used as ``inputs`` (``vault.provenance``).

These work on a decklist the caller sends; nothing is stored. The shopping list also reads the
caller's own collection (scoped to them, like every other collection endpoint).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from mtg_toolkits import delta
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import catalog_queries as q
from .. import combos
from .. import deck_tools as dt
from .. import provenance as prov
from ..importer import user_entries
from ..models import User
from .catalog_api import throttle
from .hal import link

V1 = "/api/v1"
DECK = Field(min_length=1, max_length=50_000, description="The decklist, one card per line, e.g. '1 Sol Ring'; Commander cards under a 'Commander' header")
FORMAT = Field(max_length=20, description=f"One of: {', '.join(dt.FORMATS)}")
DECK_LIMIT = 30  # analyses a minute per person


class DeckIn(BaseModel):
    text: str = DECK


class FormatIn(DeckIn):
    format: str = FORMAT


class UpgradesIn(FormatIn):
    budget_usd: float = Field(ge=0, le=100_000, description="The most any single added card may cost")
    roles: list[str] | None = Field(default=None, max_length=len(dt.ROLE_TAGS), description=f"Roles to search ({', '.join(dt.ROLE_TAGS)}); default: the roles the deck is short of")
    limit: int = Field(default=10, ge=1, le=dt.MAX_CANDIDATES)


class ChangesIn(FormatIn):
    adds: list[str] = Field(default_factory=list, max_length=60, description="Card names to add (one copy each)")
    cuts: list[str] = Field(default_factory=list, max_length=60, description="Card names to cut (one copy each)")
    budget_usd: float | None = Field(default=None, ge=0, le=100_000, description="The most the adds may cost in total")


class Answer(BaseModel):
    result: dict
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


def build_router(get_db, current_user, settings, transport=None) -> APIRouter:
    router = APIRouter(prefix=V1 + "/decks", tags=["decks"])

    def answer(db: Session, what: str, result: dict, inputs: tuple[str, ...], path: str) -> dict:
        loaded = q.sources(db)
        used = [n for n in inputs if n in loaded]
        return {"result": result, "provenance": [prov.computed(what, q.provenance_for(db, *used), as_of=date.today())],
                "_links": {"self": link(f"{V1}/decks/{path}")}}

    def prepared(request: Request, db: Session, user: User, text: str) -> dt.Resolved:
        throttle(request, settings, "deck analysis", f"user:{user.id}", DECK_LIMIT)
        if "oracle_cards" not in q.sources(db):
            raise HTTPException(503, "The card catalog has not been loaded yet, so this cannot be computed.")
        try:
            return dt.resolve(db, dt.parse(text))
        except dt.DeckError as exc:
            raise HTTPException(400, str(exc)) from exc

    def failing(exc: dt.DeckError):
        raise HTTPException(400, str(exc)) from exc

    @router.post("/stats", response_model=Answer, response_model_by_alias=True,
                 summary="Counts, curve, color identity, roles and estimated cost of a decklist")
    def deck_stats(request: Request, body: DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, body.text)
        return answer(db, "deck statistics", dt.stats(db, resolved), ("oracle_cards", "oracle_tags", "oracle_prices"), "stats")

    @router.post("/legality", response_model=Answer, response_model_by_alias=True,
                 summary="Is the decklist legal in a format? Lists every issue, and what was not checked")
    def deck_legality(request: Request, body: FormatIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, body.text)
        try:
            result = dt.legality(resolved, body.format)
        except dt.DeckError as exc:
            failing(exc)
        return answer(db, "legality check", result, ("oracle_cards",), "legality")

    @router.post("/upgrades", response_model=Answer, response_model_by_alias=True,
                 summary="Upgrade candidates within a budget: legal, in the deck's colors, not already in it")
    def deck_upgrades(request: Request, body: UpgradesIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, body.text)
        loaded = q.sources(db)
        if "oracle_prices" not in loaded or "oracle_tags" not in loaded:
            raise HTTPException(503, "Prices and role tags have not been loaded yet, so upgrade candidates cannot be computed.")
        try:
            result = dt.find_upgrades(db, resolved, body.format, body.budget_usd, body.roles, body.limit)
        except dt.DeckError as exc:
            failing(exc)
        return answer(db, "upgrade candidates", result, ("oracle_cards", "oracle_tags", "oracle_prices"), "upgrades")

    @router.post("/validate-changes", response_model=Answer, response_model_by_alias=True,
                 summary="Check a proposed list of cuts and adds: legality, colors, resulting deck, and budget")
    def deck_validate(request: Request, body: ChangesIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        prepared(request, db, user, body.text)
        try:
            result = dt.validate_changes(db, body.text, body.format, body.adds, body.cuts, body.budget_usd)
        except dt.DeckError as exc:
            failing(exc)
        return answer(db, "validation of proposed changes", result, ("oracle_cards", "oracle_prices"), "validate-changes")

    @router.post("/combos", response_model=Answer, response_model_by_alias=True,
                 summary="Combos in a decklist, and those one card short (asked of Commander Spellbook on demand)")
    def deck_combos(request: Request, body: DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, body.text)
        names = {e.name for e in resolved.played()}
        commanders = [e.name for e in resolved.section("commander")]
        main = [(e.name, e.line.quantity) for e in resolved.played() if e.line.section != "commander"]
        try:
            results = combos.ask(main, commanders, transport)
        except combos.ComboServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        out = combos.summarize(results, names)
        spellbook = prov.source("Commander Spellbook", origin="combos written by its community", url="https://commanderspellbook.com",
                                as_of=date.today(), wizards_material=True)
        return {"result": out, "provenance": [spellbook, prov.computed("combo lookup", [spellbook], as_of=date.today())],
                "_links": {"self": link(f"{V1}/decks/combos")}}

    @router.post("/shopping-list", response_model=Answer, response_model_by_alias=True,
                 summary="The cards of a decklist you do not own, with cheapest known prices, as a paste-ready list")
    def deck_shopping(request: Request, body: DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, body.text)
        deck = dt.parse(body.text)
        owned = [r.to_collection_entry() for r in user_entries(db, user)]
        by_name = {e.line.name.strip().lower(): e.card for e in resolved.entries if e.card}
        prices = dt.prices_of(db, [c.oracle_id for c in by_name.values()])
        lines, total, unpriced = [], 0.0, 0
        for c in delta.coverage(deck.to_entries(), owned):
            if c.missing <= 0:
                continue
            card = by_name.get(c.entry.name.strip().lower())
            price = prices.get(card.oracle_id) if card else None
            unit = price.usd if price else None
            if unit is None:
                unpriced += 1
            else:
                total += unit * c.missing
            lines.append({"name": card.name if card else c.entry.name, "quantity": c.missing, "unit_price_usd": unit,
                          "price_date": price.day.isoformat() if price else None, "known_card": card is not None})
        result = {"lines": lines, "text": "\n".join(f"{l['quantity']} {l['name']}" for l in lines),
                  "total_usd": round(total, 2), "unpriced_lines": unpriced,
                  "notes": ["Prices are the cheapest priced paper printing, from Scryfall (sourced from TCGplayer and Cardmarket), not any store's price today.",
                            "Paste the text into a store's own list or deck tool (for example Card Kingdom's Deck Builder); check how it matches names. The Vault does not contact stores or fill carts."]}
        return answer(db, "shopping list", result, ("oracle_cards", "oracle_prices"), "shopping-list")

    return router
