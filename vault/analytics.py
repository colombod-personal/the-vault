"""Collection analytics computed in Postgres: P&L, breakdowns, valuation, per-name rollups, set
colour mixes and deck prices.

Every query starts from one CTE (:func:`_priced`) that prices each of the owner's rows exactly as
:func:`vault.prices.unit_price` does: the latest Scryfall snapshot for the row's pricing finish,
else the file's own market price, else 0, with prices no import would accept (not finite, or
above ``MAX_PRICE``) treated as missing. Rows are joined to ``cards`` for type, colours, mana value
and rarity; rows whose printing isn't in ``cards`` yet count as ``unknown``.

Known-cost semantics: a copy's cost is known when a non-zero price paid was
recorded for its row. P&L compares what was paid against today's market value of those same
copies only; copies without a price paid are counted, never valued at zero cost.

Main card type (:func:`main_type`): the front face's types (the type line before `` // ``, then
before the `` — `` that starts the subtypes), and the first of Creature, Planeswalker, Battle,
Land, Instant, Sorcery, Artifact, Enchantment found there, else Other. So "Legendary Creature",
"Artifact Creature" and "Enchantment Creature" are Creatures, "Artifact Land" is a Land, a
transforming or modal double-faced card is typed by its front face ("Sorcery // Land" is a
Sorcery), and a split card by its left half. This matches the graph view's last-word rule for
every printed type combination except split and double-faced cards, which it typed by the last face.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from .prices import MAX_PRICE

COLORS = ("W", "U", "B", "R", "G", "M", "C")  # mono colours, multicolour, colourless
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "M": "Multicolor",
               "C": "Colorless", "unknown": "Unknown"}
# Precedence when a type line has several card types (see the module docstring).
MAIN_TYPES = ("Creature", "Planeswalker", "Battle", "Land", "Instant", "Sorcery", "Artifact", "Enchantment")
TYPES = ("Creature", "Land", "Artifact", "Enchantment", "Instant", "Sorcery", "Planeswalker", "Battle", "Other")
MANA_VALUES = ("0", "1", "2", "3", "4", "5", "6", "7", "8+")
RARITIES = ("common", "uncommon", "rare", "mythic", "special", "bonus")
UNKNOWN = "unknown"


def main_type(type_line: str | None) -> str:
    """The main card type of a type line (the rule in the module docstring; the SQL uses the same)."""
    types = (type_line or "").split(" // ")[0].split(" — ")[0]
    words = {w.lower() for w in types.split()}
    return next((t for t in MAIN_TYPES if t.lower() in words), "Other")


def color_key(color_identity: list[str] | None) -> str:
    """W, U, B, R or G for a mono-coloured card, M for multicolour, C for colourless."""
    ci = color_identity or []
    return "C" if not ci else ci[0].upper() if len(ci) == 1 else "M"


# -- SQL building blocks ----------------------------------------------------------------------

def _plausible(x: str) -> str:
    return f"(CASE WHEN abs({x}) <= {MAX_PRICE!r} THEN ({x})::float8 END)"


# The group a row belongs to, exactly as vault.collection_view groups printings.
SET_CODE = "coalesce(nullif(e.extra->>'Set Code', ''), upper(coalesce(e.set_code, '')))"
PRINTING = ("coalesce(e.extra->>'Printing', CASE e.finish WHEN 'foil' THEN 'Foil' WHEN 'etched' THEN 'Etched' "
            "ELSE 'Normal' END)")
GROUP_KEY = (f"concat_ws(chr(31), e.name, {SET_CODE}, coalesce(e.collector_number, ''), {PRINTING}, "
             "e.condition, e.language)")
SNAPSHOT_PRICE = _plausible("CASE coalesce(e.price_finish, e.finish) WHEN 'foil' THEN l.usd_foil "
                            "WHEN 'etched' THEN l.usd_etched ELSE l.usd END")
FILE_PRICE = _plausible("CASE WHEN json_typeof(e.source_prices->'market') = 'number' "
                        "THEN (e.source_prices->>'market')::float8 END")

# Colour and main type of the printing in ``c`` (a ``cards`` row, or NULLs when unknown).
COLOR = ("CASE WHEN c.scryfall_id IS NULL THEN 'unknown' "
         "WHEN coalesce(json_typeof(c.color_identity), 'null') <> 'array' THEN 'C' "
         "WHEN json_array_length(c.color_identity) = 0 THEN 'C' "
         "WHEN json_array_length(c.color_identity) = 1 THEN upper(c.color_identity->>0) "
         "ELSE 'M' END")
_FRONT_TYPES = "split_part(split_part(coalesce(c.type_line, ''), ' // ', 1), ' — ', 1)"
MAIN_TYPE = ("CASE WHEN c.scryfall_id IS NULL THEN 'unknown' "
             + " ".join(f"WHEN {_FRONT_TYPES} ~* '\\m{t.lower()}\\M' THEN '{t}'" for t in MAIN_TYPES)
             + " ELSE 'Other' END")
MANA_VALUE = ("CASE WHEN c.scryfall_id IS NULL THEN 'unknown' WHEN coalesce(c.cmc, 0) >= 8 THEN '8+' "
              "ELSE floor(greatest(coalesce(c.cmc, 0), 0))::int::text END")
RARITY = "CASE WHEN c.scryfall_id IS NULL THEN 'unknown' ELSE coalesce(nullif(lower(c.rarity), ''), 'unknown') END"


def _priced(where: str = "") -> str:
    """``WITH … priced AS (…)``: one row per owned row of user ``:uid``, priced and typed."""
    return f"""
WITH mine AS (SELECT * FROM entries WHERE user_id = :uid {where}),
latest AS (
  SELECT DISTINCT ON (p.scryfall_id) p.scryfall_id, p.usd, p.usd_foil, p.usd_etched
  FROM price_snapshots p
  WHERE p.scryfall_id IN (SELECT scryfall_id FROM mine WHERE scryfall_id IS NOT NULL)
  ORDER BY p.scryfall_id, p.day DESC
),
priced AS (
  SELECT e.id, e.name, e.quantity, e.scryfall_id, e.purchase_date, e.finish, e.condition, e.language,
         e.collector_number, {SET_CODE} AS set_code, {PRINTING} AS printing, {GROUP_KEY} AS gkey,
         coalesce({SNAPSHOT_PRICE}, {FILE_PRICE}, 0)::float8 AS price,
         {_plausible("e.purchase_price")} AS paid,
         c.scryfall_id IS NOT NULL AS has_card, c.color_identity,
         {COLOR} AS color, {MAIN_TYPE} AS main_type, {MANA_VALUE} AS mana_value, {RARITY} AS rarity
  FROM mine e
  LEFT JOIN latest l ON l.scryfall_id = e.scryfall_id
  LEFT JOIN cards c ON c.scryfall_id = e.scryfall_id
)"""


def _money(value) -> float:
    return round(float(value or 0), 2)


# -- P&L --------------------------------------------------------------------------------------

def pnl(db: Session, user_id: int) -> dict:
    """Profit and loss over the copies with a known cost, per printing as the web app counts it:
    a printing's cost is known when its total paid is positive; its known copies are those with a
    price paid, valued at today's price."""
    row = db.execute(text(_priced() + """,
groups AS (
  SELECT gkey, sum(quantity) AS qty, sum(coalesce(paid, 0) * quantity) AS paid_total,
         coalesce(sum(quantity) FILTER (WHERE paid <> 0), 0) AS paid_qty,
         coalesce(sum(price * quantity) FILTER (WHERE paid <> 0), 0) AS known_market
  FROM priced GROUP BY gkey
)
SELECT coalesce(sum(paid_total) FILTER (WHERE paid_total > 0 AND paid_qty > 0), 0) AS paid,
       coalesce(sum(known_market) FILTER (WHERE paid_total > 0 AND paid_qty > 0), 0) AS market,
       coalesce(sum(paid_qty) FILTER (WHERE paid_total > 0 AND paid_qty > 0), 0) AS known_copies,
       coalesce(sum(qty), 0) AS copies,
       count(*) FILTER (WHERE paid_total > 0 AND paid_qty > 0) AS known_printings
FROM groups"""), {"uid": user_id}).one()
    paid, market = _money(row.paid), _money(row.market)
    gain = round(market - paid, 2) if row.known_printings else None
    return {"pnl": gain, "pnl_pct": round(gain / paid * 100, 2) if gain is not None and paid else None,
            "known_cost_paid": paid, "known_cost_market": market,
            "known_cost_copies": int(row.known_copies), "unknown_cost_copies": int(row.copies - row.known_copies)}


HIDDEN_PNL = {"pnl": None, "pnl_pct": None, "known_cost_paid": None, "known_cost_market": None,
              "known_cost_copies": None, "unknown_cost_copies": None}


# -- breakdowns -------------------------------------------------------------------------------

def _bucket(key: str, label: str | None, row) -> dict:
    return {"key": key, "label": label or key, **_counts(row)}


def _counts(row) -> dict:
    return {"copies": int(row.copies or 0) if row else 0, "printings": int(row.printings or 0) if row else 0,
            "market_value": _money(row.market) if row else 0.0}


def breakdowns(db: Session, user_id: int) -> dict:
    """Copies, printings and market value by colour identity, main type, mana value, rarity and
    colour × type, in one grouping-sets query. Every bucket list is fixed (zeros included), plus
    ``unknown`` for printings not yet in the card table, so the answer's size never grows."""
    rows = db.execute(text(_priced() + """
SELECT GROUPING(color) AS g_color, GROUPING(main_type) AS g_type, GROUPING(mana_value) AS g_mv,
       GROUPING(rarity) AS g_rarity, color, main_type, mana_value, rarity,
       sum(quantity) AS copies, count(DISTINCT gkey) AS printings, sum(price * quantity) AS market
FROM priced
GROUP BY GROUPING SETS ((color), (main_type), (mana_value), (rarity), (color, main_type), ())"""),
                      {"uid": user_id}).all()
    by_color, by_type, by_mv, by_rarity, matrix, total = {}, {}, {}, {}, {}, None
    for r in rows:
        grouped = (r.g_color, r.g_type, r.g_mv, r.g_rarity)
        if grouped == (0, 1, 1, 1):
            by_color[r.color] = r
        elif grouped == (1, 0, 1, 1):
            by_type[r.main_type] = r
        elif grouped == (1, 1, 0, 1):
            by_mv[r.mana_value] = r
        elif grouped == (1, 1, 1, 0):
            by_rarity[r.rarity] = r
        elif grouped == (0, 0, 1, 1):
            matrix[(r.color, r.main_type)] = r
        else:
            total = r

    def buckets(found: dict, keys, labels=None) -> list[dict]:
        keys = list(keys) + sorted(k for k in found if k not in keys and k != UNKNOWN) + [UNKNOWN]
        return [_bucket(k, (labels or {}).get(k), found.get(k)) for k in keys]

    colors = list(COLORS)
    return {
        "totals": _counts(total),
        "colors": buckets(by_color, colors, COLOR_NAMES),
        "types": buckets(by_type, TYPES),
        "mana_values": buckets(by_mv, MANA_VALUES),
        "rarities": buckets(by_rarity, RARITIES),
        "matrix": [{"color": c, "type": t, **_counts(matrix.get((c, t)))}
                   for c in colors + [UNKNOWN] for t in (TYPES if c != UNKNOWN else (UNKNOWN,))],
    }


# -- valuation --------------------------------------------------------------------------------

def _month_index(month: str) -> int:
    y, m = month.split("-")
    return int(y) * 12 + int(m) - 1


def valuation(db: Session, user_id: int, hide_costs: bool) -> dict:
    """Month by month (by purchase date, as the timeline): copies bought, their market value today,
    what was paid, and the running totals; the month that added the most value; the change over
    the trailing 12 calendar months (ending with the latest month bought); undated copies."""
    rows = db.execute(text(_priced() + """
SELECT to_char(purchase_date, 'YYYY-MM') AS month, sum(quantity) AS copies, sum(price * quantity) AS market,
       sum(coalesce(paid, 0) * quantity) AS spend,
       coalesce(sum(price * quantity) FILTER (WHERE paid <> 0), 0) AS known_market
FROM priced GROUP BY 1 ORDER BY 1 NULLS LAST"""), {"uid": user_id}).all()
    undated = next((r for r in rows if r.month is None), None)
    months, mc, cc, kc, qc = [], 0.0, 0.0, 0.0, 0
    for r in (r for r in rows if r.month is not None):
        mc, cc, kc, qc = mc + float(r.market), cc + float(r.spend), kc + float(r.known_market), qc + int(r.copies)
        months.append({
            "month": r.month, "copies": int(r.copies), "market": _money(r.market),
            "paid": None if hide_costs else _money(r.spend),
            "copies_cum": qc, "market_cum": _money(mc), "cost_cum": None if hide_costs else _money(cc),
            "gain_cum": None if hide_costs else _money(mc - cc),
            "known_gain_cum": None if hide_costs else _money(kc - cc),
        })
    peak = max(months, key=lambda m: m["market"], default=None)
    trailing = None
    if months:
        last = _month_index(months[-1]["month"])
        recent = [m for m in months if _month_index(m["month"]) > last - 12]
        since = last - 11
        trailing = {"since": f"{since // 12:04d}-{since % 12 + 1:02d}", "until": months[-1]["month"],
                    "copies": sum(m["copies"] for m in recent),
                    "market": _money(sum(m["market"] for m in recent)),
                    "paid": None if hide_costs else _money(sum(m["paid"] for m in recent))}
    return {
        "months": months,
        "totals": {"copies": qc, "market": _money(mc), "cost": None if hide_costs else _money(cc),
                   "gain": None if hide_costs else _money(mc - cc),
                   "known_gain": None if hide_costs else _money(kc - cc)},
        "peak": {"month": peak["month"], "market": peak["market"], "copies": peak["copies"]} if peak else None,
        "trailing_12m": trailing,
        "undated": {"copies": int(undated.copies) if undated else 0, "market": _money(undated.market) if undated else 0.0},
        "costs_hidden": hide_costs,
    }


# -- per-name rollup --------------------------------------------------------------------------

NAME_SORTS = {  # sort -> (column, descending)
    "-value": ("market", True), "value": ("market", False),
    "-quantity": ("copies", True), "quantity": ("copies", False),
    "name": ("key", False), "-name": ("key", True),
}


class BadCursor(ValueError):
    pass


def names(db: Session, user_id: int, *, sort: str = "-value", colors: list[str] | None = None,
          type_: str | None = None, min_value: float | None = None, after: list | None = None,
          limit: int = 100) -> tuple[list[dict], int, list | None]:
    """One row per card name (case-insensitive), paged with a keyset: (items, total, next key).
    The name's colour, type, mana value, rarity and image are its most valuable known printing's."""
    column, desc = NAME_SORTS[sort]
    params: dict = {"uid": user_id, "lim": limit + 1}
    where = ["true"]
    if colors:
        params["colors"] = colors
        where.append("(color = ANY(:colors) OR CASE WHEN color = 'M' THEN EXISTS (SELECT 1 FROM "
                     "json_array_elements_text(color_identity) x WHERE upper(x) = ANY(:colors)) ELSE false END)")
    if type_:
        params["type"] = type_.lower()
        where.append("lower(main_type) = :type")
    if min_value is not None:
        params["min"] = Decimal(str(min_value))
        where.append("market >= :min")
    page_where = "true"
    if after is not None:
        try:
            if column == "key":
                [key] = after
                params["k"] = str(key)
                page_where = f"key {'<' if desc else '>'} :k"
            else:
                value, key = after
                params["v"], params["k"] = (Decimal(str(value)) if column == "market" else int(value)), str(key)
                op = "<" if desc else ">"
                page_where = f"({column} {op} :v OR ({column} = :v AND key > :k))"
        except (TypeError, ValueError, ArithmeticError):
            raise BadCursor from None

    def order(table: str) -> str:
        return f"{table}{column} {'DESC' if desc else 'ASC'}" + (f", {table}key ASC" if column != "key" else "")
    rows = db.execute(text(_priced() + f""",
named AS (
  -- byte order (COLLATE "C"), so pages come in the same order on every database, whatever its locale
  SELECT lower(name) COLLATE "C" AS key, (array_agg(name ORDER BY has_card DESC, price DESC, id))[1] AS name,
         sum(quantity)::bigint AS copies, round(sum(price * quantity)::numeric, 2) AS market,
         count(DISTINCT gkey) AS printings, array_agg(DISTINCT set_code) AS sets,
         (array_agg(scryfall_id ORDER BY has_card DESC, price DESC, id))[1] AS top_id
  FROM priced GROUP BY lower(name)
),
rolled AS (
  SELECT n.*, c.color_identity, {COLOR} AS color, {MAIN_TYPE} AS main_type, c.type_line, c.cmc, c.rarity,
         c.image_small, c.image_normal, c.artist, c.scryfall_id AS card_id
  FROM named n LEFT JOIN cards c ON c.scryfall_id = n.top_id
),
filtered AS (SELECT * FROM rolled WHERE {' AND '.join(where)})
SELECT f.*, t.total FROM (SELECT count(*) AS total FROM filtered) t
LEFT JOIN LATERAL (SELECT * FROM filtered WHERE {page_where} ORDER BY {order('')} LIMIT :lim) f ON true
ORDER BY {order('f.')}"""), params).all()
    total = int(rows[0].total)
    rows = [r for r in rows if r.key is not None]
    more = len(rows) > limit
    rows = rows[:limit]
    items = [{
        "name": r.name, "copies": int(r.copies), "market_value": float(r.market),
        "unit_price": round(float(r.market) / r.copies, 2) if r.copies else 0.0,
        "printings": int(r.printings), "sets": sorted(s for s in r.sets if s),
        "color": r.color, "color_identity": list(r.color_identity or []) if r.card_id else [],
        "type": r.main_type, "type_line": r.type_line, "cmc": r.cmc, "rarity": r.rarity,
        "image": {"small": r.image_small, "normal": r.image_normal, "artist": r.artist,
                  "credit": "Image via Scryfall · © Wizards of the Coast"} if r.card_id else None,
        "scryfall_id": r.card_id,
    } for r in rows]
    last = rows[-1] if rows and more else None
    nxt = None if last is None else ([last.key] if column == "key" else
                                     [str(last.market) if column == "market" else int(last.copies), last.key])
    return items, total, nxt


# -- sets ---------------------------------------------------------------------------------------

def set_colors(db: Session, user_id: int) -> dict[str, dict[str, int]]:
    """Copies per colour identity in each set (keyed like vault.collection_view's set codes)."""
    out: dict[str, dict[str, int]] = {}
    for code, color, copies in db.execute(text(_priced() + """
SELECT set_code, color, sum(quantity) FROM priced GROUP BY 1, 2"""), {"uid": user_id}):
        out.setdefault(code, {k: 0 for k in COLORS + (UNKNOWN,)})[color] = int(copies)
    return out


# -- decks ----------------------------------------------------------------------------------------

def _front(name: str) -> str:
    return (name or "").split(" // ")[0].strip().lower()


MAX_OWNED_PRINTINGS = 50  # per deck line


def price_coverage(db: Session, user_id: int, coverage: dict) -> dict:
    """Add prices to a deck's coverage: each line's ``unit_price`` (the cheapest known USD price of
    the line's printing when it names one the Vault knows, else of any printing of the card, in any
    finish), ``missing_cost`` (that price times the copies missing; null when no price is known),
    and ``owned_printings`` (the printings of the card in the collection, at today's prices). The
    deck's ``missing_cost`` totals the lines with a price; ``missing_unpriced`` counts the others."""
    lines = coverage["cards"]
    fronts = sorted({_front(c["name"]) for c in lines if c["name"]})
    if not fronts:
        return {**coverage, "missing_cost": 0.0, "missing_unpriced": 0}
    cheapest = (f"least({_plausible('l.usd')}, {_plausible('l.usd_foil')}, {_plausible('l.usd_etched')})")
    priced_cards = db.execute(text(f"""
WITH named AS (
  SELECT scryfall_id, lower(split_part(name, ' // ', 1)) AS front, set_code, collector_number FROM cards
  WHERE lower(split_part(name, ' // ', 1)) = ANY(:fronts)
),
latest AS (
  SELECT DISTINCT ON (p.scryfall_id) p.scryfall_id, p.usd, p.usd_foil, p.usd_etched FROM price_snapshots p
  WHERE p.scryfall_id IN (SELECT scryfall_id FROM named) ORDER BY p.scryfall_id, p.day DESC
)
SELECT n.front, n.set_code, n.collector_number, {cheapest} AS price
FROM named n JOIN latest l ON l.scryfall_id = n.scryfall_id"""), {"fronts": fronts}).all()
    by_name: dict[str, float] = {}
    by_printing: dict[tuple, float] = {}
    for front, set_code, number, price in priced_cards:
        if price is None:
            continue
        by_name[front] = min(by_name.get(front, price), price)
        key = (front, (set_code or "").lower(), (number or "").lower())
        by_printing[key] = min(by_printing.get(key, price), price)
    owned: dict[str, list[dict]] = {}
    for r in db.execute(text(_priced("AND lower(split_part(name, ' // ', 1)) = ANY(:fronts)") + """
SELECT lower(split_part(name, ' // ', 1)) AS front, set_code, collector_number, printing, finish,
       sum(quantity) AS copies, max(price) AS price
FROM priced GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, max(price) DESC, 2, 3"""), {"uid": user_id, "fronts": fronts}):
        owned.setdefault(r.front, []).append({"set": r.set_code, "collector_number": r.collector_number or "",
                                              "printing": r.printing, "finish": r.finish, "quantity": int(r.copies),
                                              "unit_price": _money(r.price)})
    out, total, unpriced = [], 0.0, 0
    for c in lines:
        front = _front(c["name"])
        unit = by_printing.get((front, (c.get("set") or "").lower(), (c.get("number") or "").lower())) \
            if c.get("set") and c.get("number") else None
        unit = unit if unit is not None else by_name.get(front)
        cost = round(unit * c["missing"], 2) if unit is not None else None
        if c["missing"]:
            if cost is None:
                unpriced += 1
            else:
                total += cost
        out.append({**c, "unit_price": None if unit is None else round(unit, 2), "missing_cost": cost if c["missing"] else 0.0,
                    "owned_printings": owned.get(front, [])[:MAX_OWNED_PRINTINGS]})
    return {**coverage, "cards": out, "missing_cost": round(total, 2), "missing_unpriced": unpriced}


# -- server-side price refresh --------------------------------------------------------------------

REFRESH_CHUNK = 300  # printings per call: four Scryfall /cards/collection requests of 75


def refresh_state(db: Session, user_id: int, day: date, after: str | None, force: bool,
                  limit: int | None = None) -> dict:
    """Where a refresh stands: ``total`` printings owned, the next ``limit`` ids to refresh (after
    ``after``, in id order), how many remain, and rows not matched to a printing yet. A printing is
    done for the day once it has that day's price snapshot (unless ``force``), or once a call has
    passed it (``after``)."""
    total, unmatched = db.execute(text("""
SELECT count(DISTINCT scryfall_id), count(*) FILTER (WHERE scryfall_id IS NULL) FROM entries WHERE user_id = :uid"""),
                                  {"uid": user_id}).one()
    todo = f"""
SELECT DISTINCT e.scryfall_id FROM entries e
WHERE e.user_id = :uid AND e.scryfall_id IS NOT NULL AND (CAST(:after AS text) IS NULL OR e.scryfall_id > :after)
{'' if force else 'AND NOT EXISTS (SELECT 1 FROM price_snapshots p WHERE p.scryfall_id = e.scryfall_id AND p.day = :day)'}"""
    params = {"uid": user_id, "after": after, "day": day, "lim": REFRESH_CHUNK if limit is None else limit}
    remaining = db.scalar(text(f"SELECT count(*) FROM ({todo}) t"), params)
    ids = db.scalars(text(todo + " ORDER BY e.scryfall_id LIMIT :lim"), params).all()
    return {"total": int(total), "remaining": int(remaining), "ids": list(ids), "unmatched": int(unmatched)}
