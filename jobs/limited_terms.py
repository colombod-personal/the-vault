"""The 17Lands terms re-read (#417, docs/limited-data-design.md section 6, docs/compliance.md "17Lands").

The licence page is drawn by script, so no automated fetch can read the sentence the Vault relies on. A person re-reads the pages every
90 days and writes the day in ``docs/compliance.md`` ("17Lands terms read on: YYYY-MM-DD"). Two things hold the Vault to that:

* the monthly reminder issue (``jobs/budget_alert.py`` kind ``limited-terms``, started by ``.github/workflows/limited-terms-monthly.yml``)
  lists the pages and the sentence and says how old the recorded date is;
* ``jobs/sync_limited.py`` calls :func:`check` first and refuses to run once that date is more than 120 days old (or cannot be read).
  There is no switch to skip the check: re-reading the pages and moving the date is the only way on.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

COMPLIANCE = Path(__file__).resolve().parent.parent / "docs" / "compliance.md"
REREAD_EVERY_DAYS = 90  # a person re-reads at least this often (the reminder says when it is due)
REFUSE_AFTER_DAYS = 120  # the job refuses to run once the recorded date is older than this
LINE = re.compile(r"^\**17Lands terms read on:\**\s*(\d{4}-\d{2}-\d{2})\b", re.MULTILINE)

PAGES = (
    ("The public data sets page (the licence sentence)", "https://www.17lands.com/public_datasets"),
    ("The terms of service", "https://www.17lands.com/terms_of_service"),
    ("The usage guidelines", "https://www.17lands.com/usage_guidelines"),
    ("The FAQ (when the files are published and refreshed)", "https://www.17lands.com/faq"),
    ("The metrics definitions", "https://www.17lands.com/metrics_definitions"),
    ("robots.txt", "https://www.17lands.com/robots.txt"),
    ("The CC BY 4.0 legal code", "https://creativecommons.org/licenses/by/4.0/legalcode.en"),
)
LICENCE_SENTENCE = ("Unless otherwise noted, these data sets are licensed under a Creative Commons Attribution 4.0 International License")


class TermsStale(Exception):
    """The recorded day cannot be read, or is too old: the job does not run."""


def today() -> date:  # a name tests replace
    return datetime.now(timezone.utc).date()


def terms_read_on(path: Path | None = None) -> date:
    text = (path or COMPLIANCE).read_text(encoding="utf-8")
    found = LINE.search(text)
    if not found:
        raise TermsStale("docs/compliance.md has no '17Lands terms read on: YYYY-MM-DD' line")
    try:
        return date.fromisoformat(found.group(1))
    except ValueError as exc:
        raise TermsStale(f"docs/compliance.md: '{found.group(1)}' is not a date") from exc


def age_days(path: Path | None = None, now: date | None = None) -> int:
    return ((now or today()) - terms_read_on(path)).days


def check(path: Path | None = None, now: date | None = None) -> int:
    """The age in days of the recorded reading, or :class:`TermsStale` when it is older than ``REFUSE_AFTER_DAYS``."""
    days = age_days(path, now)
    if days > REFUSE_AFTER_DAYS:
        raise TermsStale(f"the 17Lands terms were last read {days} days ago (on {terms_read_on(path)}); the job refuses to run after "
                         f"{REFUSE_AFTER_DAYS} days. Re-read the pages listed in the monthly reminder issue (or docs/compliance.md, "
                         "'17Lands'), then change the '17Lands terms read on' date in docs/compliance.md")
    return days


def reminder_body(path: Path | None = None, now: date | None = None) -> str:
    """The text of the monthly reminder issue."""
    try:
        read = terms_read_on(path)
        days = ((now or today()) - read).days
        state = (f"The recorded reading is **{read}**, {days} days ago. " + (
            f"It is **due**: re-read now (the job refuses to run {REFUSE_AFTER_DAYS - days} days from now)." if days >= REREAD_EVERY_DAYS and days <= REFUSE_AFTER_DAYS
            else f"**The job is refusing to run** (more than {REFUSE_AFTER_DAYS} days): re-read now." if days > REFUSE_AFTER_DAYS
            else f"Not due yet (due at {REREAD_EVERY_DAYS} days); look at the pages anyway if 17Lands has announced a change."))
    except (TermsStale, OSError) as exc:
        state = f"**The recorded date cannot be read ({exc}); the job refuses to run until it can.**"
    pages = "\n".join(f"- [ ] {name}: {url}" for name, url in PAGES)
    return f"""It is the first of the month. The Limited statistics (`sync-limited`, the 17Lands public data sets) are used under their terms; the
pages are drawn by script, so a program cannot read them: a person must. {state}

Open each page and check that nothing that matters has changed:

{pages}

The sentence the Vault relies on, on the public data sets page, is: "{LICENCE_SENTENCE}". Also check that the page still asks for credit and that
nothing forbids reducing the files to per-card counts (the rules and what they mean for the Vault are in `docs/compliance.md`, "17Lands").

- [ ] If anything changed: say so in this issue and in `docs/compliance.md` before the next weekly run (switch the source off with the repository variable `CATALOG_SOURCES` if the licence no longer allows it).
- [ ] Change the date in the line `17Lands terms read on: YYYY-MM-DD` of `docs/compliance.md` (one-line pull request).

Re-read at least every {REREAD_EVERY_DAYS} days. Close this issue when done; the next one opens on the 1st of next month.
"""
