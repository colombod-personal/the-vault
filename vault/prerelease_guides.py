"""Prerelease guides: for a set, where Wizards' pages are and the few facts read from them (vault-064.10.8).

Two kinds of page per set: the **player guide** on magic.wizards.com (what a Prerelease Pack holds, how to build the 40-card
Sealed deck) and the **WPN event page** on wpn.wizards.com (dates, templates, the Two-Headed Giant arrangement). Their address
cannot be worked out from the set's name (``spider-man-prerelease-guide``, ``the-guide-to-your-first-prerelease-with-aetherdrift``,
``prerelease-marvels-spider-man``), so the Vault keeps a table, ``set_prerelease_guides``: filled by hand for the first sets
(``vault/prerelease_seed.py``) and by the discovery job (``jobs/sync_prerelease_guides.py``) for new ones.

What is kept: links, the page's own title as a link label, the prerelease dates and the number of packs and boosters, each with the
page it was read from, the day, and ``verified`` or ``unverified``. What is never kept: any text of a Wizards page
(docs/compliance.md, "Wizards' prerelease pages"). The answer says so, shows the source of every fact and says plainly when no
guide is published.

This module holds the pure parts (matching a set to a page, reading the few facts out of a page, the answer) and the discovery
run, which takes an ``httpx.Client`` so tests send it to the Wizards twin (``twins/wizards.py``) and never to the real web.
"""

from __future__ import annotations

import html as htmllib
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import provenance as prov
from .models import SetPrereleaseGuide

SOURCE_NAME = "prerelease_guides"  # the key in the Source gate (docs/compliance.md) and in CATALOG_SOURCES
USER_AGENT = "the-vault/0.1 (+https://github.com/colombod-personal/the-vault)"
MAGIC_SITE, WPN_SITE = "magic.wizards.com", "wpn.wizards.com"
ALLOWED_HOSTS = frozenset((MAGIC_SITE, WPN_SITE))  # the only hosts the job ever contacts
MAGIC_SITEMAP = f"https://{MAGIC_SITE}/en/sitemap.xml"
WPN_SITEMAP_INDEX = f"https://{WPN_SITE}/sitemap_index.xml"
WPN_SITEMAP_PATH = "/__sitemap__/en/sitemap.xml"  # where the index points (on WPN's origin host, which the Vault never contacts)

SET_TYPES = frozenset(("expansion", "core"))  # the set types that have a Prerelease
PRERELEASE_LEAD_DAYS = 7  # a Prerelease week starts on the Friday a week before the release (checked on 14 sets, 2024-2026)
SEARCH_WINDOW_DAYS = 45  # the job looks for a guide once the prerelease is this near (the owner's rule)
RECENT_DAYS = 7  # a set whose prerelease ended less than this long ago is still looked at
PAGE_LIMIT = 3_000_000  # bytes read from any one page or sitemap
SITEMAP_LIMIT = 6_000_000
PAUSE = 0.5  # seconds between two page requests of one run
FORMATS = ("traditional", "two_headed_giant")
FORMAT_NAMES = {"traditional": "Traditional (one against one)", "two_headed_giant": "Two-Headed Giant (teams of two)"}
GUIDE_NOTE = ("The Vault keeps links and a few facts (dates, pack counts), each with the page it came from and the day it was "
              "read, and no text of a Wizards page. Dates and contents can differ by region and by store: the store's own "
              "listing decides.")


def sleep(seconds: float) -> None:  # a name tests replace
    time.sleep(seconds)


def today() -> date:  # a name tests replace
    return datetime.now(timezone.utc).date()


# -- dates -------------------------------------------------------------------------------------

def derived_dates(released: date) -> tuple[date, date]:
    """The prerelease week worked out from the release date: it starts seven days before it and ends the day before it."""
    return released - timedelta(days=PRERELEASE_LEAD_DAYS), released - timedelta(days=1)


MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september",
                                      "october", "november", "december"), 1)}
MONTHS |= {k[:3]: v for k, v in list(MONTHS.items())} | {"sept": 9}
_MONTH = r"([A-Za-z]{3,9})\.?"
SCHEDULING = re.compile(rf"Scheduling\s*:\s*(?:Between\s+)?{_MONTH}\s+(\d{{1,2}})\s*[–—-]\s*(?:{_MONTH}\s+)?(\d{{1,2}})")
STARTING = re.compile(rf"scheduled starting at .{{0,40}}? on (?:[A-Z][a-z]+day, )?{_MONTH}\s+(\d{{1,2}}),?(?: \d{{4}},?)? and "
                      rf"throughout the week until (?:{_MONTH}\s+)?(\d{{1,2}})")


def _month(name: str | None) -> int | None:
    return MONTHS.get((name or "").lower())


def parse_event_dates(lines: list[str], released: date | None) -> tuple[date, date] | None:
    """The prerelease week an event page states ("Scheduling: September 25-October 1" or "can be scheduled starting ... on Friday,
    September 25, and throughout the week until October 1"), with the year taken from the release date. None when the page says
    nothing the Vault can read, or says something more than four days away from the week before the release (then it is not
    trusted: a person looks)."""
    if released is None:
        return None
    text = " ".join(lines)
    for pattern in (SCHEDULING, STARTING):
        found = pattern.search(text)
        if not found:
            continue
        m1, d1, m2, d2 = found.groups()
        a, b = _month(m1), _month(m2) or _month(m1)
        if not a or not b:
            continue
        expected = released - timedelta(days=PRERELEASE_LEAD_DAYS)
        for year in (released.year, released.year - 1, released.year + 1):
            try:
                start = date(year, a, int(d1))
                end = date(year + (1 if b < a else 0), b, int(d2))
            except ValueError:
                continue
            if abs((start - expected).days) <= 4 and start <= end <= start + timedelta(days=14):
                return start, end
    return None


# -- reading a page (nothing of the text is kept) -------------------------------------------------

def page_lines(raw: str) -> list[str]:
    """A page as a list of text lines (tags removed, one block per line). Used only to read facts out of it."""
    body = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", raw)
    body = re.sub(r"</(p|li|div|h\d|ul|ol|tr|section|article)>|<br\s*/?>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = htmllib.unescape(body).replace("\xa0", " ")
    return [re.sub(r"[ \t]+", " ", l).strip() for l in body.split("\n") if l.strip()]


def page_title(raw: str) -> str | None:
    found = re.search(r"<title>([^<]*)</title>", raw, re.I)
    title = re.sub(r"\s+", " ", htmllib.unescape(found.group(1))).strip() if found else ""
    return title[:300] or None


PLAY_BOOSTERS = re.compile(r"^(\d{1,2})\s+.*\bPlay Boosters?\b", re.I)
OTHER_BOOSTER = re.compile(r"^(\d{1,2})\s+.*\bbooster\b", re.I)


def parse_pack_contents(lines: list[str]) -> dict | None:
    """What one Prerelease Pack holds, from the guide's list ("... Prerelease Pack contains the following: 6 ... Play Boosters, 1 ...
    foil promo card ..."): the number of Play Boosters and of other boosters. Only when exactly one line gives a Play Booster count;
    otherwise None (the row says the contents are unverified)."""
    for i, line in enumerate(lines):
        if re.search(r"contain(s|ing) the following", line, re.I):
            items = lines[i + 1:i + 10]
            plays = [m for m in (PLAY_BOOSTERS.match(l) for l in items) if m]
            if len(plays) != 1:
                return None
            others = sum(int(m.group(1)) for m in (OTHER_BOOSTER.match(l) for l in items)
                         if m and not PLAY_BOOSTERS.match(m.string))
            return {"play_boosters_in_each_pack": int(plays[0].group(1)), "other_boosters_in_each_pack": others}
    return None


def parse_two_headed_giant(lines: list[str]) -> dict | None:
    """The Two-Headed Giant arrangement an event page states, as numbers: ``{"prerelease_packs": 1, "loose_play_boosters": 2}``."""
    text = " ".join(lines)
    if re.search(r"each team (?:will now )?receives? one Prerelease Pack plus two Play Boosters", text, re.I):
        return {"prerelease_packs": 1, "loose_play_boosters": 2}
    if re.search(r"each team (?:will now )?receives? two Prerelease Packs", text, re.I):
        return {"prerelease_packs": 2, "loose_play_boosters": 0}
    return None


# -- sitemaps and matching a set to a page --------------------------------------------------------

def parse_sitemap(xml: str) -> list[str]:
    """The ``<loc>`` addresses of a sitemap (a regular expression, not an XML parser: the file is someone else's)."""
    return [htmllib.unescape(u).strip() for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)]


STOPWORDS = {"the", "of", "and", "a", "to", "your", "first", "with", "magic", "gathering", "mtg", "prerelease", "guide", "primer",
             "draft", "planning", "set"}


def name_tokens(text: str) -> frozenset[str]:
    """Words that identify a set in a name or an address slug: lower-case, without possessives and plurals, without the words every
    prerelease page has."""
    text = re.sub(r"['’]s\b", "", text.lower())
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w]
    return frozenset((w[:-1] if len(w) > 3 and w.endswith("s") else w) for w in words if w not in STOPWORDS)


def match_pages(set_name: str, urls: list[str], kind: str) -> list[str]:
    """The addresses among ``urls`` that are this set's page of this kind. A page matches when every identifying word of its slug is a
    word of the set's name; the page whose words equal the set's are preferred. ``kind`` is ``guide`` (a feature article on
    magic.wizards.com with "prerelease" in its slug) or ``event`` (``/event/prerelease-...`` on WPN)."""
    wanted = name_tokens(set_name)
    if not wanted:
        return []
    exact, partial = [], []
    for url in urls:
        parsed = httpx.URL(url)
        slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if kind == "guide":
            if not (parsed.host == MAGIC_SITE and "/news/feature/" in parsed.path and "prerelease" in slug):
                continue
        elif not (parsed.path.startswith("/en/event/prerelease-") and slug.startswith("prerelease-")):
            continue
        tokens = name_tokens(slug)
        if tokens and tokens <= wanted:
            (exact if tokens == wanted else partial).append(url)
    return exact or partial


# -- the answer ------------------------------------------------------------------------------------

def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


def composition_entry(entry: dict | None, fmt: str) -> dict | None:
    """One format's pack composition as the answer shows it, with the totals the Vault works out from the counts."""
    if not entry:
        return None
    out = {"format": FORMAT_NAMES[fmt], **{k: v for k, v in entry.items() if k != "corroborated_by"}}
    packs, loose = entry.get("prerelease_packs"), entry.get("loose_play_boosters") or 0
    each = entry.get("play_boosters_in_each_pack")
    if packs is not None and each is not None:
        out["play_boosters_total"] = packs * each + loose  # computed by the Vault from the counts above
    elif fmt == "two_headed_giant" and packs is not None and entry.get("status") == "verified":
        out["play_boosters_loose"] = loose
    if entry.get("corroborated_by"):
        out["also_stated_at"] = entry["corroborated_by"]
    return out


def _fact(url: str | None, title: str | None, status: str, fetched: datetime | None) -> dict | None:
    if not url:
        return None
    return {"url": url, "title": title, "status": status, "source_url": url, "fetched_at": _iso(fetched)}


def row_body(row: SetPrereleaseGuide, day: date) -> dict:
    """One row as the tool shows it: the facts, each with its source and status, and the plain sentence for the person."""
    start, end = row.prerelease_start, row.prerelease_end
    dates = None
    if start:
        dates = {"start": start.isoformat(), "end": end.isoformat() if end else None, "status": row.dates_status,
                 "source_url": row.dates_source_url, "fetched_at": _iso(row.dates_fetched_at),
                 "basis": "read from a Wizards page" if row.dates_status == "verified"
                 else "worked out by the Vault from the release date (the week before it); no Wizards page was read for the dates"}
    state = ("past" if end and end < day else "ongoing" if start and start <= day else "upcoming") if start else "unknown"
    guide = _fact(row.player_guide_url, row.player_guide_title, row.player_guide_status, row.player_guide_fetched_at)
    event = _fact(row.wpn_event_url, row.wpn_event_title, row.wpn_event_status, row.wpn_event_fetched_at)
    composition = {fmt: composition_entry((row.composition or {}).get(fmt), fmt) for fmt in FORMATS}
    return {"set": {"code": row.set_code, "name": row.set_name, "released_at": _iso(row.released_at)},
            "row_status": row.status, "prerelease": dates, "prerelease_state": state,
            "player_guide": guide, "wpn_event_page": event, "pack_composition": composition,
            "guide_published": bool(row.player_guide_url), "note": row.note, "source_change_note": row.source_change_note,
            "last_looked_at": _iso(row.last_looked_at), "message": message_for(row, day)}


def message_for(row: SetPrereleaseGuide, day: date) -> str:
    name = row.set_name
    parts = []
    if row.player_guide_url:
        parts.append(f"Wizards has published a prerelease player guide for {name}: {row.player_guide_url}.")
    else:
        looked = f" (last looked for {row.last_looked_at.date().isoformat()})" if row.last_looked_at else ""
        parts.append(f"No prerelease player guide has been published yet for {name}{looked}: the Vault holds no link to one.")
    if row.wpn_event_url:
        parts.append(f"Wizards' store event page for it (dates and event templates, written for stores): {row.wpn_event_url}.")
    if row.prerelease_start:
        verb = "were" if row.prerelease_end and row.prerelease_end < day else "are"
        basis = "" if row.dates_status == "verified" else " (worked out by the Vault from the release date, not read from Wizards)"
        parts.append(f"The prerelease events {verb} {row.prerelease_start.isoformat()} to "
                     f"{(row.prerelease_end or row.prerelease_start).isoformat()}{basis}.")
    return " ".join(parts)


def provenance_for(row: SetPrereleaseGuide, body: dict) -> list[prov.Provenance]:
    """One source block per Wizards page used (Wizards' pages, linked and not copied), and a computed block for what the Vault worked
    out (dates from the release date, booster totals)."""
    blocks: list[prov.Provenance] = []
    seen: set[str] = set()

    def page(url: str | None, what: str, fetched) -> None:
        if url and url not in seen:
            seen.add(url)
            blocks.append(prov.source("Wizards of the Coast", origin=what, url=url, as_of=fetched, wizards_material=True,
                                      changes="Only the link and a few facts (dates, pack counts) were read from the page; no text of it is kept."))

    page(row.player_guide_url, "Prerelease player guide (magic.wizards.com)", row.player_guide_fetched_at)
    page(row.wpn_event_url, "Prerelease event page (Wizards Play Network)", row.wpn_event_fetched_at)
    if row.dates_status == "verified":
        page(row.dates_source_url, "Prerelease dates (Wizards page)", row.dates_fetched_at)
    for fmt in FORMATS:
        entry = (row.composition or {}).get(fmt) or {}
        page(entry.get("source_url"), f"Pack contents, {FORMAT_NAMES[fmt]} (Wizards page)",
             datetime.fromisoformat(entry["fetched_at"]) if entry.get("fetched_at") else None)
        for extra in entry.get("corroborated_by") or []:
            page(extra, f"Pack contents, {FORMAT_NAMES[fmt]} (Wizards announcement)", None)
    inputs = [prov.source("Scryfall", origin="the set's release date", url=f"https://scryfall.com/sets/{row.set_code}",
                          as_of=row.updated_at)]
    if row.dates_status != "verified" and row.prerelease_start:
        blocks.append(prov.computed("Prerelease dates worked out as the seven days before the release date; not read from a Wizards page",
                                    inputs, as_of=row.updated_at))
    if any("play_boosters_total" in (c or {}) for c in body["pack_composition"].values()):
        blocks.append(prov.computed("Play Boosters per player or team: packs times the boosters in each pack, plus loose Play Boosters",
                                    [b for b in blocks if b.kind == "source"], as_of=row.updated_at))
    return blocks


def find_row(db: Session, query: str) -> tuple[SetPrereleaseGuide | None, list[SetPrereleaseGuide]]:
    """A set by its code, else by its name (exact, then the only one whose name contains the words). The second value lists the
    candidates when the name fits several."""
    wanted = query.strip().lower()
    if not wanted:
        return None, []
    row = db.get(SetPrereleaseGuide, wanted)
    if row:
        return row, []
    rows = list(db.scalars(select(SetPrereleaseGuide)))
    exact = [r for r in rows if r.set_name.lower() == wanted]
    if len(exact) == 1:
        return exact[0], []
    contains = [r for r in rows if wanted in r.set_name.lower()]
    if len(contains) == 1:
        return contains[0], []
    return None, contains or exact


def nearest_upcoming(db: Session, day: date) -> SetPrereleaseGuide | None:
    """The set whose prerelease is under way or next (the earliest end date on or after ``day``)."""
    return db.scalars(select(SetPrereleaseGuide).where(SetPrereleaseGuide.prerelease_end >= day)
                      .order_by(SetPrereleaseGuide.prerelease_start, SetPrereleaseGuide.set_code).limit(1)).first()


def latest_past(db: Session, day: date) -> SetPrereleaseGuide | None:
    return db.scalars(select(SetPrereleaseGuide).where(SetPrereleaseGuide.prerelease_end < day)
                      .order_by(SetPrereleaseGuide.prerelease_end.desc(), SetPrereleaseGuide.set_code).limit(1)).first()


def _brief(row: SetPrereleaseGuide, day: date) -> dict:
    return {"code": row.set_code, "name": row.set_name, "prerelease_start": _iso(row.prerelease_start),
            "prerelease_end": _iso(row.prerelease_end), "guide_published": bool(row.player_guide_url)}


def answer(db: Session, query: str | None, day: date | None = None) -> dict:
    """The ``get_prerelease_guide`` answer. With a set (code or name): its row, or a plain "the Vault has no record" with the nearest
    upcoming prerelease offered. With none: the nearest upcoming prerelease (else the latest past one)."""
    day = day or today()
    chosen_because = None
    row: SetPrereleaseGuide | None = None
    candidates: list[SetPrereleaseGuide] = []
    if query and query.strip():
        row, candidates = find_row(db, query)
    else:
        row = nearest_upcoming(db, day)
        if row is not None:
            chosen_because = "No set was named: this is the prerelease under way or next on the Vault's table."
        else:
            row = latest_past(db, day)
            if row is not None:
                chosen_because = ("No set was named and no prerelease is upcoming on the Vault's table: this is the most recent one.")
    if row is None:
        upcoming = nearest_upcoming(db, day)
        if candidates:
            said = "Several sets fit that name: " + ", ".join(f"{c.set_name} ({c.set_code})" for c in candidates[:8]) + "."
        elif query and query.strip():
            said = (f"The Vault has no prerelease record for \"{query.strip()}\". That is not a statement that Wizards has published "
                    "nothing: the table holds the sets of the last two years and the sets announced since.")
        else:
            said = "The Vault's prerelease table is empty."
        return {"found": False, "set": None, "guide_published": False, "message": said,
                "nearest_upcoming": _brief(upcoming, day) if upcoming else None, "candidates": [_brief(c, day) for c in candidates[:8]],
                "caveats": [GUIDE_NOTE], "provenance": [], "as_of": day.isoformat()}
    body = row_body(row, day)
    body |= {"found": True, "caveats": [GUIDE_NOTE], "as_of": day.isoformat()}
    if chosen_because:
        body["chosen_because"] = chosen_because
    if query and query.strip() and row.prerelease_end and row.prerelease_end < day:
        upcoming = nearest_upcoming(db, day)
        if upcoming and upcoming.set_code != row.set_code:
            body["nearest_upcoming"] = _brief(upcoming, day)
    body["provenance"] = provenance_for(row, body)
    return body


# -- the discovery run ---------------------------------------------------------------------------

@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    found: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    needs_a_person: list[dict] = field(default_factory=list)
    requests: int = 0

    def as_dict(self) -> dict:
        return {"rows_created": self.created, "found": self.found, "no_guide_found": self.missing, "source_changed": self.changed,
                "needs_a_person": self.needs_a_person, "problems": self.problems, "requests": self.requests}


class FetchError(Exception):
    pass


def _fetch(client: httpx.Client, url: str, report: Report, limit: int = PAGE_LIMIT) -> str:
    """One GET of a page on one of the two Wizards hosts, never anything else, with a size cap. Raises FetchError."""
    host = httpx.URL(url).host
    if host not in ALLOWED_HOSTS:
        raise FetchError(f"{host} is not one of the hosts this job may contact")
    report.requests += 1
    try:
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.5"},
                           follow_redirects=False) as response:
            if response.status_code != 200:
                raise FetchError(f"{url} answered {response.status_code}")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise FetchError(f"{url} is larger than {limit} bytes")
                chunks.append(chunk)
            return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    except httpx.HTTPError as exc:
        raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc


def upcoming_sets(scryfall_sets: list[dict], day: date) -> list[dict]:
    """The sets worth a row: expansions and core sets (no digital sets, no child sets) whose prerelease has not ended more than
    ``RECENT_DAYS`` ago, with the release date as a date."""
    out = []
    for item in scryfall_sets:
        if item.get("set_type") not in SET_TYPES or item.get("digital") or item.get("parent_set_code"):
            continue
        try:
            released = date.fromisoformat(item["released_at"])
        except (KeyError, TypeError, ValueError):
            continue
        code = str(item.get("code") or "").lower()
        if not re.fullmatch(r"[a-z0-9]{2,10}", code) or not item.get("name"):
            continue
        if derived_dates(released)[1] >= day - timedelta(days=RECENT_DAYS):
            out.append({"code": code, "name": str(item["name"])[:200], "released_at": released})
    return sorted(out, key=lambda s: (s["released_at"], s["code"]))


def _status(row: SetPrereleaseGuide) -> str:
    """verified when the dates were read from a page and a page of the set (guide or event) is verified."""
    pages = (row.player_guide_status if row.player_guide_url else "", row.wpn_event_status if row.wpn_event_url else "")
    return "verified" if row.dates_status == "verified" and "verified" in pages else "unverified"


def _complete(row: SetPrereleaseGuide) -> bool:
    return (row.player_guide_url and row.player_guide_status == "verified" and row.wpn_event_url
            and row.wpn_event_status == "verified" and row.dates_status == "verified")


def ensure_rows(db: Session, sets: list[dict], day: date, now: datetime, report: Report) -> dict[str, SetPrereleaseGuide]:
    """A row for every upcoming set, as soon as Scryfall lists it (dates worked out from its release date, unverified, with the
    reason written in the row). A verified date is never replaced by the worked-out one."""
    rows = {r.set_code: r for r in db.scalars(select(SetPrereleaseGuide))}
    for item in sets:
        start, end = derived_dates(item["released_at"])
        row = rows.get(item["code"])
        if row is None:
            row = SetPrereleaseGuide(
                set_code=item["code"], set_name=item["name"], released_at=item["released_at"], prerelease_start=start, prerelease_end=end,
                dates_status="unverified", dates_source_url=f"https://scryfall.com/sets/{item['code']}", dates_fetched_at=now,
                player_guide_status="unverified", wpn_event_status="unverified", composition={}, status="unverified",
                note=(f"Row created {day.isoformat()} from Scryfall's release date; the dates are worked out, not read from Wizards. "
                      f"The search for Wizards' pages starts {SEARCH_WINDOW_DAYS} days before the prerelease."))
            db.add(row)
            rows[item["code"]] = row
            report.created.append(item["code"])
        elif row.released_at != item["released_at"]:
            if row.dates_status == "verified":
                row.source_change_note = (f"{day.isoformat()}: Scryfall now lists the release as {item['released_at'].isoformat()}, the "
                                          f"row has {row.released_at.isoformat() if row.released_at else 'none'} and verified prerelease "
                                          f"dates {_iso(row.prerelease_start)} to {_iso(row.prerelease_end)}. The row was not changed.")
                report.changed.append({"set": row.set_code, "what": "release date", "to": item["released_at"].isoformat()})
            else:
                row.released_at, row.prerelease_start, row.prerelease_end = item["released_at"], start, end
                row.dates_fetched_at = now
    db.flush()
    return rows


def discover(db: Session, client: httpx.Client, sets: list[dict], day: date | None = None, *, now: datetime | None = None) -> Report:
    """One daily run: make sure every upcoming set has a row, and for the sets whose prerelease is within ``SEARCH_WINDOW_DAYS`` and
    whose pages are not all verified, look for the player guide and the event page in the two sitemaps, check each page found
    (one request), and write what was read. A set with nothing found gets an ``unverified`` row that says so, never silence. A
    verified fact is never overwritten: a different page or date is written to ``source_change_note`` and the row is left as it is."""
    day = day or today()
    now = now or datetime.now(timezone.utc)
    report = Report()
    rows = ensure_rows(db, sets, day, now, report)
    wanted = [r for r in rows.values() if r.prerelease_start and r.prerelease_end and r.prerelease_end >= day - timedelta(days=RECENT_DAYS)
              and r.prerelease_start <= day + timedelta(days=SEARCH_WINDOW_DAYS) and not _complete(r)]
    magic_urls: list[str] | None = None
    wpn_urls: list[str] | None = None
    if wanted:
        try:
            magic_urls = parse_sitemap(_fetch(client, MAGIC_SITEMAP, report, SITEMAP_LIMIT))
        except FetchError as exc:
            report.problems.append(f"magic.wizards.com sitemap: {exc}")
        try:
            index = _fetch(client, WPN_SITEMAP_INDEX, report, SITEMAP_LIMIT)
            english = next((u for u in parse_sitemap(index) if u.endswith(WPN_SITEMAP_PATH)), None)
            if english is None:
                raise FetchError("the English sitemap is not in WPN's sitemap index")
            sitemap = _fetch(client, f"https://{WPN_SITE}{WPN_SITEMAP_PATH}", report, SITEMAP_LIMIT)
            # the sitemap names WPN's origin host; the same paths on wpn.wizards.com are what a person opens
            wpn_urls = [f"https://{WPN_SITE}{httpx.URL(u).path}" for u in parse_sitemap(sitemap)]
        except FetchError as exc:
            report.problems.append(f"wpn.wizards.com sitemap: {exc}")
    for row in sorted(wanted, key=lambda r: (r.prerelease_start, r.set_code)):
        _look(db, client, row, magic_urls, wpn_urls, day, now, report)
    for row in rows.values():  # a set close to its prerelease with no verified guide is a job for a person
        if row.prerelease_start and row.prerelease_end and row.prerelease_end >= day and row.prerelease_start <= day + timedelta(days=PRERELEASE_LEAD_DAYS * 2) \
                and row.player_guide_status != "verified":
            report.needs_a_person.append({"set": row.set_code, "name": row.set_name, "prerelease_start": row.prerelease_start.isoformat(),
                                          "why": "no verified player guide"})
    db.flush()
    return report


def _look(db: Session, client: httpx.Client, row: SetPrereleaseGuide, magic_urls, wpn_urls, day: date, now: datetime, report: Report) -> None:
    searched = [s for s, urls in (("magic.wizards.com", magic_urls), ("wpn.wizards.com", wpn_urls)) if urls is not None]
    row.last_looked_at = now
    if row.player_guide_status != "verified" and magic_urls is not None:
        _take(client, row, "guide", match_pages(row.set_name, magic_urls, "guide"), now, report)
    elif row.player_guide_status == "verified" and magic_urls is not None:
        _check_differs(row, "guide", match_pages(row.set_name, magic_urls, "guide"), day, report)
    if row.wpn_event_status != "verified" and wpn_urls is not None:
        _take(client, row, "event", match_pages(row.set_name, wpn_urls, "event"), now, report)
    elif row.wpn_event_status == "verified" and wpn_urls is not None:
        _check_differs(row, "event", match_pages(row.set_name, wpn_urls, "event"), day, report)
    row.status = _status(row)
    missing = [what for what, url in (("player guide", row.player_guide_url), ("WPN event page", row.wpn_event_url)) if not url]
    notes = []
    if len(searched) < 2:
        notes.append(f"could not read every sitemap ({'; '.join(report.problems) or 'unknown reason'}); the row is from an earlier look")
    elif missing:
        notes.append(f"no {' and no '.join(missing)} found for {row.set_name} in the sitemaps of magic.wizards.com and wpn.wizards.com; "
                     "Wizards may not have published it yet, and the Vault looks again every day until the prerelease")
        report.missing.append({"set": row.set_code, "missing": missing})
    if row.dates_status != "verified":
        notes.append("the prerelease dates are worked out from the release date, not read from a Wizards page")
    if row.player_guide_url and (row.composition or {}).get("traditional", {}).get("status") != "verified":
        notes.append("the pack contents were not read from the guide (open it)")
    row.note = f"{day.isoformat()}: " + "; ".join(notes) + "." if notes else None
    if row.dates_status != "verified":
        row.dates_source_url = row.dates_source_url or f"https://scryfall.com/sets/{row.set_code}"


def _check_differs(row: SetPrereleaseGuide, kind: str, found: list[str], day: date, report: Report) -> None:
    have = row.player_guide_url if kind == "guide" else row.wpn_event_url
    if found and have not in found:
        row.source_change_note = (f"{day.isoformat()}: the sitemap now lists {found[0]} for this set; the verified "
                                  f"{'player guide' if kind == 'guide' else 'event page'} link {have} was kept. A person should look.")
        report.changed.append({"set": row.set_code, "what": kind, "to": found[0]})


def _take(client: httpx.Client, row: SetPrereleaseGuide, kind: str, found: list[str], now: datetime, report: Report) -> None:
    if not found:
        return
    if len(found) > 1:
        report.problems.append(f"{row.set_code}: {len(found)} {kind} pages fit the name ({', '.join(found[:4])}); none was taken")
        row.source_change_note = f"{now.date().isoformat()}: several {kind} pages fit this set: {', '.join(found[:4])}. None was taken."
        return
    url = found[0]
    try:
        raw = _fetch(client, url, report)
    except FetchError as exc:
        report.problems.append(f"{row.set_code} {kind}: {exc}")
        return
    sleep(PAUSE)
    title = page_title(raw)
    if not title or "prerelease" not in title.lower():
        report.problems.append(f"{row.set_code} {kind}: {url} answered but its title is not a prerelease page ({title!r})")
        return
    lines = page_lines(raw)
    if kind == "guide":
        row.player_guide_url, row.player_guide_title = url, title
        row.player_guide_status, row.player_guide_fetched_at = "verified", now
        contents = parse_pack_contents(lines)
        composition = dict(row.composition or {})
        if contents and (composition.get("traditional") or {}).get("status") != "verified":
            each, other = contents["play_boosters_in_each_pack"], contents["other_boosters_in_each_pack"]
            composition["traditional"] = {
                "unit": "player", "prerelease_packs": 1, "loose_play_boosters": 0, "play_boosters_in_each_pack": each,
                "other_boosters_in_each_pack": other,
                "summary": f"One Prerelease Pack per player; each pack holds {each} Play Boosters"
                           + (f" and {other} other booster{'s' if other != 1 else ''}" if other else "")
                           + " (the guide lists the rest).",
                "source_url": url, "fetched_at": now.isoformat(), "status": "verified"}
            row.composition = composition
    else:
        row.wpn_event_url, row.wpn_event_title = url, title
        row.wpn_event_status, row.wpn_event_fetched_at = "verified", now
        dates = parse_event_dates(lines, row.released_at)
        if dates and (row.dates_status != "verified" or (row.prerelease_start, row.prerelease_end) == dates):
            row.prerelease_start, row.prerelease_end = dates
            row.dates_status, row.dates_source_url, row.dates_fetched_at = "verified", url, now
        elif dates:
            row.source_change_note = (f"{now.date().isoformat()}: {url} gives {dates[0].isoformat()} to {dates[1].isoformat()}; the "
                                      f"verified dates {_iso(row.prerelease_start)} to {_iso(row.prerelease_end)} were kept.")
            report.changed.append({"set": row.set_code, "what": "dates", "to": f"{dates[0].isoformat()} to {dates[1].isoformat()}"})
        arrangement = parse_two_headed_giant(lines)
        composition = dict(row.composition or {})
        if arrangement and (composition.get("two_headed_giant") or {}).get("status") != "verified":
            packs, loose = arrangement["prerelease_packs"], arrangement["loose_play_boosters"]
            composition["two_headed_giant"] = {
                "unit": "team", "prerelease_packs": packs, "loose_play_boosters": loose,
                "summary": (f"{'One' if packs == 1 else 'Two'} Prerelease Pack{'s' if packs != 1 else ''} per team"
                            + (f" plus {loose} Play Boosters" if loose else "") + "."),
                "source_url": url, "fetched_at": now.isoformat(), "status": "verified"}
            row.composition = composition
    report.found.append({"set": row.set_code, "kind": kind, "url": url})
