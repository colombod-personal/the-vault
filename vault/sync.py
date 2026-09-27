"""Daily Scryfall sync: match collection rows, store card data and today's prices.

Runs outside the web request cycle (GitHub Actions cron, see
``jobs/sync_prices.py``) because the bulk file is ~75 MB and matching every
user's collection takes longer than a serverless request should.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path

from mtg_toolkits.scryfall import Card, iter_bulk_file, resolve_offline
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from . import models
from .models import Entry, PriceSnapshot
from .prices import compute_values


EXACT = ("set_number", "id")


def _front(name: str) -> str:
    return name.split(" // ")[0].strip().lower()


def wanted_cards(db: Session, bulk: Iterable[dict]) -> Iterator[Card]:
    """Only keep bulk objects that could match someone's collection (saves memory)."""
    rows = db.execute(select(Entry.set_code, Entry.collector_number, Entry.name, Entry.scryfall_id)).all()
    ids = {r.scryfall_id for r in rows if r.scryfall_id}
    names = {_front(r.name) for r in rows}
    for obj in bulk:
        if obj.get("object") != "card":
            continue
        faces = [f.get("name", "") for f in obj.get("card_faces") or []]
        if obj.get("id") in ids or _front(obj.get("name", "")) in names or any(_front(f) in names for f in faces):
            yield Card.from_json(obj)


def upsert(db: Session, model, rows: list[dict], keys: tuple[str, ...]) -> None:
    if not rows:
        return
    insert = postgresql.insert if db.bind.dialect.name == "postgresql" else sqlite.insert
    table = model.__table__
    for i in range(0, len(rows), 1000):
        stmt = insert(table).values(rows[i:i + 1000])
        update = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in keys}
        db.execute(stmt.on_conflict_do_update(index_elements=list(keys), set_=update))


def card_row(c: Card) -> dict:
    return {
        "scryfall_id": c.id, "oracle_id": c.oracle_id, "name": c.name, "set_code": c.set_code,
        "set_name": c.set_name, "collector_number": c.collector_number, "rarity": c.rarity,
        "type_line": c.type_line, "mana_cost": c.mana_cost, "cmc": c.cmc, "colors": c.colors,
        "color_identity": c.color_identity, "oracle_text": c.oracle_text, "finishes": c.finishes,
        "image_small": c.image_uris.get("small"), "image_normal": c.image_uris.get("normal"),
        "artist": c.raw.get("artist") or next((f.get("artist") for f in c.raw.get("card_faces") or [] if f.get("artist")), None),
        "scryfall_uri": c.scryfall_uri, "updated_at": datetime.now(timezone.utc),
    }


def price_row(c: Card, day: date) -> dict:
    p = c.prices
    return {
        "scryfall_id": c.id, "day": day, "usd": p.usd, "usd_foil": p.usd_foil, "usd_etched": p.usd_etched,
        "eur": p.eur, "eur_foil": p.eur_foil, "eur_etched": p.eur_etched,
    }


def sync(db: Session, cards: Iterable[Card], day: date | None = None) -> dict:
    """Match every collection row against ``cards`` and record today's data."""
    day = day or date.today()
    cards = list(cards)
    rows = list(db.scalars(select(Entry)))
    by_id = {c.id: c for c in cards}
    matched: dict[str, Card] = {}
    methods: dict[str, int] = {}

    # Rows already matched by exact printing keep their id; everything else is (re)resolved,
    # so a later-added alias or better set code upgrades name-only matches.
    todo = []
    for r in rows:
        if r.scryfall_id in by_id and r.match_method in EXACT:
            matched[r.scryfall_id] = by_id[r.scryfall_id]
        else:
            todo.append(r)

    def fresh(r: Entry):
        # Drop the previous guess: re-resolving by its id would relabel a name-only match as exact.
        e = r.to_collection_entry()
        e.scryfall_id = None
        return e

    for row, res in zip(todo, resolve_offline([fresh(r) for r in todo], cards)):
        if res.card is None:
            row.scryfall_id = row.match_method = row.price_finish = None
            continue
        row.scryfall_id, row.match_method = res.card.id, res.method
        row.price_finish = None
        # Price an etched-only (or foil-only) printing by its real finish, but only when we
        # know the exact printing; a name-only match picks an arbitrary one.
        only = res.card.finishes[0] if len(res.card.finishes) == 1 else None
        if res.method in EXACT and only in ("nonfoil", "foil", "etched") and only != row.finish:
            row.price_finish = only
        matched[res.card.id] = res.card
        methods[res.method] = methods.get(res.method, 0) + 1

    upsert(db, models.Card, [card_row(c) for c in matched.values()], ("scryfall_id",))
    upsert(db, PriceSnapshot, [price_row(c, day) for c in matched.values()], ("scryfall_id", "day"))
    db.commit()
    users = compute_values(db, day)
    return {
        "day": day.isoformat(), "rows": len(rows), "resolved_now": sum(methods.values()),
        "methods": methods, "unmatched": sum(1 for r in rows if not r.scryfall_id),
        "printings_priced": len(matched), "users": users,
    }


def sync_from_file(db: Session, path: str | Path, day: date | None = None) -> dict:
    return sync(db, wanted_cards(db, iter_bulk_file(path)), day)
