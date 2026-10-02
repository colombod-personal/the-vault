"""Latest prices per printing and per-user daily collection value."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
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


def upsert(db: Session, model, rows: list[dict], keys: tuple[str, ...]) -> None:
    """INSERT … ON CONFLICT DO UPDATE: one statement, so concurrent writers of a row never collide."""
    if not rows:
        return
    insert = postgresql.insert
    table = model.__table__
    for i in range(0, len(rows), 1000):
        stmt = insert(table).values(rows[i:i + 1000])
        update = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in keys}
        db.execute(stmt.on_conflict_do_update(index_elements=list(keys), set_=update))


def compute_values(db: Session, day: date, user_id: int | None = None, *, commit: bool = True) -> int:
    """Write every user's (or one user's) collection value for ``day``. Returns users processed.
    ``commit=False`` leaves it in the caller's transaction."""
    query = select(Entry) if user_id is None else select(Entry).where(Entry.user_id == user_id)
    rows = list(db.scalars(query))
    prices = latest_prices(db, {r.scryfall_id for r in rows if r.scryfall_id})
    totals: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0, 0])
    for r in rows:
        price, priced = unit_price(r, prices.get(r.scryfall_id) if r.scryfall_id else None)
        t = totals[r.user_id]
        t[0] += price * r.quantity
        t[1] += (r.purchase_price or 0.0) * r.quantity
        t[2] += r.quantity
        t[3] += r.quantity if priced else 0
    # An upsert, not select-then-insert: the daily sync and a user's first import of the day can
    # write the same (user, day) row at once.
    upsert(db, CollectionValue, [
        {"user_id": user_id, "day": day, "market_usd": round(market, 2), "cost_usd": round(cost, 2),
         "copies": int(copies), "priced_copies": int(priced)}
        for user_id, (market, cost, copies, priced) in totals.items()], ("user_id", "day"))
    if commit:
        db.commit()
    return len(totals)


def history(db: Session, user: User) -> list[dict]:
    rows = db.scalars(select(CollectionValue).where(CollectionValue.user_id == user.id).order_by(CollectionValue.day))
    return [
        {"day": v.day.isoformat(), "market": v.market_usd, "cost": v.cost_usd,
         "copies": v.copies, "priced": v.priced_copies}
        for v in rows
    ]
