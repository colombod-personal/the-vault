"""Storing and reading the per-card Limited counts (#178, docs/limited-data-design.md).

``replace_file`` writes one reduced 17Lands file (the job, ``jobs/sync_limited.py``); ``card_stats`` builds the answer of
``GET /api/v1/catalog/limited/{set}`` and the MCP tool ``get_limited_card_stats``: every figure with its sample, the exact sample-size
warnings, the attribution text and the provenance blocks (17Lands' counts as a ``source``, the Vault's rates as ``computed``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from . import limited_stats as ls
from . import provenance as prov
from .api.hal import paginate
from .catalog_queries import card_priority
from .catalog_sync import record_source
from .models import LimitedGameStat, LimitedPickStat, LimitedSource, OracleCard

SOURCE_NAME = "limited_17lands"  # the key in catalog_sources and in the Source gate (docs/compliance.md)
KINDS = {"game": LimitedGameStat, "draft": LimitedPickStat}
KIND_ORDER = {"game": 0, "draft": 1}
SORTS = {  # sort -> (descending, the count that must reach the floor, what the left-out sentence counts)
    "win_rate_in_hand": (True, "games_in_hand", "games in hand"),
    "games_in_hand": (True, "games_in_hand", "games in hand"),
    "avg_last_seen_pick": (False, "times_seen", "packs in which they were seen"),
    "avg_taken_at": (False, "times_picked", "picks"),
}
MAX_CARDS = 40
MAX_LIMIT = 50


def catalog_index(db: Session) -> dict[str, str]:
    """Lower-case card name (the full name, and each face of a double-faced card) -> oracle id, for the playable card when
    several share a name. 17Lands names a card the way Arena does; a name that matches nothing (an Arena-only rebalanced card)
    stays unmatched."""
    rows = db.execute(select(OracleCard.oracle_id, OracleCard.name, OracleCard.layout, OracleCard.digital, OracleCard.edhrec_rank)).all()
    index: dict[str, str] = {}
    for row in sorted(rows, key=card_priority):
        full = row.name.strip().lower()
        index.setdefault(full, row.oracle_id)
        if " // " in full:
            for face in full.split(" // "):
                index.setdefault(face.strip(), row.oracle_id)
    return index


def oracle_id_for(index: dict[str, str], name: str) -> str | None:
    lower = name.strip().lower()
    found = index.get(lower)
    if found is None and " // " in lower:
        found = index.get(lower.split(" // ")[0].strip())
    return found


def previous(db: Session, set_code: str, fmt: str, kind: str) -> dict | None:
    row = db.get(LimitedSource, (set_code, fmt, kind))
    return None if row is None else {"etag": row.etag, "last_modified": row.last_modified, "cards": row.cards, "records": row.records}


def replace_file(db: Session, set_code: str, fmt: str, result: ls.FileResult, *, etag: str | None, last_modified: datetime | None,
                 content_length: int | None, index: dict[str, str]) -> dict:
    """Replace the stored rows of one (set, format, kind) with a reduced file, and write its ``limited_sources`` row, in the
    caller's transaction (the caller commits, so a failure leaves the old rows). Returns what was written."""
    model = KINDS[result.kind]
    fields = ls.GAME_FIELDS if result.kind == "game" else ls.PICK_FIELDS
    db.execute(delete(model).where(model.set_code == set_code, model.format == fmt))
    rows, unmatched = [], 0
    for name, counts in sorted(result.cards.items()):
        oracle_id = oracle_id_for(index, name)
        unmatched += oracle_id is None
        rows.append({"set_code": set_code, "format": fmt, "card_name": name[:200], "oracle_id": oracle_id, **dict(zip(fields, counts))})
    if rows:
        db.execute(insert(model), rows)
    now = datetime.now(timezone.utc)
    values = dict(etag=etag, last_modified=last_modified, content_length=content_length, fetched_at=now, records=result.records,
                  skipped_records=result.skipped, wins=result.wins, first_time=result.first_time, last_time=result.last_time,
                  first_picks=result.first_picks, empty_first_picks=result.empty_first_picks, cards=len(rows), unmatched_cards=unmatched)
    existing = db.get(LimitedSource, (set_code, fmt, result.kind))
    if existing is None:
        db.add(LimitedSource(set_code=set_code, format=fmt, kind=result.kind, **values))
    else:
        for key, value in values.items():
            setattr(existing, key, value)
    db.flush()
    record_catalog_source(db)
    return {"cards": len(rows), "unmatched_cards": unmatched, "records": result.records, "skipped_records": result.skipped}


def record_catalog_source(db: Session) -> None:
    """The ``catalog_sources`` row of this source: what ``whoami`` and the status of the data show (written last)."""
    sources = db.scalars(select(LimitedSource)).all()
    if not sources:
        return
    newest = max((s.last_modified for s in sources if s.last_modified), default=None)
    rows = sum(db.scalar(select(func.count()).select_from(m)) or 0 for m in KINDS.values())
    record_source(db, SOURCE_NAME, version=f"{len(sources)} files, newest {newest.date().isoformat() if newest else 'unknown'}"[:60],
                  rows=rows, source_updated_at=newest, url=ls.SOURCE_URL)


def _day(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


def loaded_sets(db: Session) -> list[dict]:
    """What the Vault holds, one entry per set and format, with the date of each file."""
    out: dict[tuple[str, str], dict] = {}
    for s in sorted(db.scalars(select(LimitedSource)), key=lambda s: (s.set_code, s.format, KIND_ORDER[s.kind])):
        entry = out.setdefault((s.set_code, s.format), {"set": s.set_code, "format": s.format, "files": {}})
        entry["files"][s.kind] = {"last_modified": _day(s.last_modified), "fetched_at": s.fetched_at.date().isoformat(), "records": s.records}
    return list(out.values())


def loaded_text(sets: list[dict]) -> str:
    if not sets:
        return "The Vault has no 17Lands data loaded yet."
    return "Loaded: " + "; ".join(
        f"{e['set']} {e['format']} (" + ", ".join(f"{k} file {v['last_modified']}" for k, v in e["files"].items()) + ")" for e in sets) + "."


def _rate(wins: int, n: int) -> float | None:
    return round(wins / n, 4) if n else None


def _shown(value: float | None, n: int) -> float | None:
    """A rate or an average is shown only when its sample reaches the floor."""
    return value if n >= ls.SHOW_FLOOR else None


def card_record(name: str, g: LimitedGameStat | None, p: LimitedPickStat | None, set_code: str, fmt: str, baseline: float | None) -> dict:
    gih = g.in_hand if g else 0  # games with a copy in hand at least once: not opening + drawn, which counts a game with both twice
    wih = g.wins_in_hand if g else 0
    rate = _rate(wih, gih)
    low, high = ls.wilson(wih, gih) if gih else (None, None)
    seen, picked = (p.seen, p.picked) if p else (0, 0)
    level = ls.level(gih)
    return {
        "name": name, "oracle_id": (g.oracle_id if g else None) or (p.oracle_id if p else None),
        "games_in_hand": gih if g else None,
        "win_rate_in_hand": rate,  # shown at every level, flagged below the floor; null with no games
        "win_rate_ci95": [round(low, 4), round(high, 4)] if gih else None,
        "vs_baseline_points": round((rate - baseline) * 100, 1) if rate is not None and baseline is not None else None,
        "games_in_opening_hand": g.opening if g else None,
        "win_rate_opening_hand": _shown(_rate(g.wins_opening, g.opening), g.opening) if g else None,
        "games_drawn": g.drawn if g else None,
        "win_rate_drawn": _shown(_rate(g.wins_drawn, g.drawn), g.drawn) if g else None,
        "games_played": g.games_played if g else None,
        "win_rate_played": _shown(_rate(g.wins_played, g.games_played), g.games_played) if g else None,
        "times_seen": seen if p else None,
        "avg_last_seen_pick": _shown(round(p.last_seen_sum / seen, 2), seen) if p and seen else None,
        "times_picked": picked if p else None,
        "avg_taken_at": _shown(round(p.picked_sum / picked, 2), picked) if p and picked else None,
        "sample": {"level": level, "n": gih, "warning": ls.warning_for(name, set_code, fmt, wih, gih),
                   "position_warning": ls.positions_warning(name, seen, picked) if p else None},
    }


def _faces(name: str) -> list[str]:
    lower = name.strip().lower()
    return [lower] + ([f.strip() for f in lower.split(" // ")] if " // " in lower else [])


def card_stats(db: Session, set_code: str, fmt: str, *, cards: list[str] | None = None, sort: str = "win_rate_in_hand",
               limit: int = 20, cursor: str | None = None) -> dict:
    """The answer for one set and format. 404 (with what is loaded) when there is nothing for the set and format."""
    set_code = set_code.strip().upper()
    sources = {s.kind: s for s in db.scalars(select(LimitedSource).where(LimitedSource.set_code == set_code, LimitedSource.format == fmt))}
    if not sources:
        raise HTTPException(404, f"The Vault has no 17Lands data for {set_code} {fmt}. " + loaded_text(loaded_sets(db)))
    game = {r.card_name: r for r in db.scalars(select(LimitedGameStat).where(LimitedGameStat.set_code == set_code, LimitedGameStat.format == fmt))}
    picks = {r.card_name: r for r in db.scalars(select(LimitedPickStat).where(LimitedPickStat.set_code == set_code, LimitedPickStat.format == fmt))}
    gs = sources.get("game")
    baseline = (gs.wins / gs.records) if gs and gs.records and gs.wins is not None else None
    records = {name: card_record(name, game.get(name), picks.get(name), set_code, fmt, baseline) for name in sorted(game.keys() | picks.keys())}

    not_found: list[str] = []
    left_out = {"count": 0, "reason": None}
    next_cursor = None
    total = len(records)
    if cards:
        by_face: dict[str, str] = {}
        for name in records:
            for face in _faces(name):
                by_face.setdefault(face, name)
        chosen, seen_names = [], set()
        for asked in cards:
            name = next((by_face[f] for f in _faces(asked) if f in by_face), None)
            if name is None:
                not_found.append(asked)
            elif name not in seen_names:
                seen_names.add(name)
                chosen.append(records[name])
        page = chosen
        total = len(chosen)
    else:
        descending, count_key, what = SORTS[sort]
        ranked = [r for r in records.values() if (r[count_key] or 0) >= ls.SHOW_FLOOR and r[sort] is not None]
        skipped = len(records) - len(ranked)  # below the floor for this metric: never in a sorted list
        total = len(ranked)
        # a keyset cursor (the metric's value and the card's name, as every other list of the API): a weekly refresh between two
        # page requests cannot make a page skip or repeat a card, which an item offset would
        page, next_cursor = paginate(ranked, key=lambda r: ((-r[sort] if descending else r[sort]),), ident=lambda r: r["name"],
                                     cursor=cursor, limit=limit)
        if skipped:
            left_out = {"count": skipped, "reason": ls.WARN_LEFT_OUT.format(k=skipped, floor=ls.SHOW_FLOOR, what=what)}

    ds = sources.get("draft")
    window = {
        "first_game": gs.first_time.isoformat() if gs and gs.first_time else None,
        "last_game": gs.last_time.isoformat() if gs and gs.last_time else None,
        "games": gs.records if gs else None, "games_left_out_as_inconsistent": gs.skipped_records if gs else None,
        "baseline_win_rate": round(baseline, 4) if baseline is not None else None,
        "picks": ds.records if ds else None,
        "first_draft": ds.first_time.isoformat() if ds and ds.first_time else None,
        "last_draft": ds.last_time.isoformat() if ds and ds.last_time else None,
        "game_file": _file(gs), "draft_file": _file(ds),
    }
    modified = {k: _day(sources[k].last_modified) for k in sorted(sources, key=KIND_ORDER.get)}
    updated = (next(iter(modified.values())) if len(set(modified.values())) == 1
               else ", ".join(f"{k} file {v}" for k, v in modified.items()))
    as_of = max((v for v in modified.values() if v), default=None)
    read = max(s.fetched_at for s in sources.values()).date().isoformat()
    caveats = [ls.CAVEAT]
    if ds and ds.first_picks and (ds.empty_first_picks or 0) / ds.first_picks >= ls.FIRST_PICK_SHARE:
        caveats.append(ls.FIRST_PICK_CAVEAT)
    attribution = ls.attribution(set_code, fmt, updated, read)
    source = prov.source("17Lands", origin=ls.ORIGIN, url=ls.SOURCE_URL, as_of=as_of,
                         version=f"{set_code} {fmt}: " + ", ".join(f"{k} file {v}" for k, v in modified.items()),
                         notice=attribution, licence={"name": ls.LICENCE_NAME, "url": ls.LICENCE_URL}, changes=ls.CHANGES)
    computed = prov.computed(ls.COMPUTED_WHAT, [source], as_of=as_of)
    body = {"attribution": attribution, "set": set_code, "format": fmt, "platform": ls.PLATFORM, "data_window": window,
            "sort": None if cards else sort, "total": total, "count": len(page), "cards": page, "not_found": not_found, "left_out": left_out,
            "caveats": caveats, "provenance": [source.model_dump(exclude_none=True), computed.model_dump(exclude_none=True)]}
    if next_cursor:
        body["next_cursor"] = next_cursor
    return body


def _file(s: LimitedSource | None) -> dict | None:
    if s is None:
        return None
    return {"last_modified": _day(s.last_modified), "fetched_at": s.fetched_at.isoformat(), "etag": s.etag,
            "cards": s.cards, "cards_not_in_the_catalog": s.unmatched_cards}
