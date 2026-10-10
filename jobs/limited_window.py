"""The rolling window of sets the Limited job keeps (#417, docs/limited-data-design.md sections 3, 4 and 13).

Pure functions, no network: which Scryfall sets are candidates, which are too new to read yet, and when a set leaves the window.
``jobs/sync_limited.py`` asks the 17Lands bucket about each candidate (HEAD) and applies the rules below.

* **Candidates.** Scryfall sets of type expansion, core, masters or draft_innovation released in the last 30 months, newest first, with a
  code 17Lands could use (letters and digits, upper-cased).
* **The wait.** A set is not read before 14 days after its ``released_at`` (our reading of 17Lands' day-12 embargo wish; it does not
  formally apply to the public files). A set inside the wait is neither read nor deleted.
* **The window.** Per format, the 8 newest candidates that have a game file. A stored set that leaves it is deleted, with its rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

SET_TYPES = frozenset(("expansion", "core", "masters", "draft_innovation"))
WINDOW_SETS = 8
HORIZON_MONTHS = 30
WAIT_DAYS = 14
CODE = re.compile(r"^[A-Z0-9]{2,6}$")


@dataclass(frozen=True)
class Candidate:
    code: str  # 17Lands' spelling: the upper-case Scryfall code
    released: date
    name: str = ""


def months_before(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    for d in range(day.day, 0, -1):  # 31 March minus one month is the end of February
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def candidates(scryfall_sets: list[dict], today: date) -> tuple[list[Candidate], dict[str, date]]:
    """``(candidates, embargoed)``: the sets to ask S3 about, newest first, and the codes that are too new to read (with their
    release day). A set without a usable ``released_at`` or code is left out."""
    oldest, newest = months_before(today, HORIZON_MONTHS), today - timedelta(days=WAIT_DAYS)
    found: dict[str, Candidate] = {}
    embargoed: dict[str, date] = {}
    for entry in scryfall_sets:
        if entry.get("set_type") not in SET_TYPES:
            continue
        code = str(entry.get("code") or "").upper()
        try:
            released = date.fromisoformat(str(entry.get("released_at") or ""))
        except ValueError:
            continue
        if not CODE.match(code) or released < oldest:
            continue
        if released > newest:
            embargoed[code] = released
        else:
            found[code] = Candidate(code, released, str(entry.get("name") or ""))
    return sorted(found.values(), key=lambda c: (c.released, c.code), reverse=True), embargoed


def leaving(stored: set[tuple[str, str]], window: dict[str, list[str]], embargoed: dict[str, date], formats: list[str]) -> list[tuple[str, str]]:
    """The stored ``(set, format)`` pairs that are no longer in the window, in the formats this run looked at. A format whose window
    came out empty removes nothing (it means the discovery found nothing, which is more likely a problem than a change), and a set
    inside the wait is never removed."""
    out = []
    for code, fmt in sorted(stored):
        if fmt in formats and window.get(fmt) and code not in window[fmt] and code not in embargoed:
            out.append((code, fmt))
    return out
