"""Catalog lookups under ``/api/v1/catalog``: a card, its rulings, a rule, a search of the rules, a
check that a quote is verbatim, and which data versions the Vault holds.

Every answer carries ``provenance`` (a list of blocks, ``vault.provenance``): the material is
Wizards' and Scryfall's, shown as theirs. Answers are small and capped, by the card or rule asked
about: there is no way to list or download the catalog (docs/compliance.md). Only signed-in people (or their tokens)
can use it, rate-limited: there is no anonymous access. An anonymous card-data API would be a proxy of Scryfall's data,
which Scryfall's terms forbid; every answer here serves a person's collection, decks or question (owner decision
2026-10-06, #62).
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import catalog_queries as q
from .. import card_roles, deck_tools, limited_data, role_rules, rules_changes
from .. import provenance as prov
from ..models import User
from ..ratelimit import WINDOW, hit
from ..rules_live import LiveRules, RulesUnavailable
from .hal import link
from .schemas import CardRolesOut, RoleVocabulary

V1 = "/api/v1"
Name = Annotated[str, Query(min_length=1, max_length=300, description="Exact card name (either face of a double-faced card)")]
Quote = Annotated[str, Field(min_length=1, max_length=4000)]


class CardOut(BaseModel):
    card: dict | None = None
    suggestions: list[str] = Field(default_factory=list, description="Close names, when the name was not found exactly")
    tags: list[dict] = Field(default_factory=list, description="Scryfall Tagger tags (community opinion, not rules)")
    rulings_total: int = 0
    computed_roles: list[dict] = Field(default_factory=list, description="Roles the Vault worked out from this card's Oracle text where Scryfall's "
                                       "Tagger has no tag for the role: role, rule, what the rule matches, what it is known to get wrong. "
                                       "Computed by the Vault, not Scryfall's tags, and not facts about the card")
    legality_changes: list[dict] = Field(default_factory=list, description="Recorded changes of this card's legality (bans, unbans, restrictions), "
                                         "newest first: format, old, new, observed_on. Empty when none was recorded")
    legality_changes_note: str | None = Field(default=None, description="What the recorded changes are and are not")
    price: dict | None = Field(default=None, description="Cheapest priced paper printing, today's figure: usd, usd_foil, eur, as_of, source")
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RulingsOut(BaseModel):
    oracle_id: str
    rulings: list[dict]
    total: int
    offset: int = 0
    next_offset: int | None = None
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RuleOut(BaseModel):
    version: str | None
    rule: dict | None
    subrules: list[dict] = Field(default_factory=list)
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RuleOutlineOut(BaseModel):
    version: str
    under: dict | None
    items: list[dict]
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RuleTermOut(BaseModel):
    version: str
    term: str
    glossary: dict | None
    rules: list[dict]
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RuleSearchOut(BaseModel):
    version: str | None
    query: str
    matched: str = Field(default="all words", description="'all words' (every word is in each rule) or 'any word' (nothing had all of them, so rules with some of them are listed best first)")
    results: list[dict]
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class RulesChangesOut(BaseModel):
    current: dict = Field(description="The current edition: version (its 'effective as of' date), file_date (the date in its file name), url, in_force")
    previous: dict | None = Field(description="The edition before it, in the same shape; null when none was found (see `note`)")
    rules: dict | None = Field(description="The change brief: counts, then added, removed, renumbered, shifted and changed rules in rule "
                                           "order, capped at `limit`, with the first changed sentence of each changed rule and a tally by "
                                           "subsection. Null when no previous edition was found")
    rulings: dict = Field(description="Rulings published since `since`: total, how many each source wrote, and the newest `limit`")
    legality: dict = Field(description="Legality changes the Vault recorded since `since`: total and the newest `limit`, with what they are not")
    since: str = Field(description="The first day of the rulings and legality window (YYYY-MM-DD)")
    note: str | None = None
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


class LimitedOut(BaseModel):
    attribution: str = Field(description="17Lands' CC BY 4.0 credit for these figures, with the set, format and dates: repeat it in the first sentence that uses a number")
    set: str
    format: str
    platform: str = Field(description="Always 'MTG Arena': the data is from Arena players who run the 17Lands tracker, not from paper Magic")
    data_window: dict = Field(description="The games and drafts the counts cover (first and last time), the set's baseline win rate and the version (date, ETag) of each file read")
    sort: str | None = Field(description="The metric the list is sorted by; null when `cards` named the cards")
    total: int = Field(description="Cards in the whole list (those at or above the sample floor for the sort, or the cards asked for)")
    count: int
    cards: list[dict] = Field(description="Per card: games in hand with its win rate and 95% range, opening-hand, drawn and played figures, where it is last seen and taken, "
                                          "and `sample` (level too_few, low or ok, the number behind it and the exact warning)")
    not_found: list[str] = Field(default_factory=list, description="Names asked for that are not in this set's data")
    left_out: dict = Field(description="How many cards were left out of the sorted list for being under the sample floor, and the sentence that says so")
    caveats: list[str]
    next_cursor: str | None = None
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


class StatusOut(BaseModel):
    sources: dict[str, dict]
    rules_version: str | None
    provenance: list[prov.Provenance]
    notice: str = prov.FAN_CONTENT_NOTICE
    links: dict = Field(default_factory=dict, alias="_links")


def _query(params: dict, cards: list[str], cursor: str | None) -> str:
    from urllib.parse import urlencode

    pairs = [(k, v) for k, v in params.items() if v is not None] + [("cards", c) for c in cards] + ([("cursor", cursor)] if cursor else [])
    return "?" + urlencode(pairs) if pairs else ""


def throttle(request: Request, settings, bucket: str, who: str, allowed: int, db: Session | None = None) -> None:
    """Count one call for ``who`` ("user:7" or "ip:1.2.3.4") in ``bucket``; 429 over ``allowed`` a minute, with Retry-After
    and the seconds in the message. Counted in the database like the sign-in limits, under a keyed hash, so the rows name no
    one. Pass the request's own ``db`` session: a second one would hold two connections at once, and under parallel use
    (five agents) a request that needs two waits for the pool it is itself emptying (#169)."""
    minute = int(time.time() // WINDOW)
    key = hmac.new(settings.session_secret.encode(), f"{bucket}:{who}".encode(), hashlib.sha256).hexdigest()
    if db is None:
        with request.app.state.db.sessions() as own:
            count = hit(own, key, minute)
            own.commit()
    else:
        count = hit(db, key, minute)
        db.commit()
    if count > allowed:
        wait = max(1, math.ceil((minute + 1) * WINDOW - time.time()))
        raise HTTPException(429, f"Too many {bucket} requests. Try again in {wait} seconds.", headers={"Retry-After": str(wait)})


class WhoamiOut(BaseModel):
    user: dict
    auth: str = Field(description="How this request is authenticated: 'session' (web) or 'bearer' (an access or personal access token)")
    scopes: list[str]
    data: StatusOut
    guidance: str
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


def build_router(get_db, optional_user, current_user, settings, rules_live=None) -> APIRouter:
    router = APIRouter(prefix=V1 + "/catalog", tags=["catalog"])
    live_rules = rules_live or LiveRules()

    def rules_edition():
        """The current Comprehensive Rules, read live from Wizards (docs/rules-index.md): nothing is stored."""
        try:
            return live_rules.edition()
        except RulesUnavailable as exc:
            raise HTTPException(503, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    agent = APIRouter(prefix=V1 + "/agent", tags=["agents"])

    def current_edition(version: str | None):
        """The Comprehensive Rules edition to answer from. ``version`` (YYYY-MM-DD) may only name the current edition (or
        be omitted or "latest"): Wizards publishes just the current edition on its rules page and the Vault keeps no copy
        of the rules, so an older one cannot be read. Any other value is refused by name, never silently ignored (#25)."""
        edition = rules_edition()
        wanted = (version or "").strip().lower()
        if wanted not in ("", "latest", edition.version):
            raise HTTPException(422, f"Comprehensive Rules edition {version.strip()} is not available: only the current edition, "
                                     f"{edition.version}, can be read (Wizards publishes just that one, and the Vault stores no copy "
                                     "of the rules). Leave version out, or pass 'latest' or " + edition.version + ".")
        return edition

    def access(request: Request, user: User | None = Depends(optional_user), db: Session = Depends(get_db)) -> User | None:
        """Signed-in people only (never anonymous: see the module docstring), at most CATALOG_RATE_LIMIT a minute."""
        if user is None:
            raise HTTPException(401, "Sign in (or send a personal access token) to use the catalog.",
                                headers={"WWW-Authenticate": 'Bearer realm="the-vault"'})
        throttle(request, settings, "catalog", f"user:{user.id}", settings.catalog_rate_limit, db)
        return user

    def status_body(db: Session) -> dict:
        rows = q.sources(db)
        out = {n: {"version": s.version, "as_of": s.fetched_at.date().isoformat(), "rows": s.rows} for n, s in rows.items()}
        if limited_data.SOURCE_NAME in out:  # each loaded set and format, with the date of each 17Lands file (design section 6)
            out[limited_data.SOURCE_NAME]["sets"] = [
                f"{e['set']} {e['format']}: " + ", ".join(f"{k} file {v['last_modified']}" for k, v in e["files"].items())
                for e in limited_data.loaded_sets(db)]
        version = live_rules.cached_version
        if version is None:  # a cold server instance: read the current edition now (cached for hours, as any rules tool would)
            try:
                version = live_rules.edition().version
            except RulesUnavailable:
                version = None  # Wizards' page cannot be read right now: say nothing rather than fail the status (#244)
        return {"sources": out, "rules_version": version,
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

    @router.get("/limited/{set_code}", response_model=LimitedOut, response_model_by_alias=True,
                summary="Win rates and pick positions of a Limited set's cards, from 17Lands' public data (Magic Arena, CC BY 4.0), each with its sample size")
    def limited(request: Request, set_code: Annotated[str, Path(min_length=2, max_length=10, pattern="^[A-Za-z0-9]+$")],
                fmt: Literal["PremierDraft", "TradDraft"] = Query(default="PremierDraft", alias="format"),
                cards: list[Annotated[str, Field(min_length=1, max_length=300)]] = Query(default=[], max_length=limited_data.MAX_CARDS),
                sort: Literal["win_rate_in_hand", "games_in_hand", "avg_last_seen_pick", "avg_taken_at"] = "win_rate_in_hand",
                limit: int = Query(default=20, ge=1, le=limited_data.MAX_LIMIT), cursor: str | None = Query(default=None, max_length=200),
                user=Depends(access), db: Session = Depends(get_db)) -> dict:
        """Global data, no user id: whatever the person asks about, nothing of theirs is read."""
        body = limited_data.card_stats(db, set_code, fmt, cards=cards, sort=sort, limit=limit, cursor=cursor)
        params = {"format": fmt, "sort": None if cards else sort, "limit": limit}
        links = {"self": link(f"{V1}/catalog/limited/{body['set']}" + _query(params, cards, cursor))}
        if body.get("next_cursor"):
            links["next"] = link(f"{V1}/catalog/limited/{body['set']}" + _query(params, cards, body["next_cursor"]))
        return body | {"_links": links}

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
            body["computed_roles"] = [{"role": role, "basis": "computed", **role_rules.describe(entry["rule"])}
                                      for role, entry in deck_tools.role_entries(db, [found])[found.oracle_id].items()
                                      if entry["basis"] == "computed"]
            body |= {"tags": q.card_tags(db, found.oracle_id), "rulings_total": total,
                     "legality_changes": q.legality_changes(db, [found.oracle_id]), "legality_changes_note": q.LEGALITY_NOTE}
            body["_links"]["rulings"] = link(f"{V1}/catalog/cards/{found.oracle_id}/rulings")
            if body["computed_roles"]:  # a role from the Vault's own rules over Oracle text is computed, never Scryfall's tag
                body["provenance"].append(prov.computed("roles worked out by the Vault's rules over this card's Oracle text, where Tagger has no tag "
                                                        "(docs/card-roles-design.md)", q.provenance_for(db, "oracle_cards"), as_of=date.today()))
            if body["legality_changes"]:  # the change log is the Vault's own record of Scryfall's data, so it is labelled as computed
                body["provenance"].append(prov.computed("legality change log: the Vault compared Scryfall's legalities between daily loads",
                                                        q.provenance_for(db, "oracle_cards"), as_of=body["legality_changes"][0]["observed_on"]))
            price = db.get(q.OraclePrice, found.oracle_id)
            if price is not None:  # the cheapest priced paper printing, today's figure (no history for cards nobody owns)
                body["price"] = {"usd": price.usd, "usd_foil": price.usd_foil, "eur": price.eur, "as_of": price.day.isoformat(),
                                 "source": price.source, "printing": "the cheapest priced paper printing"}
                body["provenance"] += q.provenance_for(db, "oracle_prices")
        return body

    @router.get("/roles", response_model=RoleVocabulary, response_model_by_alias=True,
                summary="The 22 roles the Vault reads on a card (a mana rock, a token doubler, a free counterspell ...) and the rules that find each")
    def roles_vocabulary(user=Depends(access), db: Session = Depends(get_db)) -> dict:
        sources = q.sources(db)
        return {"label": card_roles.LABEL, "roles_version": card_roles.VERSION, "roles": card_roles.vocabulary(),
                "provenance": card_roles.computed_provenance(sources),
                "_links": {"self": link(f"{V1}/catalog/roles"), "card": link(f"{V1}/catalog/cards/roles", title="Add ?name=NAME or ?oracle_id=ID"),
                           "collection": link(f"{V1}/collection/roles", title="How many cards of yours have each role")}}

    @router.get("/cards/roles", response_model=CardRolesOut, response_model_by_alias=True,
                summary="What a card does, as a list of roles: the Vault's reading of its Oracle text, each with the rule that found it")
    def card_role_list(name: str | None = Query(default=None, min_length=1, max_length=300),
                       oracle_id: str | None = Query(default=None, min_length=36, max_length=36),
                       user=Depends(access), db: Session = Depends(get_db)) -> dict:
        if not name and not oracle_id:
            raise HTTPException(422, "Give a name or an oracle_id")
        found, suggestions = q.find_card(db, name=name, oracle_id=oracle_id)
        if found is None and not suggestions:
            raise HTTPException(404, "No card with that name")
        sources = q.sources(db)
        body = {"card": None, "suggestions": [c.name for c in suggestions], "label": card_roles.LABEL, "roles_version": card_roles.VERSION,
                "roles": [], "message": None, "community_tags": [], "community_tags_label": card_roles.TAGGER_LABEL,
                "provenance": card_roles.computed_provenance(sources),
                "_links": {"self": link(f"{V1}/catalog/cards/roles"), "vocabulary": link(f"{V1}/catalog/roles")}}
        if found is None:
            return body
        hits = card_roles.hits_of(found)
        body["card"] = {"oracle_id": found.oracle_id, "name": found.name, "type_line": found.type_line, "mana_cost": found.mana_cost,
                        "scryfall_uri": found.scryfall_uri}
        body["roles"] = card_roles.views(hits)
        if not hits:
            body["message"] = ("The Vault's rules found no role on this card. That is not the same as the card doing nothing: the rules read "
                               "22 common jobs and miss some wordings.")
        body["community_tags"] = q.card_tags(db, found.oracle_id)
        if body["community_tags"]:
            body["provenance"] += q.provenance_for(db, "oracle_tags")
        body["_links"] |= {"catalog_card": link(f"{V1}/catalog/cards?oracle_id={found.oracle_id}")}
        return body

    @router.post("/walkthrough", response_model=WalkthroughOut, response_model_by_alias=True,
                 summary="Present a step-by-step explanation with each cited rule looked up and attached verbatim")
    def walkthrough(body: WalkthroughIn, user=Depends(access), db: Session = Depends(get_db)) -> dict:
        edition = current_edition(body.version)
        version = edition.version
        steps, unknown = [], []
        for i, step in enumerate(body.steps, 1):
            cited = []
            for number in step.rules:
                rule = edition.find(number)
                if rule is None:
                    unknown.append({"step": i, "rule": number})
                else:
                    cited.append({"number": rule["number"], "text": rule["text"]})
            steps.append({"n": i, "text": step.text, "rules": cited})
        return {"title": body.title, "cards": body.cards, "version": version, "steps": steps, "unknown_rules": unknown,
                "note": "Step text is written by the assistant; rule texts are Wizards' Comprehensive Rules, looked up by the Vault. "
                        "Steps citing an unknown rule number are flagged, not trusted.",
                "provenance": edition.provenance(), "_links": {"self": link(f"{V1}/catalog/walkthrough")}}

    @router.get("/cards/{oracle_id}/rulings", response_model=RulingsOut, response_model_by_alias=True,
                summary="A card's rulings (Wizards' text via Scryfall), newest first, at most 25 a page; offset pages on")
    def rulings(oracle_id: Annotated[str, Path(min_length=36, max_length=36)], limit: int = Query(default=25, ge=1, le=q.MAX_RULINGS),
                offset: int = Query(default=0, ge=0, le=10000), user=Depends(access), db: Session = Depends(get_db)) -> dict:
        if db.get(q.OracleCard, oracle_id) is None:
            raise HTTPException(404, "No card with that Oracle id")
        items, total = q.rulings_for(db, oracle_id, limit, offset)
        return {"oracle_id": oracle_id, "rulings": items, "total": total, "offset": offset,
                "next_offset": offset + len(items) if offset + len(items) < total else None, "provenance": q.provenance_for(db, "rulings"),
                "_links": {"self": link(f"{V1}/catalog/cards/{oracle_id}/rulings")}}

    @router.get("/rules/search", response_model=RuleSearchOut, response_model_by_alias=True,
                summary="Search the Comprehensive Rules (best matches first, at most 10)")
    def rules_search(qs: str = Query(alias="q", min_length=2, max_length=200), limit: int = Query(default=5, ge=1, le=q.MAX_RULE_RESULTS),
                     version: str | None = Query(default=None, max_length=10), user=Depends(access),
                     db: Session = Depends(get_db)) -> dict:
        edition = current_edition(version)
        results, matched = edition.search(qs, limit)
        return {"version": edition.version, "query": qs, "matched": matched, "results": results, "provenance": edition.provenance(),
                "_links": {"self": link(f"{V1}/catalog/rules/search")}}

    @router.get("/rules", response_model=RuleOutlineOut, response_model_by_alias=True,
                summary="The Comprehensive Rules' table of contents: the sections, or what is directly under a number")
    def rules_outline(under: str | None = Query(default=None, max_length=20, description="A section (7), subsection (702) or rule (702.19)"),
                      user=Depends(access)) -> dict:
        edition = rules_edition()
        out = edition.outline(under)
        if out.get("unknown"):
            raise HTTPException(404, "No such rule in the current edition")
        return {"version": edition.version, **out, "provenance": edition.provenance(), "_links": {"self": link(f"{V1}/catalog/rules")}}

    @router.get("/rules/term/{name}", response_model=RuleTermOut, response_model_by_alias=True,
                summary="A glossary term or keyword ability, and the rules that define it")
    def rules_term(name: Annotated[str, Path(min_length=2, max_length=120)], user=Depends(access)) -> dict:
        edition = rules_edition()
        found = edition.term(name)
        if found is None:
            raise HTTPException(404, "No glossary term or keyword ability by that name; try search_rules")
        return {"version": edition.version, **found, "provenance": edition.provenance(),
                "_links": {"self": link(f"{V1}/catalog/rules/term/{name}")}}

    @router.get("/rules/changes", response_model=RulesChangesOut, response_model_by_alias=True,
                summary="What changed between the previous and the current Comprehensive Rules, and the rulings and legality changes since")
    def rules_changes_brief(previous: str | None = Query(default=None, max_length=10, description="The date in the previous edition's file name "
                                                         "(YYYY-MM-DD); by default it is found on Wizards' CDN"),
                            since: str | None = Query(default=None, max_length=10, description="First day of the rulings and legality window "
                                                      "(YYYY-MM-DD); by default the day the previous edition took effect"),
                            limit: int = Query(default=rules_changes.DEFAULT_LIMIT, ge=1, le=rules_changes.MAX_LIMIT),
                            user=Depends(access), db: Session = Depends(get_db)) -> dict:
        try:
            since_day, prev_day = rules_changes.parse_date(since), rules_changes.parse_date(previous)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            comparison = live_rules.compare(previous)
        except RulesUnavailable as exc:
            raise HTTPException(503, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
        if prev_day and comparison.previous is None:
            raise HTTPException(404, comparison.note or "No such edition")
        edition = rules_edition()
        side = lambda s: {"version": s.version, "file_date": s.file_date, "url": s.url, "in_force": s.in_force}  # noqa: E731
        window = since_day or (date.fromisoformat(comparison.previous.version) if comparison.previous else date.today() - timedelta(days=30))
        rulings, rulings_total, by_source = q.rulings_since(db, window, limit)
        legality, legality_total = q.legality_changes_since(db, window, limit)
        notes = []
        if comparison.previous is None:
            notes.append(comparison.note or "No previous edition was found.")
            if not since_day:
                notes.append("With no previous edition to date the window from, rulings and legality changes cover the last 30 days.")
        if comparison.same_effective_date:
            notes.append("Earlier files with the same effective date as the current edition were skipped as its corrections: "
                         + ", ".join(s.file_date for s in comparison.same_effective_date) + ".")
        if not edition.in_force():
            notes.append(f"The current edition takes effect on {edition.version}; until then the previous edition is in force.")
        sources = [prov.source("Wizards of the Coast", origin="Comprehensive Rules, current edition (read live; the Vault stores no copy)",
                               url=comparison.current.url, as_of=date.today(), version=comparison.current.version, wizards_material=True)]
        if comparison.previous:
            sources.append(prov.source("Wizards of the Coast", origin="Comprehensive Rules, previous edition (read live; the Vault stores no copy)",
                                       url=comparison.previous.url, as_of=date.today(), version=comparison.previous.version,
                                       wizards_material=True))
        body = {
            "current": side(comparison.current), "previous": side(comparison.previous) if comparison.previous else None,
            "rules": comparison.changes.brief(limit) if comparison.changes else None,
            "rulings": {"total": rulings_total, "by_source": by_source, "items": rulings, "capped": rulings_total > len(rulings),
                        "note": "Rulings as published (Wizards' text via Scryfall), each cut to a pointer; read one in full with get_rulings."},
            "legality": {"total": legality_total, "items": legality, "capped": legality_total > len(legality), "note": q.LEGALITY_NOTE},
            "since": window.isoformat(), "note": " ".join(notes) or None,
            "provenance": [*sources, *q.provenance_for(db, "rulings")],
            "_links": {"self": link(f"{V1}/catalog/rules/changes")},
        }
        if comparison.changes:
            body["provenance"].insert(0, prov.computed("change brief: the Vault compared the two editions' rule numbers and words when asked, "
                                                       "and kept neither edition", list(sources), as_of=date.today()))
        if legality:
            body["provenance"].append(prov.computed("legality change log: the Vault compared Scryfall's legalities between daily loads",
                                                    q.provenance_for(db, "oracle_cards"), as_of=legality[0]["observed_on"]))
        return body

    @router.get("/rules/{number}", response_model=RuleOut, response_model_by_alias=True,
                summary="One rule by number (e.g. 613.1a) or glossary term (glossary:Trample), with its subrules")
    def rule(number: Annotated[str, Path(min_length=1, max_length=120)], version: str | None = Query(default=None, max_length=10),
             user=Depends(access), db: Session = Depends(get_db)) -> dict:
        edition = current_edition(version)
        body = edition.rule(number)
        if body is None:
            raise HTTPException(404, "No such rule in the current edition")
        return {"version": edition.version, "rule": body, "subrules": body["children"], "provenance": edition.provenance(),
                "_links": {"self": link(f"{V1}/catalog/rules/{number}")}}

    @router.post("/verify-citation", response_model=CitationOut, response_model_by_alias=True,
                 summary="Is this quote verbatim in the rule, Oracle text or ruling it is attributed to?")
    def verify(body: CitationIn, user=Depends(access), db: Session = Depends(get_db)) -> dict:
        if body.kind != "rule" and (body.version or "").strip().lower() not in ("", "latest"):
            raise HTTPException(422, f"version {body.version.strip()} cannot be applied to {body.kind}: card text and rulings are "
                                     "the current Scryfall data and have no editions; only kind 'rule' has a version, and then "
                                     "only the current edition. Leave version out.")
        if body.kind == "rule":
            edition = current_edition(body.version)
            rule = edition.find(body.ref)
            wanted = q._squash(body.quote)
            if rule is None:
                result = {"verified": False, "reason": "no such rule", "version": edition.version}
            else:
                ok = bool(wanted) and wanted in q._squash(rule["text"])
                result = {"verified": ok, "version": edition.version, "source_text": None if ok else rule["text"], "number": rule["number"]}
            provenance = edition.provenance()
        else:
            result = q.verify_citation(db, body.kind, body.ref, body.quote, body.version)
            provenance = q.provenance_for(db, {"oracle_text": "oracle_cards", "ruling": "rulings"}[body.kind])
        return {"verified": bool(result.pop("verified")), "detail": result,
                "provenance": provenance,
                "_links": {"self": link(f"{V1}/catalog/verify-citation")}}

    root = APIRouter()
    root.include_router(router)
    root.include_router(agent)
    return root
