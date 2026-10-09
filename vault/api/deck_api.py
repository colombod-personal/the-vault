"""Deck analysis under ``/api/v1/decks``: statistics, legality, upgrade candidates, the validator that
checks a proposed list of changes, and the shopping list. All computed from the stored catalog by
``vault.deck_tools``; no model is involved. Each answer is labelled ``computed`` (by the Vault) and
lists the sources and versions it used as ``inputs`` (``vault.provenance``).

These work on a decklist the caller sends; nothing is stored. The shopping list also reads the
caller's own collection (scoped to them, like every other collection endpoint).
"""

from __future__ import annotations

import hashlib
from datetime import date

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from mtg_toolkits import delta
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import brackets
from .. import catalog_queries as q
from .. import combos
from .. import deck_overview
from .. import deck_tools as dt
from .. import provenance as prov
from .. import shopping as shop
from .. import simulate
from ..importer import user_entries
from ..models import Card, Deck, Entry, OraclePrinting, User
from .catalog_api import throttle
from .hal import link

V1 = "/api/v1"
DECK = Field(min_length=1, max_length=50_000, description="The decklist, one card per line, e.g. '1 Sol Ring'; Commander cards under a 'Commander' header")
FORMAT = Field(max_length=20, description=f"One of: {', '.join(dt.FORMATS)}")
DECK_LIMIT = 30  # analyses a minute per person


class DeckIn(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=50_000, description="The decklist, one card per line, e.g. '1 Sol Ring'; "
                             "Commander cards under a 'Commander' header. Or give deck_id.")
    deck_id: int | None = Field(default=None, ge=1, le=2_147_483_647, description="A saved deck's id (list_decks): used instead of text")

    @model_validator(mode="after")
    def _one_of(self):
        if (self.text is None) == (self.deck_id is None):
            raise ValueError("give either text (a decklist) or deck_id (a saved deck), not both and not neither")
        return self


class StatsIn(DeckIn):
    include_combos: bool = Field(default=False, description="Also ask Commander Spellbook for the deck's two-card combos, which one "
                                 "input of the Commander Bracket hint needs. The deck's card names are sent to Commander Spellbook "
                                 "only when this is true")


class FormatIn(DeckIn):
    format: str = FORMAT


class UpgradesIn(FormatIn):
    budget_usd: float = Field(ge=0, le=100_000, description="The most any single added card may cost")
    roles: list[str] | None = Field(default=None, max_length=len(dt.ROLE_TAGS), description=f"Roles to search ({', '.join(dt.ROLE_TAGS)}); default: the roles the deck is short of")
    limit: int = Field(default=10, ge=1, le=dt.MAX_CANDIDATES)
    use_collection: bool = Field(default=False, description="Also suggest cards you already own, whatever their price, "
                                 "first, each with owned_copies")


class ChangesIn(FormatIn):
    adds: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=60,
                                                              description="Card names to add (a quantity such as 2x repeats one)")
    cuts: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=60,
                                                              description="Card names to cut (a quantity such as 2x repeats one)")
    budget_usd: float | None = Field(default=None, ge=0, le=100_000, description="The most the adds may cost in total")
    include_text: bool = Field(default=False, description="Also return `deck_text`, the list after the cuts and adds (the main deck gets "
                               "the adds, cards named as the catalog names them), ready for update_deck: what is saved is what was checked")


class SimulateIn(FormatIn):
    on_the_play: bool = Field(default=True, description="Going first (no draw on turn 1 in two-player games)")
    turns: int = Field(default=6, ge=1, le=simulate.MAX_TURNS)
    games: int = Field(default=1000, ge=1, le=simulate.MAX_GAMES, description="Games behind the odds")
    samples: int = Field(default=5, ge=0, le=simulate.MAX_SAMPLES, description="Games shown turn by turn")
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1, description="Same seed, same answer (default: from the deck)")


class ShoppingIn(DeckIn):
    format: Literal["plain", "cardkingdom", "tcgplayer", "cardmarket", "csv", "all"] = Field(default="plain", description=(
        "How `text` is written: plain, cardkingdom (Card Kingdom's Deck Builder), tcgplayer (Mass Entry, with set and collector "
        "number), cardmarket (want list, with the expansion's name), csv; all fills `texts` with every one. Each was checked "
        "against the store's own help page, named in `store_format`."))
    finish: Literal["nonfoil", "foil", "etched"] | None = Field(default=None, description=(
        "Only printings in this finish (nonfoil, foil, etched); default: whichever is cheapest"))
    language: str | None = Field(default=None, max_length=30, description=(
        "Only printings in this language: a Scryfall code (en, ja, de ...) or its name. Scryfall prices few non-English "
        "printings, so a line may then have no qualifying printing"))
    sets: list[str] | None = Field(default=None, max_length=30, description=(
        "Only printings from these sets (Scryfall set codes, e.g. ['2xm', 'mh2'])"))
    condition: Literal["NM", "LP", "MP", "HP", "DMG"] | None = Field(default=None, description=(
        "The worst condition accepted (NM, LP, MP, HP, DMG). Scryfall's prices are not per condition, so it does not change "
        "the printing or price chosen"))

    @field_validator("sets")
    @classmethod
    def _sets(cls, value):
        if value is not None and any(not s.strip() or len(s) > 10 for s in value):
            raise ValueError("sets are Scryfall set codes of up to 10 characters")
        return value


MULTIPLAYER = {"commander", "oathbreaker", "paupercommander", "predh"}


class Answer(BaseModel):
    deck: dict | None = Field(None, description="Which deck this is about: its name (saved decks), format, commander(s), "
                              "card count and colour identity (vault.deck_overview). Lead with it")
    result: dict
    provenance: list[prov.Provenance]
    links: dict = Field(default_factory=dict, alias="_links")


def owned_by_oracle(db: Session, user: User) -> dict[str, int]:
    """Copies of each card (by oracle id) in the person's collection."""
    rows = db.execute(select(Card.oracle_id, func.sum(Entry.quantity))
                      .join(Card, Card.scryfall_id == Entry.scryfall_id)
                      .where(Entry.user_id == user.id, Card.oracle_id.is_not(None)).group_by(Card.oracle_id)
                      .having(func.sum(Entry.quantity) > 0)).all()  # a row of 0 copies isn't owning the card
    return {oid: int(n) for oid, n in rows}


def build_router(get_db, current_user, settings, transport=None) -> APIRouter:
    router = APIRouter(prefix=V1 + "/decks", tags=["decks"])

    def answer(db: Session, what: str, result: dict, inputs: tuple[str, ...], path: str, deck: dict | None = None) -> dict:
        loaded = q.sources(db)
        used = [n for n in inputs if n in loaded]
        return {"deck": deck, "result": result,
                "provenance": [prov.computed(what, q.provenance_for(db, *used), as_of=date.today())],
                "_links": {"self": link(f"{V1}/decks/{path}")}}

    def identity(db: Session, user: User, body: DeckIn) -> dict:
        """Which deck an answer is about (#216): a saved deck's id and name, and for any list its overview (format,
        commander(s), card count, colour identity), so answers and views lead with it rather than with card lines."""
        if body.deck_id is not None:
            deck = db.get(Deck, body.deck_id)
            text, name, stored = deck.text, deck.name, deck.format  # text_of has already checked it is theirs
        else:
            text, name, stored = body.text, None, None
        known = deck_overview.identities(db, deck_overview.read(text)["commanders"])
        out = {"id": body.deck_id, "name": name, "overview": deck_overview.overview(text, stored, known)}
        credit = deck_overview.archidekt_credit(deck) if body.deck_id is not None else None
        if credit:  # the deck is Archidekt's: every answer about it says whose it is and when the list was read (#96)
            out["credit"] = credit
            deck_overview.refresh_note(deck, out["overview"])
        return out

    def text_of(db: Session, user: User, body: DeckIn) -> str:
        """The decklist: sent as text, or a saved deck of this person's by id."""
        if body.deck_id is None:
            return body.text
        deck = db.get(Deck, body.deck_id)
        if deck is None or deck.user_id != user.id:
            raise HTTPException(404, "No saved deck with that id (list_decks shows yours)")
        return deck.text

    def prepared(request: Request, db: Session, user: User, text: str) -> dt.Resolved:
        throttle(request, settings, "deck analysis", f"user:{user.id}", DECK_LIMIT, db)
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
    def deck_stats(request: Request, body: StatsIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, text_of(db, user, body))
        found = None
        if body.include_combos:
            found = {"checked": False, "reason": "Commander Spellbook could not be asked"}
            try:
                results = combos.ask([(e.name, e.line.quantity) for e in resolved.played() if e.line.section != "commander"],
                                     [e.name for e in resolved.section("commander")], transport)
                found = {"checked": True, "combos": combos.two_card_combos(results)}
            except combos.ComboServiceError as exc:  # down, slow, rate-limited or breaker open: the rest of the answer stands
                found = {"checked": False, "reason": str(exc)}
        result = dt.stats(db, resolved, found)
        out = answer(db, "deck statistics", result, ("oracle_cards", "oracle_tags", "oracle_prices"), "stats", identity(db, user, body))
        # the bracket hint also rests on Wizards' published bracket pages (read on brackets.RULES_READ), and on Commander Spellbook
        # when its combos were asked for: they are inputs of the computed block, so the answer never reads as the Vault's own
        computed = out["provenance"][0]
        computed.inputs = computed.inputs + [prov.source("Wizards of the Coast", origin=brackets.PROVENANCE_ORIGIN, url=brackets.SOURCES[0]["url"],
                                                         as_of=brackets.RULES_READ, wizards_material=True)]
        if found and found.get("checked"):
            computed.inputs.append(prov.source("Commander Spellbook", origin="combos written by its community",
                                               url="https://commanderspellbook.com", as_of=date.today(), wizards_material=True))
        return out

    @router.post("/simulate", response_model=Answer, response_model_by_alias=True,
                 summary="How the mana curve plays: sample opening turns and the odds behind them (a simple goldfish)")
    def deck_simulate(request: Request, body: SimulateIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        fmt = dt.check_format(body.format)
        resolved = prepared(request, db, user, text_of(db, user, body))
        cards, commander = [], None
        for e in resolved.played():
            if e.card is None or e.line.section == "companion":  # a companion starts outside the game
                continue
            card = simulate.from_oracle(e.card)
            if e.line.section == "commander" and commander is None:
                commander = card  # in the command zone: castable once there is the mana
            else:
                cards.extend([card] * e.line.quantity)
        # The default is taken modulo the range the endpoint accepts, so the seed an answer reports can always be sent back (#392).
        seed = body.seed if body.seed is not None else int(hashlib.sha256(text_of(db, user, body).encode()).hexdigest()[:8], 16) % 2**31
        try:
            result = simulate.simulate(cards, commander=commander, multiplayer=fmt in MULTIPLAYER, on_the_play=body.on_the_play,
                                       turns=body.turns, games=body.games, samples=body.samples, seed=seed)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        result["unmatched"] = resolved.unmatched
        result["format"] = fmt
        colours = {c for e in resolved.played() if e.card is not None for c in (e.card.color_identity or [])}
        if len(colours) >= 3:  # #216: a five-colour deck's numbers read far better than its real games
            result["colour_warning"] = (
                f"This deck uses {len(colours)} colours ({''.join(c for c in 'WUBRG' if c in colours)}), and the simulation does "
                "not check colours: any land pays for any spell. Real games will miss more spells and start slower than these "
                "numbers show, the more so the more colours the deck has. Say so when you report them.")
        return answer(db, "mana curve simulation", result, ("oracle_cards",), "simulate", identity(db, user, body))

    @router.post("/legality", response_model=Answer, response_model_by_alias=True,
                 summary="Is the decklist legal in a format? Lists every issue, and what was not checked")
    def deck_legality(request: Request, body: FormatIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, text_of(db, user, body))
        try:
            result = dt.legality(resolved, body.format)
        except dt.DeckError as exc:
            failing(exc)
        fmt = result["format"]
        in_deck = [e.card.oracle_id for e in resolved.played() if e.card is not None]
        result["changes"] = q.legality_changes(db, list(dict.fromkeys(in_deck)), fmt)
        result["changes_note"] = q.LEGALITY_NOTE
        return answer(db, "legality check (with the recorded legality changes of its cards)", result, ("oracle_cards",), "legality",
                      identity(db, user, body))

    @router.post("/upgrades", response_model=Answer, response_model_by_alias=True,
                 summary="Upgrade candidates within a budget: legal, in the deck's colors, not already in it")
    def deck_upgrades(request: Request, body: UpgradesIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, text_of(db, user, body))
        loaded = q.sources(db)
        if "oracle_prices" not in loaded or "oracle_tags" not in loaded:
            raise HTTPException(503, "Prices and role tags have not been loaded yet, so upgrade candidates cannot be computed.")
        try:
            owned = owned_by_oracle(db, user) if body.use_collection else None
            result = dt.find_upgrades(db, resolved, body.format, body.budget_usd, body.roles, body.limit, owned=owned)
        except dt.DeckError as exc:
            failing(exc)
        return answer(db, "upgrade candidates", result, ("oracle_cards", "oracle_tags", "oracle_prices"), "upgrades",
                      identity(db, user, body))

    @router.post("/validate-changes", response_model=Answer, response_model_by_alias=True,
                 summary="Check a proposed list of cuts and adds: legality, colors, resulting deck, and budget")
    def deck_validate(request: Request, body: ChangesIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        prepared(request, db, user, text_of(db, user, body))
        try:
            result = dt.validate_changes(db, text_of(db, user, body), body.format, body.adds, body.cuts, body.budget_usd,
                                       body.include_text)
        except dt.DeckError as exc:
            failing(exc)
        return answer(db, "validation of proposed changes", result, ("oracle_cards", "oracle_prices"), "validate-changes",
                      identity(db, user, body))

    @router.post("/combos", response_model=Answer, response_model_by_alias=True,
                 summary="Combos in a decklist, and those one card short (asked of Commander Spellbook on demand)")
    def deck_combos(request: Request, body: DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        resolved = prepared(request, db, user, text_of(db, user, body))
        names = {e.name for e in resolved.played()}
        commanders = [e.name for e in resolved.section("commander")]
        main = [(e.name, e.line.quantity) for e in resolved.played() if e.line.section != "commander"]
        try:
            results = combos.ask(main, commanders, transport)
        except combos.ComboServiceBusy as exc:  # the client's own rate limit or an open breaker: nothing was asked of them
            raise HTTPException(503, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
        except combos.ComboServiceError as exc:
            raise HTTPException(502, str(exc)) from exc
        out = combos.summarize(results, names)
        out["limits"] = ("Only combos known to Commander Spellbook are listed. A deck can hold other loops and engines that are not "
                         "listed (for example a repeatable token engine with mana creatures): finding none does not mean the deck has "
                         "no infinite combos, so never tell a player it is combo-free from this alone.")
        spellbook = prov.source("Commander Spellbook", origin="combos written by its community", url="https://commanderspellbook.com",
                                as_of=date.today(), wizards_material=True)
        return {"deck": identity(db, user, body), "result": out,
                "provenance": [spellbook, prov.computed("combo lookup", [spellbook], as_of=date.today())],
                "_links": {"self": link(f"{V1}/decks/combos")}}

    @router.post("/shopping-list", response_model=Answer, response_model_by_alias=True,
                 summary="The cards of a decklist you do not own, with dated Scryfall prices, as paste-ready text for a store's list tool")
    def deck_shopping(request: Request, body: ShoppingIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        try:
            rules = shop.make_rules(body.finish, body.language, body.sets, body.condition)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        resolved = prepared(request, db, user, text_of(db, user, body))
        loaded = q.sources(db)
        if rules.given and "oracle_printings" not in loaded:
            raise HTTPException(503, "The price of each printing has not been loaded yet, so a printing cannot be chosen under rules "
                                     "(finish, language, sets). Leave those out for the cheapest priced printing of each card.")
        deck = dt.parse(text_of(db, user, body))
        owned = [r.to_collection_entry() for r in user_entries(db, user)]
        by_name = {e.line.name.strip().lower(): e.card for e in resolved.entries if e.card}
        prices = dt.prices_of(db, [c.oracle_id for c in by_name.values()])
        covered = list(delta.coverage(deck.to_entries(), owned))
        missing = [(c, by_name.get(c.entry.name.strip().lower())) for c in covered if c.missing > 0]
        deck_copies = sum(c.entry.quantity for c in covered)
        to_buy_copies = sum(c.missing for c in covered)
        printings: dict[str, list] = {}
        as_of = None
        if rules.given:
            wanted = [card.oracle_id for _, card in missing if card]
            for p in db.scalars(select(OraclePrinting).where(OraclePrinting.oracle_id.in_(wanted))):
                printings.setdefault(p.oracle_id, []).append(p)
            as_of = loaded["oracle_printings"].version  # the day the printing prices were loaded
        lines, total, unpriced, excluded = [], 0.0, 0, 0
        for c, card in missing:
            line = {"name": card.name if card else c.entry.name, "quantity": c.missing, "unit_price_usd": None, "price_date": None,
                    "known_card": card is not None}
            if rules.given:
                picked = shop.choose(printings.get(card.oracle_id, []), rules) if card else None
                if picked is None:
                    line.update(no_qualifying_printing=True,
                                reason=shop.why_none(rules) if card else "The card was not found in the catalog, so no printing can be chosen.")
                    excluded += 1
                else:
                    printing, finish, unit = picked
                    line.update(unit_price_usd=unit, price_date=as_of, printing=shop.printing_dict(printing, finish))
                    total += unit * c.missing
            else:
                price = prices.get(card.oracle_id) if card else None
                if price is not None and price.usd is not None:
                    line.update(unit_price_usd=price.usd, price_date=price.day.isoformat())
                    total += price.usd * c.missing
            if line["unit_price_usd"] is None and not line.get("no_qualifying_printing"):
                unpriced += 1
            lines.append(line)
        every = body.format == "all"
        fmt = "plain" if every else body.format
        info = shop.STORES[fmt]
        result = {"covers": {"deck_copies": deck_copies, "already_owned": deck_copies - to_buy_copies, "to_buy": to_buy_copies,
                             "meaning": f"This list is only the cards the person does not own: they already own {deck_copies - to_buy_copies} of the "
                                        f"deck's {deck_copies} cards, and the {to_buy_copies} left are listed. It is not the whole deck, and "
                                        "a card missing from it is one they own."},
                  "price_basis": ("the cheapest printing that fits your rules, per line (`printing`)" if rules.given else
                                  "the cheapest priced paper printing of each card; no rules were given, so no printing is chosen"),
                  "lines": lines, "format": body.format, "text": shop.render(lines, fmt),
                  "total_usd": round(total, 2), "unpriced_lines": unpriced, "no_qualifying_printing": excluded,
                  "rules": rules.describe() if rules.given else None,
                  "store_format": ({k: {"store": v["store"], "line": v["line"], "checked": v["checked"], "help_page": v["page"],
                                        "limits": v["limits"]} for k, v in shop.STORES.items()} if every else
                                   {"format": fmt, "store": info["store"], "line": info["line"], "checked": info["checked"],
                                    "help_page": info["page"], "limits": info["limits"]}),
                  "notes": ["Prices are Scryfall's (sourced from TCGplayer and Cardmarket), dated, not any store's price today. "
                            "The Vault knows no store's price: it never says which store is cheapest.",
                            "Paste the text into the store's own list tool and check what it matched. The Vault does not contact stores, "
                            "fill carts or place orders."]}
        if every:
            result["texts"] = {f: shop.render(lines, f) for f in shop.FORMATS}
        else:
            result["notes"] += shop.store_notes(fmt, lines, rules)
        if excluded:
            result["notes"].append(f"{excluded} card(s) have no printing that fits the rules and are left out of the text and the total: "
                                   "see `reason` on those lines.")
        used = ("oracle_cards", "oracle_prices", "oracle_printings") if rules.given else ("oracle_cards", "oracle_prices")
        return answer(db, "shopping list", result, used, "shopping-list", identity(db, user, body))

    return router
