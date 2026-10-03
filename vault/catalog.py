"""Card data for the browser and agents, from the Vault instead of Scryfall.

The daily sync keeps the ``cards`` table (and prices) for every printing someone owns. Lookups
answer from there; a miss, or ``refresh``, is fetched from Scryfall by the server (one shared,
rate-limited client per process), stored, and answered. So the browser never calls Scryfall's
API. Card images stay on Scryfall's image CDN (cards.scryfall.io), which Scryfall asks sites to
hotlink, with the artist credited wherever they are shown.

Answers use Scryfall's card shape (``id``, ``set``, ``image_uris``, ``prices`` as strings), so
clients written against Scryfall keep working.
"""

from __future__ import annotations

import threading
import time
from datetime import date
from typing import Any, Callable

import httpx
from mtg_toolkits import normalize_collector_number, normalize_set_code
from mtg_toolkits.http import ApiError
from mtg_toolkits.scryfall import ScryfallClient
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .models import Card, PriceSnapshot
from .prices import plausible_price
from .sync import card_row, price_row, upsert

MAX_IDENTIFIERS = 75  # Scryfall's own limit per /cards/collection request
SETS_TTL = 24 * 3600


class Catalog:
    def __init__(self, transport: httpx.BaseTransport | None = None, rewrite_image: Callable[[str], str] | None = None):
        self._transport = transport
        self._client: ScryfallClient | None = None
        self._lock = threading.Lock()  # one Scryfall call at a time per process: keeps the rate limit
        self._sets: tuple[float, list[dict]] | None = None
        self._rewrite = rewrite_image or (lambda url: url)

    def _scryfall(self) -> ScryfallClient:
        if self._client is None:
            self._client = ScryfallClient(client=httpx.Client(transport=self._transport, timeout=15,
                                                              follow_redirects=True))
            # Someone is waiting on this answer, and a 429 retry would sleep 30 s holding the lock.
            # Fail fast instead: the answer says ``unavailable`` and the caller retries later.
            self._client.max_retries = 0
        return self._client

    # -- cards ------------------------------------------------------------------------------
    def lookup(self, db: Session, identifiers: list[dict[str, str]], *, refresh: bool = False) -> dict[str, Any]:
        """Scryfall's /cards/collection, answered by the Vault: ``{"data": [...], "not_found": [...],
        "unavailable": bool}``. ``unavailable`` is true when Scryfall was needed but didn't answer.
        Dragon Shield set codes (kept as-is in collections) are translated to Scryfall's; ``not_found``
        lists identifiers as asked."""
        asked, identifiers = identifiers, [_scryfall_ident(i) for i in identifiers]
        found: dict[int, Card] = {}
        if not refresh:
            for i, ident in enumerate(identifiers):
                if (card := _find(db, ident)) is not None:
                    found[i] = card
        missing = [i for i in range(len(identifiers)) if i not in found]
        unavailable = False
        if missing:
            try:
                with self._lock:
                    if not refresh:  # while this request waited, another may have stored these cards
                        db.rollback()  # see what the other request committed
                        for i in list(missing):
                            if (card := _find(db, identifiers[i])) is not None:
                                found[i] = card
                        missing = [i for i in missing if i not in found]
                    cards, _ = self._scryfall().collection([identifiers[i] for i in missing]) if missing else ([], [])
                    if cards:  # stored before the lock is released, so the next request finds them
                        today = date.today()
                        upsert(db, Card, [card_row(c) for c in cards], ("scryfall_id",))
                        upsert(db, PriceSnapshot, [price_row(c, today) for c in cards], ("scryfall_id", "day"))
                        db.commit()
            except (ApiError, httpx.HTTPError):
                unavailable = True
            for i in missing:
                if (card := _find(db, identifiers[i])) is not None:
                    found[i] = card
        prices = _latest_prices(db, [c.scryfall_id for c in found.values()])
        data, seen = [], set()
        for i in sorted(found):
            card = found[i]
            if card.scryfall_id not in seen:
                seen.add(card.scryfall_id)
                data.append(self._card_json(card, prices.get(card.scryfall_id)))
        return {"data": data, "not_found": [asked[i] for i in range(len(identifiers)) if i not in found],
                "unavailable": unavailable}

    def _card_json(self, c: Card, p: PriceSnapshot | None) -> dict[str, Any]:
        # Scryfall sends prices as strings; a stored price no import would accept is no price
        money = lambda v: None if (v := plausible_price(v)) is None else f"{v:.2f}"  # noqa: E731
        return {
            "object": "card", "id": c.scryfall_id, "oracle_id": c.oracle_id, "name": c.name,
            "set": c.set_code, "set_name": c.set_name, "collector_number": c.collector_number,
            "rarity": c.rarity, "type_line": c.type_line, "mana_cost": c.mana_cost, "cmc": c.cmc,
            "colors": c.colors or [], "color_identity": c.color_identity or [], "oracle_text": c.oracle_text,
            "power": c.power, "toughness": c.toughness, "loyalty": c.loyalty, "layout": c.layout,
            "finishes": c.finishes or [], "artist": c.artist, "scryfall_uri": c.scryfall_uri,
            "image_uris": {k: self._rewrite(v) for k, v in (("small", c.image_small), ("normal", c.image_normal)) if v},
            "prices": {k: money(getattr(p, k, None)) for k in ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "eur_etched")},
            "prices_as_of": p.day.isoformat() if p else None,
        }

    # -- sets -------------------------------------------------------------------------------
    def sets(self) -> list[dict[str, Any]]:
        """Every Magic set (code, name, icon, release date, type), fetched from Scryfall at most
        once a day per process. Raises ApiError/HTTPError when Scryfall is down and nothing is cached."""
        if (fresh := self._fresh_sets()) is not None:
            return fresh
        with self._lock:
            if (fresh := self._fresh_sets()) is not None:  # fetched while this request waited
                return fresh
            try:
                raw = self._scryfall().sets()
            except (ApiError, httpx.HTTPError):
                if self._sets:
                    return self._sets[1]  # stale beats nothing
                raise
            items = self._set_items(raw)
            self._sets = (time.monotonic(), items)
            return items

    def cached_sets(self) -> list[dict[str, Any]]:
        """The set list this process already has (possibly stale), without calling Scryfall; [] if none."""
        cached = self._sets
        return cached[1] if cached else []

    def _fresh_sets(self) -> list[dict[str, Any]] | None:
        cached = self._sets
        return cached[1] if cached and time.monotonic() - cached[0] < SETS_TTL else None

    def _set_items(self, raw: list[dict]) -> list[dict[str, Any]]:
        return [{"code": s["code"], "name": s.get("name"), "icon_svg_uri": self._rewrite(s["icon_svg_uri"]) if s.get("icon_svg_uri") else None,
                  "released_at": s.get("released_at"), "set_type": s.get("set_type"),
                  "parent_set_code": s.get("parent_set_code")} for s in raw]


def _scryfall_ident(ident: dict[str, str]) -> dict[str, str]:
    return {**ident, "set": normalize_set_code(ident["set"])} if ident.get("set") else ident


def _find(db: Session, ident: dict[str, str]) -> Card | None:
    if ident.get("id"):
        return db.get(Card, ident["id"])
    if ident.get("set") and ident.get("collector_number"):
        raw = str(ident["collector_number"]).strip()  # as written, lower-cased, or as the library matches it
        numbers = {raw, raw.lower(), normalize_collector_number(raw) or raw}
        return db.scalar(select(Card).where(Card.set_code == ident["set"].lower(),
                                            Card.collector_number.in_(numbers)).limit(1))
    if ident.get("name"):
        name = ident["name"].strip().lower()
        front = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + " // %"  # "Fire" → "Fire // Ice"
        query = select(Card).where(or_(func.lower(Card.name) == name, func.lower(Card.name).like(front, escape="\\")))
        if ident.get("set"):
            query = query.where(Card.set_code == ident["set"].lower())
        return db.scalar(query.order_by(Card.updated_at.desc()).limit(1))
    return None


def _latest_prices(db: Session, ids: list[str]) -> dict[str, PriceSnapshot]:
    if not ids:
        return {}
    latest = (select(PriceSnapshot.scryfall_id, func.max(PriceSnapshot.day).label("day"))
              .where(PriceSnapshot.scryfall_id.in_(ids)).group_by(PriceSnapshot.scryfall_id).subquery())
    rows = db.scalars(select(PriceSnapshot).join(latest, (PriceSnapshot.scryfall_id == latest.c.scryfall_id)
                                                 & (PriceSnapshot.day == latest.c.day)))
    return {r.scryfall_id: r for r in rows}
