"""Latest prices per printing and per-user daily collection value."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import CollectionValue, Entry, PriceSnapshot, User


def latest_prices(db: Session, scryfall_ids: set[str]) -> dict[str, PriceSnapshot]:
    """The most recent snapshot for each printing."""
    if not scryfall_ids:
        return {}
    latest = (
        select(PriceSnapshot.scryfall_id, func.max(PriceSnapshot.day).label("day"))
        .where(PriceSnapshot.scryfall_id.in_(scryfall_ids))
        .group_by(PriceSnapshot.scryfall_id)
        .subquery()
    )
    rows = db.scalars(
        select(PriceSnapshot).join(
            latest, (PriceSnapshot.scryfall_id == latest.c.scryfall_id) & (PriceSnapshot.day == latest.c.day)
        )
    )
    return {p.scryfall_id: p for p in rows}


def unit_price(row: Entry, snap: PriceSnapshot | None) -> tuple[float, bool]:
    """(price, from_scryfall). Falls back to the file's own market price."""
    if snap is not None:
        price = snap.for_finish(row.price_finish or row.finish)
        if price is not None:
            return price, True
    return float((row.source_prices or {}).get("market") or 0.0), False


def compute_values(db: Session, day: date) -> int:
    """Write every user's collection value for ``day``. Returns users processed."""
    rows = list(db.scalars(select(Entry)))
    prices = latest_prices(db, {r.scryfall_id for r in rows if r.scryfall_id})
    totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0, 0])
    for r in rows:
        price, priced = unit_price(r, prices.get(r.scryfall_id) if r.scryfall_id else None)
        t = totals[r.user_id]
        t[0] += price * r.quantity
        t[1] += (r.purchase_price or 0.0) * r.quantity
        t[2] += r.quantity
        t[3] += r.quantity if priced else 0
    for user_id, (market, cost, copies, priced) in totals.items():
        db.merge(CollectionValue(
            user_id=user_id, day=day, market_usd=round(market, 2), cost_usd=round(cost, 2),
            copies=int(copies), priced_copies=int(priced),
        ))
    db.commit()
    return len(totals)


def history(db: Session, user: User) -> list[dict]:
    rows = db.scalars(select(CollectionValue).where(CollectionValue.user_id == user.id).order_by(CollectionValue.day))
    return [
        {"day": v.day.isoformat(), "market": v.market_usd, "cost": v.cost_usd,
         "copies": v.copies, "priced": v.priced_copies}
        for v in rows
    ]
