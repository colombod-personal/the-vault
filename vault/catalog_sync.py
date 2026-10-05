"""Load the catalog (docs/catalog-design.md) from Scryfall's bulk files.

Each loader takes already-parsed objects, so tests feed it small lists and the job feeds it
``iter_bulk_file``. Loads are **diffs**: rows whose content did not change are not touched, rows
that disappeared are deleted, and a card whose legality changed leaves a ``legality_changes`` row.
That keeps Neon's change history small (no whole-table rewrites) and the job safe to re-run.
Everything loaded here is third-party material: it is shown with its provenance (``vault.provenance``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import date, datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from .models import (CatalogSource, LegalityChange, OracleCard, OraclePrice, OracleTag, OracleTagLink, Ruling)

# Layouts that are not playable cards (art series cards have their own Oracle ids but no rules).
SKIPPED_LAYOUTS = {"art_series"}

# Tags whose links we keep, with all of their descendants (the tag tree is rolled up by the tools).
# Start from the roles people ask about; adding a root later is a re-run of the job, not a migration.
TAG_ROOTS = (
    "ramp", "mana-rock", "mana-dork", "land-ramp", "sweeper", "counterspell", "removal", "spot-removal",
    "draw-engine", "repeatable-pure-draw", "card-advantage", "tutor", "sacrifice-outlet", "recursion",
    "evasion", "reanimate", "protection", "lifegain", "repeatable-creature-tokens",
)

CHUNK = 1000


def digest(value) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def chunks(items: list, size: int = CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _upsert(db: Session, model, rows: list[dict], keys: tuple[str, ...]) -> None:
    table = model.__table__
    for part in chunks(rows):
        stmt = postgresql.insert(table).values(part)
        update = {c.name: stmt.excluded[c.name] for c in table.columns if c.name not in keys}
        db.execute(stmt.on_conflict_do_update(index_elements=list(keys), set_=update) if update
                   else stmt.on_conflict_do_nothing(index_elements=list(keys)))


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _faces(obj: dict) -> list | None:
    faces = obj.get("card_faces")
    if not faces:
        return None
    keep = ("name", "mana_cost", "type_line", "oracle_text", "power", "toughness", "loyalty", "defense")
    return [{k: f[k] for k in keep if f.get(k) is not None} for f in faces]


def _artist(obj: dict) -> str | None:
    return obj.get("artist") or next((f["artist"] for f in obj.get("card_faces") or [] if f.get("artist")), None)


def _image(obj: dict) -> str | None:
    """Scryfall's own link to the front face's image, as given (never built by us)."""
    uris = obj.get("image_uris") or next((f["image_uris"] for f in obj.get("card_faces") or [] if f.get("image_uris")), {})
    return uris.get("normal")


def oracle_card_row(obj: dict) -> dict:
    row = {
        "oracle_id": obj["oracle_id"], "name": obj["name"], "layout": obj.get("layout"),
        "mana_cost": obj.get("mana_cost"), "cmc": obj.get("cmc"), "type_line": obj.get("type_line"),
        "oracle_text": obj.get("oracle_text"), "power": obj.get("power"), "toughness": obj.get("toughness"),
        "loyalty": obj.get("loyalty"), "defense": obj.get("defense"), "colors": obj.get("colors") or [],
        "color_identity": obj.get("color_identity") or [], "keywords": obj.get("keywords") or [],
        "produced_mana": obj.get("produced_mana") or [], "legalities": obj.get("legalities") or {},
        "faces": _faces(obj), "game_changer": obj.get("game_changer"), "edhrec_rank": obj.get("edhrec_rank"),
        "released_at": _date(obj.get("released_at")), "scryfall_uri": obj.get("scryfall_uri"),
        "representative_id": obj.get("id"), "digital": bool(obj.get("digital")),
        "artist": _artist(obj), "image_normal": _image(obj),
    }
    row["content_hash"] = digest(row)
    return row


def sync_oracle_cards(db: Session, objects: Iterable[dict], today: date | None = None) -> dict:
    """Bring ``oracle_cards`` in line with Scryfall's oracle-cards file. Returns counts."""
    today = today or date.today()
    new = {}
    for obj in objects:
        if obj.get("object") == "card" and obj.get("oracle_id") and obj.get("layout") not in SKIPPED_LAYOUTS:
            row = oracle_card_row(obj)
            new[row["oracle_id"]] = row
    old = {r.oracle_id: (r.content_hash, r.legalities) for r in db.execute(
        select(OracleCard.oracle_id, OracleCard.content_hash, OracleCard.legalities))}
    changed = [row for key, row in new.items() if key not in old or old[key][0] != row["content_hash"]]
    gone = [key for key in old if key not in new]

    legality = []
    for row in changed:
        before = old.get(row["oracle_id"])
        if before is None:
            continue  # a new card has no previous legality to compare with
        for fmt in sorted(set(before[1]) | set(row["legalities"])):
            was, now = before[1].get(fmt), row["legalities"].get(fmt)
            if was != now:
                legality.append({"oracle_id": row["oracle_id"], "format": fmt, "old": was, "new": now, "observed_on": today})

    _upsert(db, OracleCard, changed, ("oracle_id",))
    for part in chunks(gone):
        db.execute(delete(OracleCard).where(OracleCard.oracle_id.in_(part)))
    for part in chunks(legality):
        db.execute(postgresql.insert(LegalityChange.__table__).values(part))
    return {"cards": len(new), "written": len(changed), "removed": len(gone), "legality_changes": len(legality)}


def ruling_row(obj: dict) -> dict:
    comment = obj["comment"]
    ident = digest([obj["oracle_id"], obj.get("published_at"), obj.get("source"), comment])
    return {"id": ident, "oracle_id": obj["oracle_id"], "published_at": _date(obj.get("published_at")),
            "source": obj.get("source") or "wotc", "comment": comment}


def sync_rulings(db: Session, objects: Iterable[dict]) -> dict:
    """Rulings are immutable: the id is a hash of the content, so only new ones are inserted and
    ones Scryfall dropped are deleted."""
    new = {r["id"]: r for r in (ruling_row(o) for o in objects if o.get("object", "ruling") == "ruling")}
    old = set(db.scalars(select(Ruling.id)))
    added = [row for key, row in new.items() if key not in old]
    gone = list(old - set(new))
    _upsert(db, Ruling, added, ("id",))
    for part in chunks(gone):
        db.execute(delete(Ruling).where(Ruling.id.in_(part)))
    return {"rulings": len(new), "written": len(added), "removed": len(gone)}


def tag_closure(tags: list[dict], roots: Iterable[str] = TAG_ROOTS) -> set[str]:
    """Ids of the root tags and every descendant (``removal`` has no links itself, its children do)."""
    by_id = {t["id"]: t for t in tags}
    by_slug = {t["slug"]: t for t in tags}
    keep: set[str] = set()
    stack = [by_slug[r]["id"] for r in roots if r in by_slug]
    while stack:
        tid = stack.pop()
        if tid in keep:
            continue
        keep.add(tid)
        stack.extend(c for c in (by_id.get(tid) or {}).get("child_ids") or [] if c in by_id)
    return keep


def sync_oracle_tags(db: Session, objects: Iterable[dict], roots: Iterable[str] = TAG_ROOTS) -> dict:
    """All tags are stored (they are small); links only for the curated roots and their descendants."""
    tags = [o for o in objects if o.get("id") and o.get("slug")]
    keep = tag_closure(tags, roots)
    rows = [{"id": t["id"], "slug": t["slug"], "label": t.get("label"), "description": t.get("description"),
             "parent_ids": t.get("parent_ids") or [], "child_ids": t.get("child_ids") or []} for t in tags]
    _upsert(db, OracleTag, rows, ("id",))
    db.execute(delete(OracleTag).where(OracleTag.id.not_in([t["id"] for t in tags])) if tags else delete(OracleTag))

    links = {}
    for t in tags:
        if t["id"] in keep:
            for g in t.get("taggings") or []:
                links[(t["id"], g["oracle_id"])] = g.get("weight")
    old = {(r.tag_id, r.oracle_id): r.weight for r in db.execute(
        select(OracleTagLink.tag_id, OracleTagLink.oracle_id, OracleTagLink.weight))}
    changed = [{"tag_id": k[0], "oracle_id": k[1], "weight": w} for k, w in links.items() if k not in old or old[k] != w]
    _upsert(db, OracleTagLink, changed, ("tag_id", "oracle_id"))
    gone = [k for k in old if k not in links]
    for part in chunks(gone):
        for tag_id, oracle_id in part:
            db.execute(delete(OracleTagLink).where(OracleTagLink.tag_id == tag_id, OracleTagLink.oracle_id == oracle_id))
    return {"tags": len(tags), "linked_tags": len(keep), "links": len(links), "written": len(changed), "removed": len(gone)}


def cheapest_prices(objects: Iterable[dict], day: date) -> list[dict]:
    """The cheapest priced paper printing of each card from a default-cards stream: one pass, one dict.
    Prices are Scryfall's (sourced from TCGplayer and Cardmarket); we only pick the lowest USD."""
    best: dict[str, dict] = {}
    for obj in objects:
        if obj.get("object") != "card" or obj.get("digital") or not obj.get("oracle_id"):
            continue
        prices = obj.get("prices") or {}
        usd = _number(prices.get("usd"))
        if usd is None:
            continue
        current = best.get(obj["oracle_id"])
        if current is None or usd < current["usd"]:
            best[obj["oracle_id"]] = {
                "oracle_id": obj["oracle_id"], "scryfall_id": obj["id"], "usd": usd,
                "usd_foil": _number(prices.get("usd_foil")), "eur": _number(prices.get("eur")),
                "day": day, "source": "scryfall",
            }
    return list(best.values())


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and 0 <= number < 10_000_000 else None


def sync_oracle_prices(db: Session, rows: list[dict]) -> dict:
    """Today's cheapest prices only; there is no history for cards nobody owns (docs/catalog-design.md)."""
    _upsert(db, OraclePrice, rows, ("oracle_id",))
    keep = [r["oracle_id"] for r in rows]
    removed = 0
    if keep:
        removed = db.execute(delete(OraclePrice).where(OraclePrice.oracle_id.not_in(keep))).rowcount or 0
    return {"prices": len(rows), "removed": removed}


def sync_cheapest_from_file(db: Session, path, day: date | None = None) -> dict:
    """Cheapest prices from a downloaded default-cards file (the daily price job already has it)."""
    from mtg_toolkits.scryfall import iter_bulk_file

    day = day or date.today()
    result = sync_oracle_prices(db, cheapest_prices(iter_bulk_file(path), day))
    record_source(db, "oracle_prices", version=day.isoformat(), rows=result["prices"],
                  url="https://scryfall.com/docs/api/cards")
    db.commit()
    return result


def record_source(db: Session, name: str, *, version: str, rows: int, source_updated_at: datetime | None = None,
                  checksum: str | None = None, url: str | None = None) -> None:
    """Say what was loaded. Written last, so ``whoami`` and every "as of" line only describe finished loads."""
    _upsert(db, CatalogSource, [{
        "name": name, "version": version, "source_updated_at": source_updated_at,
        "fetched_at": datetime.now(timezone.utc), "rows": rows, "checksum": checksum, "url": url,
    }], ("name",))


def source_is_current(db: Session, name: str, version: str) -> bool:
    """True when this exact version of a source is already loaded (the job skips it)."""
    row = db.get(CatalogSource, name)
    return row is not None and row.version == version
