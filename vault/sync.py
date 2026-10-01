"""Daily Scryfall sync: match collection rows, store card data and today's prices.

Runs outside the web request cycle (GitHub Actions cron, see
``jobs/sync_prices.py``) because the bulk file is ~75 MB and matching every
user's collection takes longer than a serverless request should.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime, timezone
from pathlib import Path

from mtg_toolkits.scryfall import Card, card_matches_keys, index_keys, iter_bulk_file, resolve_offline
from sqlalchemy import bindparam, select, update
from sqlalchemy.orm import Session

from . import models
from .models import Entry, PriceSnapshot
from .prices import compute_values, upsert  # noqa: F401  (upsert: re-exported)


EXACT = ("set_number", "id")


def wanted_cards(db: Session, bulk: Iterable[dict]) -> Iterator[Card]:
    """Only keep bulk objects that could match someone's collection (saves memory). The keys come
    from the library, normalised exactly as ``resolve_offline`` matches (aliases, case, numbers)."""
    keys = index_keys(r.to_collection_entry() for r in db.scalars(select(Entry)))
    for obj in bulk:
        if obj.get("object") == "card" and card_matches_keys(obj, keys):
            yield Card.from_json(obj)


def card_row(c: Card) -> dict:
    return {
        "scryfall_id": c.id, "oracle_id": c.oracle_id, "name": c.name, "set_code": c.set_code,
        "set_name": c.set_name, "collector_number": c.collector_number, "rarity": c.rarity,
        "type_line": c.type_line, "mana_cost": c.mana_cost, "cmc": c.cmc, "colors": c.colors,
        "color_identity": c.color_identity, "oracle_text": c.oracle_text, "finishes": c.finishes,
        "power": c.power, "toughness": c.toughness, "loyalty": c.loyalty, "layout": c.layout,
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

    # The matches are written by id with plain UPDATEs, not through the loaded rows: an import
    # that replaced someone's collection while this ran deleted some of these rows, and an ORM
    # flush would then fail the whole sync (everyone's prices with it). Gone rows are skipped.
    updates = []
    for row, res in zip(todo, resolve_offline([fresh(r) for r in todo], cards)):
        if res.card is None:
            updates.append({"b_id": row.id, "b_sid": None, "b_method": None, "b_finish": None})
            continue
        price_finish = None
        # Price an etched-only (or foil-only) printing by its real finish, but only when we
        # know the exact printing; a name-only match picks an arbitrary one.
        only = res.card.finishes[0] if len(res.card.finishes) == 1 else None
        if res.method in EXACT and only in ("nonfoil", "foil", "etched") and only != row.finish:
            price_finish = only
        updates.append({"b_id": row.id, "b_sid": res.card.id, "b_method": res.method, "b_finish": price_finish})
        matched[res.card.id] = res.card
        methods[res.method] = methods.get(res.method, 0) + 1
    db.expunge_all()
    if updates:
        table = Entry.__table__
        db.connection().execute(
            update(table).where(table.c.id == bindparam("b_id"))
            .values(scryfall_id=bindparam("b_sid"), match_method=bindparam("b_method"), price_finish=bindparam("b_finish")),
            updates)

    upsert(db, models.Card, [card_row(c) for c in matched.values()], ("scryfall_id",))
    upsert(db, PriceSnapshot, [price_row(c, day) for c in matched.values()], ("scryfall_id", "day"))
    db.commit()
    users = compute_values(db, day)
    return {
        "day": day.isoformat(), "rows": len(rows), "resolved_now": sum(methods.values()),
        "methods": methods, "unmatched": sum(1 for u in updates if u["b_sid"] is None),
        "printings_priced": len(matched), "users": users,
    }


def sync_from_file(db: Session, path: str | Path, day: date | None = None) -> dict:
    return sync(db, wanted_cards(db, iter_bulk_file(path)), day)
