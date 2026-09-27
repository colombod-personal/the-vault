"""Build the ``collection.json`` structure the Vault front end reads.

Same shape as the prototype's static ``data/collection.json`` (``meta``,
``sets``, ``timeline``, ``cards``, ``byName``), so the UI works unchanged, with
a few additions: ``history`` (real daily values), and per card ``id``
(Scryfall id), ``fin`` (finish) and ``src`` ("scryfall" or "file" price).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from mtg_toolkits.dragonshield import CONDITION_NAMES, LANGUAGE_NAMES
from mtg_toolkits.models import Condition
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .importer import user_entries
from .models import Import, PriceSnapshot, User
from .prices import history, latest_prices, unit_price


def _printing(row) -> str:
    raw = (row.extra or {}).get("Printing")
    if raw is not None:
        return raw
    return {"foil": "Foil", "etched": "Etched"}.get(row.finish, "Normal")


def build(db: Session, user: User, *, hide_costs: bool = False) -> dict:
    """The user's collection. ``hide_costs`` blanks prices paid (for viewers of a share)."""
    rows = user_entries(db, user)
    prices = latest_prices(db, {r.scryfall_id for r in rows if r.scryfall_id})

    groups: dict[tuple, dict] = {}
    for r in rows:
        p = _printing(r)
        cond = CONDITION_NAMES[Condition(r.condition)]
        lang = LANGUAGE_NAMES.get(r.language, r.language)
        key = (r.name, r.set_code, r.collector_number, p, cond, lang)
        price, from_scryfall = unit_price(r, prices.get(r.scryfall_id) if r.scryfall_id else None)
        sp = r.source_prices or {}
        day = r.purchase_date.isoformat() if r.purchase_date else ""
        g = groups.get(key)
        if g is None:
            groups[key] = {
                "n": r.name, "s": (r.extra or {}).get("Set Code") or (r.set_code or "").upper(),
                "sn": r.set_name or "", "cn": r.collector_number or "", "p": p, "c": cond, "l": lang,
                "q": r.quantity, "pd": (r.purchase_price or 0.0) * r.quantity,
                "lo": sp.get("low"), "mi": sp.get("mid"), "mk": price, "fd": day, "ld": day,
                "id": r.scryfall_id, "fin": r.price_finish or r.finish,
                "src": "scryfall" if from_scryfall else "file",
            }
            continue
        g["q"] += r.quantity
        g["pd"] += (r.purchase_price or 0.0) * r.quantity
        if day and (not g["fd"] or day < g["fd"]):
            g["fd"] = day
        if day and day >= g["ld"]:
            g["ld"] = day
            g["lo"], g["mi"] = sp.get("low", g["lo"]), sp.get("mid", g["mi"])
        g["id"] = g["id"] or r.scryfall_id

    cards = sorted(groups.values(), key=lambda c: (c["n"].lower(), c["s"], c["cn"]))
    for c in cards:
        c["pd"] = 0.0 if hide_costs else round(c["pd"], 2)

    sets: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    timeline: Counter = Counter()
    for c in cards:
        s = sets.setdefault(c["s"], {"code": c["s"], "name": c["sn"], "qty": 0, "value": 0.0, "unique": 0})
        s["qty"] += c["q"]
        s["value"] += c["mk"] * c["q"]
        s["unique"] += 1
        b = by_name.setdefault(c["n"].lower(), {"name": c["n"], "total": 0, "value": 0.0, "entries": []})
        b["total"] += c["q"]
        b["value"] += c["mk"] * c["q"]
        b["entries"].append({k: c[k] for k in ("s", "sn", "cn", "p", "c", "q", "mk")})
    for r in rows:
        if r.purchase_date:
            timeline[r.purchase_date.isoformat()[:7]] += r.quantity
    for s in sets.values():
        s["value"] = round(s["value"], 2)
    for b in by_name.values():
        b["value"] = round(b["value"], 2)

    printings: Counter = Counter()
    conditions: Counter = Counter()
    for c in cards:
        printings[c["p"]] += c["q"]
        conditions[c["c"]] += c["q"]

    last_import = db.scalar(select(func.max(Import.created_at)).where(Import.user_id == user.id))
    last_prices = db.scalar(select(func.max(PriceSnapshot.day)))
    return {
        "meta": {
            "totalQty": sum(c["q"] for c in cards),
            "totalPaid": round(sum(c["pd"] for c in cards), 2),
            "totalMarket": round(sum(c["mk"] * c["q"] for c in cards), 2),
            "uniqueEntries": len(cards),
            "uniqueSets": len(sets),
            "printings": dict(printings),
            "conditions": dict(conditions),
            "generatedAt": (last_prices.isoformat() if last_prices else
                            last_import.isoformat() if last_import else None),
            "importedAt": last_import.isoformat() if last_import else None,
            "pricedFromScryfall": sum(c["q"] for c in cards if c["src"] == "scryfall"),
            "costsHidden": hide_costs,
        },
        "sets": sorted(sets.values(), key=lambda s: -s["value"]),
        "timeline": [{"month": m, "qty": q} for m, q in sorted(timeline.items())],
        "cards": cards,
        "byName": by_name,
        "history": [dict(h, cost=None) for h in history(db, user)] if hide_costs else history(db, user),
    }
