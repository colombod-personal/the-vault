"""A user's collection as the API serves it: grouped printings, sets, timeline, stats.

Rows are grouped by printing + printing name + condition + language (the same grouping
the design prototype used). Each group gets a stable id, so clients can cache and link
to it. Prices come from the latest Scryfall snapshot, falling back to the file's own.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import date, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .importer import MAX_PRICE, user_entries
from .models import Card, Entry, Import, PriceSnapshot, User
from .prices import latest_prices, unit_price


def finite(value: float | None) -> float | None:
    """``value``, or None when it is not a finite number. Imports drop such prices; this keeps one
    stored before that (or by hand) from making every page unanswerable (JSON has no infinity)."""
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def plausible_price(value: float | None) -> float | None:
    """A price for one copy, or None when it is not one an import would accept (not finite, or
    above MAX_PRICE). A huge stored value would otherwise overflow to infinity in the totals."""
    value = finite(value)
    return value if value is not None and abs(value) <= MAX_PRICE else None


def _printing(row: Entry) -> str:
    raw = (row.extra or {}).get("Printing")
    if raw is not None:
        return raw
    return {"foil": "Foil", "etched": "Etched"}.get(row.finish, "Normal")


def group_id(name: str, set_code: str, number: str, printing: str, condition: str, language: str) -> str:
    raw = "\x1f".join([name, set_code, number, printing, condition, language])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class Group:
    id: str
    name: str
    set_code: str
    set_name: str
    number: str
    printing: str
    finish: str  # finish used for pricing
    condition: str
    language: str
    quantity: int = 0
    paid: float = 0.0
    paid_quantity: int = 0  # copies with a recorded price paid; P&L counts only these
    price: float = 0.0
    price_source: str = "file"
    low: float | None = None
    mid: float | None = None
    first_acquired: str | None = None
    last_acquired: str | None = None
    scryfall_id: str | None = None
    copies: list[dict] = field(default_factory=list, repr=False)  # the imported rows, as plain data

    @property
    def value(self) -> float:
        return round(self.price * self.quantity, 2)


# Built views are cached per version (user, last import, last price update, costs hidden), so
# paging through a big collection costs one build, not one per page. The version is checked on
# every request, so a stale view is never served. Per process; serverless instances each keep their own.
_CACHE: OrderedDict[tuple, tuple] = OrderedDict()
_CACHE_SIZE = 16
_LOCK = threading.Lock()


class CollectionView:
    def __init__(self, db: Session, user: User, *, hide_costs: bool = False):
        self.db, self.user, self.hide_costs = db, user, hide_costs
        last_import = db.execute(select(func.count(Import.id), func.max(Import.id), func.max(Import.created_at))
                                 .where(Import.user_id == user.id)).one()
        self.prices_as_of = db.scalar(select(func.max(PriceSnapshot.day)))
        cards_updated = db.scalar(select(func.max(Card.updated_at)))
        # ids alone can be reused (deleted rows, other databases), so timestamps are in the version and the engine in the key
        self.version = (f"{user.id}.{user.created_at}.{'.'.join(map(str, last_import))}."
                        f"{self.prices_as_of or '-'}.{cards_updated or '-'}.{int(hide_costs)}")
        key = (id(db.get_bind()), self.version)
        with _LOCK:
            cached = _CACHE.get(key)
            if cached is not None:
                _CACHE.move_to_end(key)
        if cached is None:
            cached = self._build()
            with _LOCK:
                _CACHE[key] = cached
                while len(_CACHE) > _CACHE_SIZE:
                    _CACHE.popitem(last=False)
        self.groups, self.months, self.imported_at, self.source = cached
        self.by_id = {g.id: g for g in self.groups}

    def _build(self) -> tuple:
        db, user = self.db, self.user
        rows = user_entries(db, user)
        ids = {r.scryfall_id for r in rows if r.scryfall_id}
        prices = latest_prices(db, ids)
        # Files without set names (Moxfield) get Scryfall's, once the printing is matched.
        set_names = dict(db.execute(select(Card.scryfall_id, Card.set_name).where(Card.scryfall_id.in_(ids))).all()) if ids else {}
        groups: dict[str, Group] = {}
        months: Counter = Counter()
        spend: Counter = Counter()
        for r in rows:
            printing = _printing(r)
            set_code = (r.extra or {}).get("Set Code") or (r.set_code or "").upper()
            gid = group_id(r.name, set_code, r.collector_number or "", printing, r.condition, r.language)
            price, from_scryfall = unit_price(r, prices.get(r.scryfall_id) if r.scryfall_id else None)
            price, paid = plausible_price(price) or 0.0, plausible_price(r.purchase_price)
            day = r.purchase_date.isoformat() if r.purchase_date else None
            g = groups.get(gid)
            if g is None:
                g = groups[gid] = Group(
                    id=gid, name=r.name, set_code=set_code, set_name=r.set_name or set_names.get(r.scryfall_id) or "",
                    number=r.collector_number or "", printing=printing, finish=r.price_finish or r.finish,
                    condition=r.condition, language=r.language, price=price,
                    price_source="scryfall" if from_scryfall else "file",
                )
            g.copies.append({"quantity": r.quantity, "folder": r.folder, "purchase_price": paid,
                             "purchase_date": day})
            g.quantity += r.quantity
            g.paid += (paid or 0.0) * r.quantity
            if paid:
                g.paid_quantity += r.quantity
            g.scryfall_id = g.scryfall_id or r.scryfall_id
            sp = r.source_prices or {}
            if day and (g.first_acquired is None or day < g.first_acquired):
                g.first_acquired = day
            if day is None or g.last_acquired is None or day >= g.last_acquired:
                g.last_acquired = day or g.last_acquired
                g.low, g.mid = plausible_price(sp.get("low", g.low)), plausible_price(sp.get("mid", g.mid))
            if day:
                months[day[:7]] += r.quantity
                spend[day[:7]] += (paid or 0.0) * r.quantity
        ordered = sorted(groups.values(), key=lambda g: (g.name.lower(), g.set_code, g.number, g.id))
        timeline = [(m, months[m], round(spend[m], 2)) for m in sorted(months)]
        latest = db.execute(select(Import.created_at, Import.source).where(Import.user_id == user.id)
                            .order_by(Import.id.desc()).limit(1)).first()
        return ordered, timeline, latest[0] if latest else None, latest[1] if latest else None

    # -- shapes ------------------------------------------------------------------------------
    def item(self, g: Group) -> dict:
        return {
            "id": g.id, "name": g.name, "set": {"code": g.set_code, "name": g.set_name},
            "collector_number": g.number, "printing": g.printing, "finish": g.finish,
            "condition": g.condition, "language": g.language, "quantity": g.quantity,
            "paid": None if self.hide_costs else round(g.paid, 2),
            "paid_quantity": None if self.hide_costs else g.paid_quantity,
            "price": {"market": g.price, "low": g.low, "mid": g.mid, "currency": "USD", "source": g.price_source},
            "value": g.value, "acquired": {"first": g.first_acquired, "last": g.last_acquired},
            "scryfall_id": g.scryfall_id,
        }

    def card_data(self, g: Group) -> dict | None:
        card = self.db.get(Card, g.scryfall_id) if g.scryfall_id else None
        if card is None:
            return None
        return {
            "scryfall_id": card.scryfall_id, "oracle_id": card.oracle_id, "name": card.name,
            "type_line": card.type_line, "mana_cost": card.mana_cost, "cmc": card.cmc,
            "colors": card.colors, "color_identity": card.color_identity, "oracle_text": card.oracle_text,
            "rarity": card.rarity, "finishes": card.finishes,
            "image": {"small": card.image_small, "normal": card.image_normal, "artist": card.artist,
                      "credit": "Image via Scryfall · © Wizards of the Coast"},
            "scryfall_uri": card.scryfall_uri,
        }

    def price_history(self, g: Group, days: int = 90) -> list[dict]:
        if not g.scryfall_id:
            return []
        rows = self.db.scalars(
            select(PriceSnapshot).where(PriceSnapshot.scryfall_id == g.scryfall_id)
            .order_by(PriceSnapshot.day.desc()).limit(days)
        )
        return [{"day": p.day.isoformat(), "price": p.for_finish(g.finish)} for p in reversed(list(rows))]

    def summary(self) -> dict:
        total_qty = sum(g.quantity for g in self.groups)
        printings: Counter = Counter()
        conditions: Counter = Counter()
        for g in self.groups:
            printings[g.printing] += g.quantity
            conditions[g.condition] += g.quantity
        last_import, last_prices = self.imported_at, self.prices_as_of
        return {
            "copies": total_qty,
            "printings": len(self.groups),
            "cards": len({g.name.split(" // ")[0].lower() for g in self.groups}),
            "sets": len({g.set_code for g in self.groups}),
            "market_value": round(sum(g.value for g in self.groups), 2),
            "paid": None if self.hide_costs else round(sum(g.paid for g in self.groups), 2),
            "costs_hidden": self.hide_costs,
            "priced_by_scryfall": sum(g.quantity for g in self.groups if g.price_source == "scryfall"),
            "by_printing": dict(printings),
            "by_condition": dict(conditions),
            "imported_at": last_import.isoformat() if last_import else None,
            "source": self.source,  # format of the last import: dragonshield, moxfield or csv
            "prices_as_of": last_prices.isoformat() if last_prices else None,
        }

    def sets(self) -> list[dict]:
        sets: dict[str, dict] = {}
        for g in self.groups:
            s = sets.setdefault(g.set_code, {"code": g.set_code, "name": g.set_name, "copies": 0,
                                             "printings": 0, "market_value": 0.0})
            s["copies"] += g.quantity
            s["printings"] += 1
            s["market_value"] += g.value
        for s in sets.values():
            s["market_value"] = round(s["market_value"], 2)
        return sorted(sets.values(), key=lambda s: (-s["market_value"], s["code"]))

    def timeline(self) -> list[dict]:
        return [{"month": m, "copies": q, "paid": None if self.hide_costs else paid} for m, q, paid in self.months]

    def stats(self, top: int = 8) -> dict:
        foil_value = sum(g.value for g in self.groups if "foil" in g.printing.lower() or g.finish != "nonfoil")
        total_value = sum(g.value for g in self.groups) or 1.0
        by_name: Counter = Counter()
        for g in self.groups:
            by_name[g.name] += g.quantity
        out = {
            "foil_share_of_value": round(foil_value / total_value, 4),
            "most_copies": [{"name": n, "copies": q} for n, q in by_name.most_common(top)],
            "most_valuable": [self.item(g) for g in sorted(self.groups, key=lambda g: -g.value)[:top]],
        }
        if not self.hide_costs:
            # only copies with a known cost: their market value against what was paid for them
            pnl = [(g.price * g.paid_quantity - g.paid, g) for g in self.groups if g.paid_quantity]
            out["biggest_gains"] = [dict(self.item(g), gain=round(d, 2)) for d, g in sorted(pnl, key=lambda x: -x[0])[:top]]
            out["biggest_losses"] = [dict(self.item(g), gain=round(d, 2)) for d, g in sorted(pnl, key=lambda x: x[0])[:top]]
        return out


def filtered(view: CollectionView, *, q: str | None = None, set_code: str | None = None,
             finish: str | None = None, condition: str | None = None, name: str | None = None) -> list[Group]:
    out = view.groups
    if q:
        needle = q.lower()
        out = [g for g in out if needle in g.name.lower() or needle in g.set_name.lower()]
    if name:
        wanted = name.split(" // ")[0].strip().lower()
        out = [g for g in out if g.name.split(" // ")[0].strip().lower() == wanted]
    if set_code:
        out = [g for g in out if g.set_code.lower() == set_code.lower()]
    if finish:
        out = [g for g in out if g.finish == finish]
    if condition:
        out = [g for g in out if g.condition == condition]
    return out


SORTS = {
    "name": lambda g: (g.name.lower(), g.set_code, g.number),
    "-value": lambda g: (-g.value, g.name.lower()),
    "value": lambda g: (g.value, g.name.lower()),
    "-quantity": lambda g: (-g.quantity, g.name.lower()),
    "set": lambda g: (g.set_code, g.number, g.name.lower()),
    "-acquired": lambda g: (-date.fromisoformat(g.last_acquired).toordinal() if g.last_acquired else 0, g.name.lower()),
}


def import_days(db: Session, user: User) -> set[date]:
    """The days the user imported a file, in the server's local calendar (as the value history is)."""
    stamps = db.scalars(select(Import.created_at).where(Import.user_id == user.id))
    return {(s if s.tzinfo else s.replace(tzinfo=timezone.utc)).astimezone().date() for s in stamps}


def history_days(db: Session, user: User, since: date | None = None) -> list:
    from .models import CollectionValue

    stmt = select(CollectionValue).where(CollectionValue.user_id == user.id)
    if since:
        stmt = stmt.where(CollectionValue.day >= since)
    return list(db.scalars(stmt.order_by(CollectionValue.day)))
