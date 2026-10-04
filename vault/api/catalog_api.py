"""Catalog lookups under ``/api/v1/catalog``: a card, its rulings, a rule, a search of the rules, a
check that a quote is verbatim, and which data versions the Vault holds.

Every answer carries ``provenance`` (a list of blocks, ``vault.provenance``): the material is
Wizards' and Scryfall's, shown as theirs. Answers are small and capped, by the card or rule asked
about: there is no way to list or download the catalog (docs/compliance.md). Signed-in people (or
personal access tokens) can use it; ``PUBLIC_CATALOG=1`` lets anyone, rate-limited per IP.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import catalog_queries as q
from .. import provenance as prov
from ..models import User
from ..ratelimit import WINDOW, client_ip, hit
from .hal import link

V1 = "/api/v1"
Name = Annotated[str, Query(min_length=1, max_length=300, description="Exact card name (either face of a double-faced card)")]
Quote = Annotated[str, Field(min_length=1, max_length=4000)]


class CardOut(BaseModel):
    card: dict | None = None
    suggestions: list[str] = Field(default_factory=list, description="Close names, when the name was not found exactly")
    tags: list[dict] = Field(default_factory=list, description="Scryfall Tagger tags (community opinion, not rules)")
    rulings_total: int = 0
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RulingsOut(BaseModel):
    oracle_id: str
    rulings: list[dict]
    total: int
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RuleOut(BaseModel):
    version: str | None
    rule: dict | None
    subrules: list[dict] = Field(default_factory=list)
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RuleSearchOut(BaseModel):
    version: str | None
    query: str
    results: list[dict]
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class CitationIn(BaseModel):
    kind: str = Field(pattern="^(rule|oracle_text|ruling)$", description="What the quote is from")
    ref: str = Field(min_length=1, max_length=300, description="A rule number (or glossary:Term), or a card name or Oracle id")
    quote: Quote
    version: str | None = Field(default=None, max_length=10, description="Comprehensive Rules version (YYYY-MM-DD); default latest")


class CitationOut(BaseModel):
    verified: bool
    detail: dict
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


MAX_STEPS = 12
MAX_CITED = 4


class StepIn(BaseModel):
    text: str = Field(min_length=1, max_length=700, description="What happens at this step, in your own words")
    rules: list[str] = Field(default_factory=list, max_length=MAX_CITED, description="Rule numbers this step relies on, e.g. '603.3b'")


class WalkthroughIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    cards: list[str] = Field(default_factory=list, max_length=8, description="The cards involved")
    steps: list[StepIn] = Field(min_length=1, max_length=MAX_STEPS)
    version: str | None = Field(default=None, max_length=10, description="Rules edition YYYY-MM-DD; default latest")


class WalkthroughOut(BaseModel):
    title: str | None
    cards: list[str]
    version: str | None
    steps: list[dict]
    unknown_rules: list[dict]
    note: str
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class StatusOut(BaseModel):
    sources: dict[str, dict]
    rules_version: str | None
    provenance: list[prov.Provenance]
    notice: str = prov.FAN_CONTENT_NOTICE
    links: dict = Field(default_factory=dict, alias="_links")


def throttle(request: Request, settings, bucket: str, who: str, allowed: int) -> None:
    """Count one call for ``who`` ("user:7" or "ip:1.2.3.4") in ``bucket``; 429 over ``allowed`` a minute.
    Counted in the database like the sign-in limits, under a keyed hash, so the rows name no one."""
    minute = int(time.time() // WINDOW)
    key = hmac.new(settings.session_secret.encode(), f"{bucket}:{who}".encode(), hashlib.sha256).hexdigest()
    with request.app.state.db.sessions() as db:
        count = hit(db, key, minute)
        db.commit()
    if count > allowed:
        raise HTTPException(429, f"Too many {bucket} requests. Try again in a minute.",
                            headers={"Retry-After": str(max(1, math.ceil((minute + 1) * WINDOW - time.time())))})


class WhoamiOut(BaseModel):
    user: dict
    auth: str = Field(description="How this request is authenticated: 'session' (web) or 'bearer' (an access or personal access token)")
    scopes: list[str]
    data: StatusOut
    guidance: str
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


def build_router(get_db, optional_user, current_user, settings) -> APIRouter:
    router = APIRouter(prefix=V1 + "/catalog", tags=["catalog"])
    agent = APIRouter(prefix=V1 + "/agent", tags=["agents"])

    def access(request: Request, user: User | None = Depends(optional_user)) -> User | None:
        """Signed-in people, or anyone when PUBLIC_CATALOG is on; either way at most CATALOG_RATE_LIMIT a minute."""
        if user is None and not settings.public_catalog:
            raise HTTPException(401, "Sign in (or send a personal access token) to use the catalog.",
                                headers={"WWW-Authenticate": 'Bearer realm="the-vault"'})
        who = f"user:{user.id}" if user else f"ip:{client_ip(request, settings)}"
        throttle(request, settings, "catalog", who, settings.catalog_rate_limit)
        return user

    def status_body(db: Session) -> dict:
        rows = q.sources(db)
        out = {n: {"version": s.version, "as_of": s.fetched_at.date().isoformat(), "rows": s.rows} for n, s in rows.items()}
        return {"sources": out, "rules_version": q.latest_rules_version(db),
                "provenance": q.provenance_for(db, *[n for n in prov.CATALOG_SOURCES if n in rows]),
                "_links": {"self": link(f"{V1}/catalog/status")}}

    @router.get("/status", response_model=StatusOut, response_model_by_alias=True,
                summary="Which data versions the Vault holds (rules edition, card data, rulings, tags, prices)")
    def status(user=Depends(access), db: Session = Depends(get_db)) -> dict:
        return status_body(db)

    @agent.get("/whoami", response_model=WhoamiOut, response_model_by_alias=True,
               summary="Who this connection is, its scopes, and which data versions the Vault holds")
    def whoami(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        bearer = request.headers.get("authorization", "")[:7].lower() == "bearer "
        data = status_body(db)
        return {"user": {"id": user.id, "name": user.name}, "auth": "bearer" if bearer else "session",
                "scopes": sorted(request.state.scopes - {"account"}), "data": data, "provenance": data["provenance"],
                "guidance": "Answer rules and card questions from the tools, quote only verified text, and repeat each result's provenance. "
                            "Material from Scryfall and Wizards of the Coast is theirs, not the Vault's.",
                "_links": {"self": link(f"{V1}/agent/whoami"), "catalog": link(f"{V1}/catalog/status")}}

    @router.get("/cards", response_model=CardOut, response_model_by_alias=True,
                summary="A card's Oracle text, types, legalities and tags, by exact name or Oracle id")
    def card(name: str | None = Query(default=None, min_length=1, max_length=300),
             oracle_id: str | None = Query(default=None, min_length=36, max_length=36),
             user=Depends(access), db: Session = Depends(get_db)) -> dict:
        if not name and not oracle_id:
            raise HTTPException(422, "Give a name or an oracle_id")
        found, suggestions = q.find_card(db, name=name, oracle_id=oracle_id)
        if found is None and not suggestions:
            raise HTTPException(404, "No card with that name")
        sources = ("oracle_cards", "oracle_tags") if found else ("oracle_cards",)
        body = {"card": q.card_body(found) if found else None, "suggestions": [c.name for c in suggestions],
                "provenance": q.provenance_for(db, *sources), "_links": {"self": link(f"{V1}/catalog/cards")}}
        if found:
            _, total = q.rulings_for(db, found.oracle_id, 1)
            body |= {"tags": q.card_tags(db, found.oracle_id), "rulings_total": total}
            body["_links"]["rulings"] = link(f"{V1}/catalog/cards/{found.oracle_id}/rulings")
            price = db.get(q.OraclePrice, found.oracle_id)
            if price is not None:  # the cheapest priced paper printing, today's figure (no history for cards nobody owns)
                body["price"] = {"usd": price.usd, "usd_foil": price.usd_foil, "eur": price.eur, "as_of": price.day.isoformat(),
                                 "source": price.source, "printing": "the cheapest priced paper printing"}
                body["provenance"] += q.provenance_for(db, "oracle_prices")
        return body

    @router.post("/walkthrough", response_model=WalkthroughOut, response_model_by_alias=True,
                 summary="Present a step-by-step explanation with each cited rule looked up and attached verbatim")
    def walkthrough(body: WalkthroughIn, user=Depends(access), db: Session = Depends(get_db)) -> dict:
        version = body.version or q.latest_rules_version(db)
        steps, unknown = [], []
        for i, step in enumerate(body.steps, 1):
            cited = []
            for number in step.rules:
                rule, _, _ = q.get_rule(db, number, version)
                if rule is None:
                    unknown.append({"step": i, "rule": number})
                else:
                    cited.append({"number": rule["number"], "text": rule["text"]})
            steps.append({"n": i, "text": step.text, "rules": cited})
        return {"title": body.title, "cards": body.cards, "version": version, "steps": steps, "unknown_rules": unknown,
                "note": "Step text is written by the assistant; rule texts are Wizards' Comprehensive Rules, looked up by the Vault. "
                        "Steps citing an unknown rule number are flagged, not trusted.",
                "provenance": q.provenance_for(db, "rules"), "_links": {"self": link(f"{V1}/catalog/walkthrough")}}

    @router.get("/cards/{oracle_id}/rulings", response_model=RulingsOut, response_model_by_alias=True,
                summary="A card's rulings (Wizards' text via Scryfall), newest first, at most 25")
    def rulings(oracle_id: Annotated[str, Path(min_length=36, max_length=36)], limit: int = Query(default=25, ge=1, le=q.MAX_RULINGS),
                user=Depends(access), db: Session = Depends(get_db)) -> dict:
        if db.get(q.OracleCard, oracle_id) is None:
            raise HTTPException(404, "No card with that Oracle id")
        items, total = q.rulings_for(db, oracle_id, limit)
        return {"oracle_id": oracle_id, "rulings": items, "total": total, "provenance": q.provenance_for(db, "rulings"),
                "_links": {"self": link(f"{V1}/catalog/cards/{oracle_id}/rulings")}}

    @router.get("/rules/search", response_model=RuleSearchOut, response_model_by_alias=True,
                summary="Search the Comprehensive Rules (best matches first, at most 10)")
    def rules_search(qs: str = Query(alias="q", min_length=2, max_length=200), limit: int = Query(default=5, ge=1, le=q.MAX_RULE_RESULTS),
                     version: str | None = Query(default=None, max_length=10), user=Depends(access),
                     db: Session = Depends(get_db)) -> dict:
        results, used = q.search_rules(db, qs, version, limit)
        return {"version": used, "query": qs, "results": results, "provenance": q.provenance_for(db, "rules"),
                "_links": {"self": link(f"{V1}/catalog/rules/search")}}

    @router.get("/rules/{number}", response_model=RuleOut, response_model_by_alias=True,
                summary="One rule by number (e.g. 613.1a) or glossary term (glossary:Trample), with its subrules")
    def rule(number: Annotated[str, Path(min_length=1, max_length=120)], version: str | None = Query(default=None, max_length=10),
             user=Depends(access), db: Session = Depends(get_db)) -> dict:
        body, children, used = q.get_rule(db, number, version)
        if body is None:
            raise HTTPException(404, "No such rule in that edition")
        return {"version": used, "rule": body, "subrules": children, "provenance": q.provenance_for(db, "rules"),
                "_links": {"self": link(f"{V1}/catalog/rules/{number}")}}

    @router.post("/verify-citation", response_model=CitationOut, response_model_by_alias=True,
                 summary="Is this quote verbatim in the rule, Oracle text or ruling it is attributed to?")
    def verify(body: CitationIn, user=Depends(access), db: Session = Depends(get_db)) -> dict:
        result = q.verify_citation(db, body.kind, body.ref, body.quote, body.version)
        names = {"rule": "rules", "oracle_text": "oracle_cards", "ruling": "rulings"}
        return {"verified": bool(result.pop("verified")), "detail": result,
                "provenance": q.provenance_for(db, names[body.kind]),
                "_links": {"self": link(f"{V1}/catalog/verify-citation")}}

    root = APIRouter()
    root.include_router(router)
    root.include_router(agent)
    return root
