"""Printings an import can match a row to when the printing the file names is not one the Vault already holds.

``cards`` holds the printings someone owns. A row of a person's file that names another printing (a set code that is
the app's own, a collector number the catalog numbers differently) used to be left for the daily sync, and the person
was told to fix the file. This reads, for the names of those rows only, the catalog the Vault already keeps:
``oracle_cards`` (every card's name) and ``oracle_printings`` (every priced paper printing, with its set, number,
language and today's prices), and, for names the catalog does not know, the owned printings. The matching itself is the
library's (``mtg_toolkits.matching``: one ladder and one stated rule, the same the daily sync uses), so a preview, the
import and the next sync agree.

Bounded: at most ``MAX_LOOKUP_NAMES`` distinct card names are looked up in one import; the rest are left to the daily
sync and said so (never "not found").
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from mtg_toolkits import normalize_collector_number, normalize_set_code
from mtg_toolkits.matching import Printing
from mtg_toolkits.normalize import fallback_set_code
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from .models import Card, OracleCard, OraclePrinting
from .prices import latest_prices

MAX_LOOKUP_NAMES = 5000
BATCH = 500


def front(name: str) -> str:
    return name.split(" // ")[0].strip()


@dataclass
class Lookup:
    printings: list[Printing] = field(default_factory=list)
    known: set[str] = field(default_factory=set)  # names (lower-case, front face too) the catalog holds a card for
    left_out: set[str] = field(default_factory=set)  # names past MAX_LOOKUP_NAMES, which nobody looked at

    def why_not(self, name: str) -> str:
        """For a row no printing was found for: ``"not_found"`` (no card of that name in the catalog the Vault holds) or
        ``"later"`` (the card is known, or was not looked at: the daily update matches it)."""
        key = front(name).lower()
        return "later" if key in self.known or key in self.left_out else "not_found"


def printings_of(db: Session, names: Iterable[str], numbers: Iterable[tuple[str, str]] = ()) -> Lookup:
    """The printings of the cards named (lower-case, as the files write them), and what the lookup could say about them.

    ``numbers`` are the rows' (set code, collector number) as written: the printings with those (and with the code's
    fallback set), whatever the row's name is, so the preview finds an exact printing the way the daily sync does."""
    wanted = sorted({n for n in names if n})
    left_out, wanted = set(wanted[MAX_LOOKUP_NAMES:]), wanted[:MAX_LOOKUP_NAMES]
    oracle: dict[str, str] = {}  # oracle id -> the card's full name
    for i in range(0, len(wanted), BATCH):
        batch = wanted[i:i + BATCH]
        rows = db.execute(select(OracleCard.oracle_id, OracleCard.name).where(
            func.lower(OracleCard.name).in_(batch) | func.lower(func.split_part(OracleCard.name, " // ", 1)).in_(batch)))
        oracle.update(rows.all())
    printings: dict[str, Printing] = {}
    ids = list(oracle)
    for i in range(0, len(ids), BATCH * 2):
        for p in db.scalars(select(OraclePrinting).where(OraclePrinting.oracle_id.in_(ids[i:i + BATCH * 2]))):
            printings[p.scryfall_id] = Printing(p.scryfall_id, oracle[p.oracle_id], p.set_code, p.collector_number, p.lang,
                                                p.usd, p.usd_foil, p.usd_etched)
    wanted_pairs: set[tuple[str, str]] = set()
    for code, number in numbers:
        c, n = normalize_set_code(code), normalize_collector_number(number)
        if not (c and n):
            continue
        forms = {n, (number or "").strip().lower()}
        for set_code in {c, fallback_set_code(c)} - {None}:
            wanted_pairs |= {(set_code, f) for f in forms}
    pairs = sorted(wanted_pairs)
    for i in range(0, len(pairs), BATCH):
        rows = db.execute(
            select(OraclePrinting, OracleCard.name).join(OracleCard, OracleCard.oracle_id == OraclePrinting.oracle_id)
            .where(tuple_(OraclePrinting.set_code, func.lower(OraclePrinting.collector_number)).in_(pairs[i:i + BATCH])))
        for p, name in rows:
            printings.setdefault(p.scryfall_id, Printing(p.scryfall_id, name, p.set_code, p.collector_number, p.lang,
                                                         p.usd, p.usd_foil, p.usd_etched))
    # names the catalog does not know: the printings someone owns (a deployment whose catalog is not loaded yet, or a card
    # that is new), priced from the daily snapshot
    known = {n for name in oracle.values() for n in (name.lower(), front(name).lower())}
    unknown = [n for n in wanted if n not in known]
    owned: list[Card] = []
    for i in range(0, len(unknown), BATCH):
        batch = unknown[i:i + BATCH]
        owned += db.scalars(select(Card).where(
            func.lower(Card.name).in_(batch) | func.lower(func.split_part(Card.name, " // ", 1)).in_(batch)))
    if owned:
        prices = latest_prices(db, {c.scryfall_id for c in owned})
        for c in owned:
            snap = prices.get(c.scryfall_id)
            printings.setdefault(c.scryfall_id, Printing(
                c.scryfall_id, c.name, c.set_code, c.collector_number, "en",
                snap.usd if snap else None, snap.usd_foil if snap else None, snap.usd_etched if snap else None))
    known |= {p.name.lower() for p in printings.values()} | {front(p.name).lower() for p in printings.values()}
    return Lookup(list(printings.values()), known, left_out)
