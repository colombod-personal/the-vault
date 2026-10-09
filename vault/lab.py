"""The numbers behind the Lab (docs/lab-design.md, #164): which copies are spare, and profit and loss over the holdings
whose cost and current price are both known.

Both read the person's :class:`~vault.collection_view.CollectionView`, so a "holding" or a "row" here is exactly a printing
group of ``GET /collection/cards`` (printing, finish, condition and language: its stable id is the group id). Nothing in this
module reads another person's data: the routes that use it exist for the signed-in person only, never under ``/shared``.

Spare copies (section 2a). A card's spare copies are ``have - needed``, counted by card name as everywhere else (front face,
any printing), where ``needed`` is the total the saved decks need at the same time (summed over decks, basic lands left out).
This is the design's own definition and :func:`deck_needs` is the one place that reads the decks: when the extended
``GET /decks/overlap`` (docs/deck-independence.md) is on main, its ``need_for_all`` replaces it without touching the rest.

Which physical copies are spare is fixed by :func:`allocate`: the decks keep the cheapest copies of the name; at equal prices
the copies not marked for trade are kept first; ties break by the group id. An unpriced copy has price 0 in the collection
view, so it sorts as the cheapest.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from mtg_toolkits import delta
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import deck_text
from .collection_view import CollectionView, Group
from .models import Card, Deck

BASICS = {"plains", "island", "swamp", "mountain", "forest", "wastes", "snow-covered plains", "snow-covered island",
          "snow-covered swamp", "snow-covered mountain", "snow-covered forest", "snow-covered wastes"}
PREVIEW_ROWS = 10  # printing rows listed under one spare name; the rest are one link away (/collection/spare/printings)

NO_DECKS_NOTE = ("Save a deck to see which copies are spare. Spare means beyond your saved decks, so without a saved deck "
                 "the Vault does not call any copy spare.")
SPARE_NOTE = ("Spare means copies beyond what your saved decks need at the same time (basic lands left out). The Vault does "
              "not know decks you have not saved, or play-sets, so spare never means worthless to you. Your decks keep "
              "your cheapest copies of a card (trade status only breaks ties between equal prices), so the value is an "
              "upper bound on what selling releases. Copies with no price count as spare but add nothing to the value.")


def card_key(name: str | None) -> str:
    """The name a card is counted under: the front face, case-folded (the same as ``delta.BY_CARD``)."""
    return (name or "").split(" // ")[0].strip().casefold()


def scryfall_search(name: str) -> str:
    return "https://scryfall.com/search?q=" + quote(f'!"{(name or "").split(" // ")[0].strip()}"')


def scryfall_links(db: Session, ids) -> dict[str, str]:
    """Scryfall's own page for each printing the Vault holds card data for; a printing it does not hold has no link here."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {i: uri for i, uri in db.execute(select(Card.scryfall_id, Card.scryfall_uri).where(Card.scryfall_id.in_(ids)))
            if uri}


# -- spare copies -----------------------------------------------------------------------------------------

@dataclass
class Needs:
    copies: dict[str, int]  # card key -> copies all the saved decks need together
    decks: dict[str, int]  # card key -> how many saved decks use it
    analysed: int = 0
    skipped: int = 0  # saved decks the parser could not read: they count for nothing here, and the answer says so


def deck_needs(db: Session, user_id: int) -> Needs:
    needs = Needs({}, {})
    for deck in db.scalars(select(Deck).where(Deck.user_id == user_id).order_by(Deck.id)):
        try:
            needed = delta.aggregate(deck_text.parse_text(deck.text).to_entries(), delta.BY_CARD)
        except (ValueError, OverflowError):  # an unreadable saved deck is skipped, as /decks/overlap does
            needs.skipped += 1
            continue
        needs.analysed += 1
        for (key,), entry in needed.items():
            if key in BASICS or entry.quantity <= 0:
                continue
            needs.copies[key] = needs.copies.get(key, 0) + entry.quantity
            needs.decks[key] = needs.decks.get(key, 0) + 1
    return needs


def decks_stamp(db: Session, user_id: int) -> str:
    """Changes whenever the person's saved decks do (for the ETag of an answer that reads them)."""
    count, newest, changed = db.execute(select(func.count(Deck.id), func.max(Deck.id), func.max(Deck.updated_at))
                                        .where(Deck.user_id == user_id)).one()
    return f"d{count}.{newest or '-'}.{changed or '-'}"


def allocate(groups: list[Group], need: int) -> dict[str, tuple[Group, int, int]]:
    """The decks keep ``need`` copies of a name; what is left is spare. Returns group id -> (group, spare copies, of those
    marked for trade). Copies are taken cheapest first; at equal prices the unmarked ones first; then by group id."""
    lots = []
    for g in groups:
        marked = min(g.trade_marked, g.quantity)  # an import can store more marked copies than there are copies
        lots += [(g.price, 0, g.id, g, g.quantity - marked), (g.price, 1, g.id, g, marked)]
    lots.sort(key=lambda lot: lot[:3])
    left, spare = need, {}
    for _, is_marked, gid, g, quantity in lots:
        keep = min(quantity, left)
        left -= keep
        if quantity > keep:
            _, count, marked_count = spare.get(gid, (g, 0, 0))
            spare[gid] = (g, count + quantity - keep, marked_count + (quantity - keep if is_marked else 0))
    return spare


def spare_row(g: Group, count: int, marked: int) -> dict:
    priced = g.price > 0
    return {"id": g.id, "scryfall_id": g.scryfall_id, "set": {"code": g.set_code, "name": g.set_name},
            "collector_number": g.number, "printing": g.printing, "finish": g.finish, "condition": g.condition,
            "language": g.language, "spare_quantity": count, "unit_price": g.price if priced else None,
            "price_status": "priced" if priced else "unpriced", "trade_marked_quantity": marked,
            "scryfall_link": None, "scryfall_search": scryfall_search(g.name)}


def spare_name(groups: list[Group], key: str, needs: Needs) -> dict | None:
    """One name's spare copies, or None when none are spare. ``rows`` are all the spare printing rows, cheapest first."""
    have = sum(g.quantity for g in groups)
    needed = needs.copies.get(key, 0)
    if have - needed <= 0:
        return None
    rows = sorted((spare_row(g, count, marked) for g, count, marked in allocate(groups, needed).values()),
                  key=lambda r: (r["unit_price"] or 0.0, r["id"]))
    priced = sum(r["spare_quantity"] for r in rows if r["price_status"] == "priced")
    shown = max(groups, key=lambda g: (g.quantity, g.name))  # the spelling of the name most copies carry
    return {"name": shown.name, "key": key, "have": have, "needed": needed, "spare": have - needed,
            "market_value_of_spare": round(sum(r["spare_quantity"] * r["unit_price"] for r in rows if r["unit_price"]), 2),
            "priced_copies": priced, "unpriced_copies": have - needed - priced, "deck_count": needs.decks.get(key, 0),
            "printing_rows_total": len(rows), "rows": rows}


@dataclass
class SpareReport:
    status: str  # ok | no_decks | empty_collection
    needs: Needs
    names: list[dict]  # sorted: the value of the spare copies, dearest first; names with only unpriced copies last; then by name
    summary: dict
    note: str


def spare_report(view: CollectionView, needs: Needs) -> SpareReport:
    names: list[dict] = []
    by_key: dict[str, list[Group]] = {}
    for g in view.groups:
        key = card_key(g.name)
        if key not in BASICS:  # basic lands are left out, as in the deck checks
            by_key.setdefault(key, []).append(g)
    if not view.groups:
        status = "empty_collection"
    elif not needs.analysed:  # no saved deck (or none readable): never call the whole collection spare
        status = "no_decks"
    else:
        status = "ok"
        names = [n for key, groups in by_key.items() if (n := spare_name(groups, key, needs))]
        names.sort(key=lambda n: (-n["market_value_of_spare"], n["key"]))
    priced = sum(n["priced_copies"] for n in names)
    copies = sum(n["spare"] for n in names)
    summary = {"names": len(names), "copies": copies, "market_value": round(sum(n["market_value_of_spare"] for n in names), 2),
               "priced_copies": priced, "unpriced_copies": copies - priced}
    return SpareReport(status, needs, names, summary, SPARE_NOTE if status == "ok" else NO_DECKS_NOTE if status == "no_decks"
                       else "Import your collection to see which copies are spare.")


# -- profit and loss --------------------------------------------------------------------------------------

REASON_NO_COST = "no price paid is known"
REASON_NO_PRICE = "prices are not available yet"
REASON_EMPTY = "the collection is empty"


@dataclass
class PnlReport:
    winners: list[tuple[float, Group]]  # gain > 0, most profitable first
    losers: list[tuple[float, Group]]  # gain < 0, biggest loss first
    counts: dict
    net_gain: float | None  # null while no holding is counted


def pnl_report(view: CollectionView) -> PnlReport:
    """A holding counts only when both its price paid and a current market price are known. The collection view turns a
    missing market price into 0, which would show an unpriced holding as a total loss, so those are left out and counted."""
    total = covered = unknown = unpriced = counted = 0
    net = 0.0
    winners, losers = [], []
    for g in view.groups:
        total += g.quantity
        if not (g.paid > 0 and g.paid_quantity):  # the same test as vault.analytics.pnl: a printing's cost is known
            unknown += g.quantity
            continue
        unknown += g.quantity - g.paid_quantity  # the copies of this printing with no price paid
        if g.price <= 0:
            unpriced += g.paid_quantity
            continue
        covered += g.paid_quantity
        counted += 1
        gain = round(g.price * g.paid_quantity - g.paid, 2)
        net += gain
        if gain > 0:
            winners.append((gain, g))
        elif gain < 0:
            losers.append((gain, g))
    winners.sort(key=lambda p: (-p[0], p[1].name.lower(), p[1].id))
    losers.sort(key=lambda p: (p[0], p[1].name.lower(), p[1].id))
    counts = {"total_copies": total, "covered_copies": covered, "unknown_cost_copies": unknown,
              "unpriced_market_copies": unpriced}
    return PnlReport(winners, losers, counts, round(net, 2) if counted else None)


def pnl_reason(counts: dict) -> str | None:
    """Why there is nothing to show, chosen from the counts (None while some holding is counted)."""
    if counts["covered_copies"]:
        return None
    if not counts["total_copies"]:
        return REASON_EMPTY
    return REASON_NO_COST if counts["unknown_cost_copies"] == counts["total_copies"] else REASON_NO_PRICE


def pnl_item(g: Group, gain: float) -> dict:
    """A counted holding: what was paid for its copies with a known cost, what they are worth now, the difference."""
    paid = round(g.paid, 2)
    return {"id": g.id, "name": g.name, "set": {"code": g.set_code, "name": g.set_name}, "collector_number": g.number,
            "printing": g.printing, "finish": g.finish, "condition": g.condition, "language": g.language,
            "quantity": g.quantity, "copies": g.paid_quantity, "paid": paid, "unit_price": g.price,
            "market_value": round(g.price * g.paid_quantity, 2), "gain": gain,
            "gain_pct": round(gain / paid * 100, 2) if paid else None, "scryfall_id": g.scryfall_id,
            "scryfall_link": None, "scryfall_search": scryfall_search(g.name)}
