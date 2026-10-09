"""Can each saved deck stand on its own? (#165, docs/deck-independence.md)

The question: can every saved deck be built **at the same time** from the copies owned, and if not, which cards are contested,
which deck would lose each one, and what does a copy cost to end the borrowing. Everything here is computed on the server; the
web view and assistants only show it.

Counted by card name (basic lands left out), the same way the deck coverage and the older overlap answer count: any printing
owned counts, and what is counted is **copies**.

- A card is **contested** when two or more saved decks use it and the collection holds some copies but fewer than they need
  together (``0 < have < need_for_all``). A card owned zero times is not contested: there is no copy to give or move.
- A contested card's copies are **allocated** to the decks in order: each deck takes the copies it needs while any remain. The
  default order finishes the deck that is closest to complete first (fewest cards still missing against the whole collection,
  then most recently edited, then name); the caller can pass ``priority`` (deck ids, in order) instead.
- A deck **holds** the copies it is given of a contested card and **lacks** the rest. Each lacking copy is ``not_owned`` (the
  collection could not supply it even if this deck took every owned copy) or ``held_by_other_deck`` (it exists, another deck was
  given it, so it is available by moving).
- The **global deficit** of a card is ``need_for_all - have``. Under the allocation the decks' lacking copies add up to exactly
  that, whatever the order, so the cost to finish every deck is the deficit times the price, counted once.

Prices are the cheapest known price of the card (the figure ``shopping_list`` gives: ``analytics.price_coverages``), with the
day it is from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from mtg_toolkits import delta
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import analytics, deck_text
from .models import Entry

BASICS = {"plains", "island", "swamp", "mountain", "forest", "wastes", "snow-covered plains", "snow-covered island",
          "snow-covered swamp", "snow-covered mountain", "snow-covered forest", "snow-covered wastes"}

DEFAULT_RULE = "closest_to_complete"
PRIORITY_RULE = "priority"
LEGACY_CARDS = 200  # cards in the older `cards` list, as before
PREVIEW_DECKS = 10  # deck names previewed on an older `cards` entry, or as "also wanted by"
CONTESTED_DECKS = 25  # decks listed on one contested card (decks_total says how many use it)
LISTED = 100  # cards listed in one deck's `holds` and `lacking` (the totals say how many there are)
SKIPPED_REASON = "The saved list could not be read, so this deck is not part of the check"
LIMITS = ("It counts copies, not play (a deck the person does not play counts as much as one they do), and it is by card name, "
          "not by printing.")


def allocation_note(rule: str) -> str:
    if rule == PRIORITY_RULE:
        return ("The decks named in `priority` take contested copies first, in that order; the others follow, the deck closest to "
                "complete first. Each deck takes the copies it needs while any remain.")
    return ("The deck closest to complete takes contested copies first (fewest cards still missing against the whole collection, "
            "then most recently edited, then name). Each deck takes the copies it needs while any remain.")


@dataclass
class ReadDeck:
    deck: object  # the saved Deck row
    needs: dict  # card key -> (name, copies needed), basic lands left out


@dataclass
class Card:
    key: tuple
    name: str
    have: int
    wants: list = field(default_factory=list)  # [(ReadDeck, copies)], in allocation order
    gets: dict = field(default_factory=dict)  # deck id -> copies given
    need_all: int = 0
    contested: bool = False
    deficit: int = 0
    unit_price: float | None = None
    price_date: str | None = None


@dataclass
class Analysis:
    decks: list  # one record per saved deck: the analysed ones in allocation order, then the skipped ones
    contested: list
    purchases: list
    summary: dict
    allocation: dict
    decks_analysed: int
    decks_skipped_count: int
    decks_checked: int
    legacy: dict  # the fields the answer has always had: shared_cards, short_cards, cards
    prices_date: str | None


def read_decks(decks) -> tuple[list[ReadDeck], list]:
    """The decks that can be read, with what each needs, and the ones that cannot (skipped, not an error for the others)."""
    ready, skipped = [], []
    for d in decks:
        try:
            needed = delta.aggregate(deck_text.parse_text(d.text).to_entries(), delta.BY_CARD)
        except (ValueError, OverflowError):
            skipped.append(d)
            continue
        ready.append(ReadDeck(d, {k: (e.name, e.quantity) for k, e in needed.items() if e.name.strip().lower() not in BASICS}))
    return ready, skipped


def _stamp(deck) -> float:
    at = deck.updated_at
    if at is None:
        return 0.0
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def order_decks(ready: list[ReadDeck], have: dict, priority: list[int]) -> list[ReadDeck]:
    """The allocation order: the decks named in ``priority`` first, as given; then the rest, closest to complete first."""
    def missing(r: ReadDeck) -> int:
        return sum(max(0, q - have.get(k, 0)) for k, (_, q) in r.needs.items() if q > 0)

    default = sorted(ready, key=lambda r: (missing(r), -_stamp(r.deck), r.deck.name.lower(), r.deck.id))
    if not priority:
        return default
    by_id = {r.deck.id: r for r in default}
    named = [by_id[i] for i in dict.fromkeys(priority) if i in by_id]
    taken = {r.deck.id for r in named}
    return named + [r for r in default if r.deck.id not in taken]


@dataclass
class Allocation:
    """Who gets which copies, before any price is looked up: the readable decks in allocation order and the cards they need."""

    ready: list  # ReadDeck, in name order as given
    skipped: list  # saved Deck rows that could not be read
    order: list  # ReadDeck, in allocation order
    cards: dict  # card key -> Card (what each deck wants and gets)
    have: dict  # card key -> copies owned


def owned_counts(db: Session, user_id: int) -> dict[tuple, int]:
    """Copies owned per card key (the key ``delta.BY_CARD`` makes: the front face, case-folded), added up in the database so the
    person's whole collection never has to be loaded as rows. The same numbers ``analyse`` gets from the entries."""
    rows = db.execute(select(Entry.name, func.sum(Entry.quantity)).where(Entry.user_id == user_id).group_by(Entry.name)).all()
    have: dict[tuple, int] = {}
    for name, copies in rows:
        key = (name.split(" // ")[0].strip().casefold(),)
        have[key] = have.get(key, 0) + int(copies or 0)
    return have


def allocate(decks: list, have: dict, priority: list[int] | None = None) -> Allocation:
    """The allocation of ``have`` (copies owned per card key) to ``decks`` (the person's saved decks, in name order)."""
    ready, skipped = read_decks(decks)
    order = order_decks(ready, have, list(priority or []))
    cards: dict[tuple, Card] = {}
    for r in order:
        for key, (name, q) in r.needs.items():
            if q > 0:
                cards.setdefault(key, Card(key, name, have.get(key, 0))).wants.append((r, q))
    for c in cards.values():
        c.need_all = sum(q for _, q in c.wants)
        c.deficit = max(0, c.need_all - c.have)
        c.contested = len(c.wants) >= 2 and 0 < c.have < c.need_all
        left = c.have
        for r, q in c.wants:
            c.gets[r.deck.id] = min(q, left)
            left -= c.gets[r.deck.id]
    return Allocation(ready, skipped, order, cards, have)


def _price_all(db: Session, user_id: int, cards: list[Card]) -> None:
    short = [c for c in cards if c.deficit > 0]
    if not short:
        return
    covered = analytics.price_coverages(db, user_id, [{"cards": [{"name": c.name, "missing": c.deficit} for c in short]}],
                                        owned_printings=False)[0]["cards"]
    for c, line in zip(short, covered):
        c.unit_price, c.price_date = line["unit_price"], line["price_date"]


def _cost(unit: float | None, copies: int) -> float | None:
    return None if unit is None else round(unit * copies, 2)


def _ref(deck) -> dict:
    return {"id": deck.id, "name": deck.name}


def _move(c: Card, order: list[ReadDeck], lacking_by_other: dict) -> dict | None:
    """Give one copy to the first deck that lacks one held by another deck, taken from the last deck holding copies."""
    target = next((r for r in order if lacking_by_other.get(r.deck.id, 0) > 0), None)
    if target is None:
        return None
    sources = [r for r, _ in c.wants if r is not target and c.gets.get(r.deck.id, 0) > 0]
    if not sources:
        return None
    source = sources[-1]
    return {"kind": "move", "from_deck": _ref(source.deck), "to_deck": _ref(target.deck), "quantity": 1,
            "effect": (f"Give one copy of {c.name} to {target.deck.name}; {source.deck.name} then lacks one. "
                       f"The copies still to buy ({c.deficit}) do not change.")}


def _buy(c: Card) -> dict:
    cost = _cost(c.unit_price, c.deficit)
    copies = f"{c.deficit} copy" if c.deficit == 1 else f"{c.deficit} copies"
    effect = (f"Buy {copies} of {c.name}" + (f" at ${c.unit_price:.2f} each, ${cost:.2f} in all (Scryfall's cheapest price, "
                                              f"as of {c.price_date})" if cost is not None else " (no price is known for it)")
              + ": every deck that uses it then has it.")
    return {"kind": "buy", "quantity": c.deficit, "unit_price": c.unit_price, "cost": cost,
            "price_status": "priced" if cost is not None else "unpriced", "price_date": c.price_date, "effect": effect}


def analyse(db: Session, user_id: int, decks: list, owned_entries, priority: list[int] | None = None) -> Analysis:
    """The independence of ``decks`` (the person's saved decks, in name order) against ``owned_entries`` (their collection)."""
    priority = list(priority or [])
    have_entries = delta.aggregate([r.to_collection_entry() for r in owned_entries], delta.BY_CARD)
    have = {k: e.quantity for k, e in have_entries.items()}
    allocation = allocate(decks, have, priority)
    ready, skipped, order, cards = allocation.ready, allocation.skipped, allocation.order, allocation.cards
    _price_all(db, user_id, list(cards.values()))

    records, lacking_by_other = [], {}  # lacking_by_other: card key -> {deck id: held_by_other_deck copies}
    for position, r in enumerate(order, 1):
        need = free = 0
        holds, lacking = [], []
        cost, unpriced = 0.0, 0
        for key, (name, q) in sorted(r.needs.items(), key=lambda kv: kv[1][0].lower()):
            if q <= 0:
                continue
            c = cards[key]
            need += q
            if not c.contested:
                free += q
            gets = c.gets[r.deck.id]
            if c.contested and gets > 0:
                others = [o.deck.name for o, _ in c.wants if o is not r]
                holds.append({"card": c.name, "quantity": gets, "also_wanted_by": others[:PREVIEW_DECKS],
                              "also_wanted_by_total": len(others)})
            short = q - gets
            if short > 0:
                not_owned = max(0, q - c.have)
                lacking_by_other.setdefault(key, {})[r.deck.id] = short - not_owned
                line_cost = _cost(c.unit_price, short)
                if line_cost is None:
                    unpriced += 1
                else:
                    cost += line_cost
                lacking.append({"card": c.name, "quantity": short, "not_owned": not_owned, "held_by_other_deck": short - not_owned,
                                "unit_price": c.unit_price, "price_date": c.price_date, "cost": line_cost})
        records.append({"id": r.deck.id, "name": r.deck.name, "order": position, "status": "analysed", "reason": None,
                        "need": need, "free": free, "holds": holds[:LISTED], "holds_total": len(holds),
                        "lacking": lacking[:LISTED], "lacking_total": len(lacking),
                        "stands_alone": not lacking, "independent": not lacking and not holds,
                        "independence": round(free / need, 3) if need else 1.0,
                        "cost_to_complete": round(cost, 2), "cost_unpriced": unpriced})
    for offset, d in enumerate(sorted(skipped, key=lambda d: (d.name.lower(), d.id)), 1):
        records.append({"id": d.id, "name": d.name, "order": len(order) + offset, "status": "skipped", "reason": SKIPPED_REASON,
                        "need": None, "free": None, "holds": [], "holds_total": None, "lacking": [], "lacking_total": None,
                        "stands_alone": None, "independent": None, "independence": None, "cost_to_complete": None,
                        "cost_unpriced": None})

    contested, purchases = [], []
    for c in sorted(cards.values(), key=lambda c: c.name.lower()):
        if c.deficit <= 0:
            continue
        move = None
        if c.contested:
            by_other = lacking_by_other.get(c.key, {})
            move = _move(c, order, by_other)
            wants = [{"deck_id": r.deck.id, "deck": r.deck.name, "need": q, "gets": c.gets[r.deck.id],
                      "lacking": q - c.gets[r.deck.id]} for r, q in c.wants]
            contested.append({"card": c.name, "have": c.have, "need_for_all": c.need_all, "global_deficit": c.deficit,
                              "decks": wants[:CONTESTED_DECKS], "decks_total": len(wants),
                              "options": [o for o in (move, _buy(c)) if o]})
        cost = _cost(c.unit_price, c.deficit)
        purchases.append({"card": c.name, "have": c.have, "need_for_all": c.need_all, "global_deficit": c.deficit,
                          "unit_price": c.unit_price, "cost": cost, "price_status": "priced" if cost is not None else "unpriced",
                          "price_date": c.price_date, "contested": c.contested, "move": move})
    contested.sort(key=lambda x: (-x["global_deficit"], x["card"].lower()))
    purchases.sort(key=lambda p: (p["cost"] is None, p["cost"] or 0.0, p["card"].lower()))

    analysed = [d for d in records if d["status"] == "analysed"]
    dates = [p["price_date"] for p in purchases if p["price_date"]]
    summary = {"decks_analysed": len(analysed), "decks_needing_purchase": sum(1 for d in analysed if not d["stands_alone"]),
               "finish_all_cost": round(sum(p["cost"] for p in purchases if p["cost"] is not None), 2),
               "unpriced": sum(1 for p in purchases if p["cost"] is None),
               "contested_cards": len(contested), "cards_to_buy": len(purchases)}
    rule = PRIORITY_RULE if priority else DEFAULT_RULE
    allocation = {"rule": rule, "priority_applied": bool(priority), "description": allocation_note(rule), "limits": LIMITS}
    return Analysis(decks=records, contested=contested, purchases=purchases, summary=summary, allocation=allocation,
                    decks_analysed=len(analysed), decks_skipped_count=len(skipped), decks_checked=len(decks),
                    legacy=_legacy(ready, have), prices_date=max(dates) if dates else None)


def _legacy(ready: list[ReadDeck], have: dict) -> dict:
    """The fields the overlap answer has always had (shared_cards, short_cards, cards): cards that two or more decks use, with
    the copies short, decks in name order. Each card previews at most 10 of its decks (``decks_total`` says how many)."""
    uses: dict = {}
    for r in ready:  # the decks come in name order
        for key, (name, q) in r.needs.items():
            uses.setdefault(key, (name, []))[1].append({"deck_id": r.deck.id, "deck": r.deck.name, "quantity": q})
    shared = []
    for key, (name, in_decks) in uses.items():
        if len(in_decks) < 2:
            continue
        need, owned = sum(u["quantity"] for u in in_decks), have.get(key, 0)
        shared.append({"name": name, "decks": in_decks, "need_for_all": need, "have": owned, "short": max(0, need - owned)})
    shared.sort(key=lambda s: (-s["short"], -len(s["decks"]), s["name"].lower()))
    return {"shared_cards": len(shared), "short_cards": sum(1 for s in shared if s["short"]),
            "cards": [{**s, "decks": s["decks"][:PREVIEW_DECKS], "decks_total": len(s["decks"])} for s in shared[:LEGACY_CARDS]]}
