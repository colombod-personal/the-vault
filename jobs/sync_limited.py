"""Weekly job: per-card Limited statistics from 17Lands' public data sets (#178, docs/limited-data-design.md).

    DATABASE_URL=postgres://... CATALOG_SOURCES=limited_17lands python -m jobs.sync_limited
    python -m jobs.sync_limited --sources limited_17lands --sets HOB --formats PremierDraft,TradDraft --force

17Lands publishes one gzip CSV per set, format and kind (a row per game, a row per pick) under CC BY 4.0. This job reads each file
**once as a stream and never writes it to disk**: the rows go through ``vault.limited_stats`` (``GameCounter``, ``PickCounter``) and
only per-card counts are kept (``limited_game_stats``, ``limited_pick_stats``, ``limited_sources``). A file whose ETag and
Last-Modified are already stored is not read again. Nothing loads unless ``limited_17lands`` is named in ``--sources`` or the repository
variable ``CATALOG_SOURCES`` (docs/compliance.md "Source gate"); the real run is the GitHub Action ``sync-limited``, never a laptop.

**Which sets** (#417, docs/limited-data-design.md sections 4 and 14). With no ``--sets`` the job works out the rolling window itself: from
Scryfall's list of sets (expansion, core, masters and draft-innovation sets released in the last 30 months) it asks the 17Lands bucket with
HEAD about the upper-case code of each, newest first, and keeps, per format, the 8 newest sets that have a game file
(``jobs/limited_window.py``). A set is not read until 14 days after its ``released_at``. A stored set that is no longer in the window is
deleted, with its ``limited_sources`` rows, in the same run. ``--sets`` (a manual first fill or a re-read) names the sets instead: no
discovery, no wait, nothing deleted. **Formats** default to PremierDraft and TradDraft. The job refuses to run when the 17Lands terms were
last read more than 120 days ago (``jobs/limited_terms.py``, ``docs/compliance.md``).

The limits are ours, because 17Lands publishes none for the dumps: one download at a time, the descriptive User-Agent of the other jobs,
no file over 400 MB, at most 800 MB a run (the rest waits for the next), three retries only for connection errors, 429 and 5xx.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import time
import zlib
from datetime import date
from email.utils import parsedate_to_datetime

import httpx
from mtg_toolkits.scryfall import ApiError, ScryfallClient

from jobs import db_budget, limited_terms, limited_window, sync_catalog
from vault import limited_data, limited_stats, outbound
from vault.config import Settings
from vault.db import Database

HOST = "17lands-public.s3.amazonaws.com"
SOURCE = limited_data.SOURCE_NAME
USER_AGENT = sync_catalog.USER_AGENT
DEFAULT_FORMATS = ("PremierDraft", "TradDraft")  # the two formats with a game and a draft file for every recent set
MAX_FILE_BYTES = 400_000_000  # today's largest (FIN PremierDraft draft data) is 215.7 MB; a file far larger means a changed format
MAX_RUN_BYTES = 800_000_000  # downloaded per run; the rest waits for the next run
RETRY_WAITS = (2, 8, 30)  # seconds, only for connection errors, 429 and 5xx; Retry-After wins
MAX_RETRY_AFTER = 300
SET_CODE = re.compile(r"^[A-Z0-9]{2,6}$")


def file_url(kind: str, set_code: str, fmt: str) -> str:
    return f"https://{HOST}/analysis_data/{kind}_data/{kind}_data_public.{set_code}.{fmt}.csv.gz"


def sleep(seconds: float) -> None:  # a name tests replace
    time.sleep(seconds)


class FetchError(Exception):
    """A request that failed after its retries, or an answer that cannot be used."""


class CapReached(FetchError):
    """The run's byte budget is used up: the file waits for the next run (it is not a failure)."""


class Budget:
    """The bytes this run may still download (MAX_RUN_BYTES in all). Every byte a stream delivers is counted as it arrives, whether the
    read ends well, breaks, or is read again, so a reset connection or a file that grew after its HEAD cannot go over the cap."""

    def __init__(self, cap: int):
        self.cap, self.used = cap, 0

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.used)


def _metered(chunks, budget: Budget, limit: int, what: str):
    """The chunks of one stream, counted against the run's budget and against ``limit`` (the size HEAD advertised): reading stops at
    the first byte over either."""
    seen = 0
    for chunk in chunks:
        seen += len(chunk)
        budget.used += len(chunk)
        if seen > limit:
            raise FetchError(f"{what}: more than the {limit} bytes HEAD advertised; the file changed or the answer is not the file")
        if budget.used > budget.cap:
            raise CapReached(f"{what}: the run's {budget.cap}-byte cap was reached while reading")
        yield chunk


def _retrying(send, what: str) -> httpx.Response:
    """Call ``send()`` until it answers with something other than 429 or 5xx; at most ``len(RETRY_WAITS)`` retries."""
    for attempt in range(len(RETRY_WAITS) + 1):
        wait: float | None = None
        try:
            response = send()
        except httpx.TransportError as exc:
            problem = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code != 429 and response.status_code < 500:
                return response
            problem = f"HTTP {response.status_code}"
            try:
                wait = min(float(response.headers.get("retry-after", "")), MAX_RETRY_AFTER)
            except ValueError:
                wait = None
            response.close()
        if attempt == len(RETRY_WAITS):
            raise FetchError(f"{what}: {problem} after {len(RETRY_WAITS)} retries")
        sleep(wait if wait is not None else RETRY_WAITS[attempt])
    raise AssertionError("unreachable")


class _Chunks(io.RawIOBase):
    """A file-like over an iterator of byte chunks, so gzip and csv read the download as it arrives."""

    def __init__(self, chunks):
        self.chunks, self.rest, self.total = iter(chunks), b"", 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        while not self.rest:
            try:
                self.rest = next(self.chunks)
            except StopIteration:
                return 0
        n = min(len(buffer), len(self.rest))
        buffer[:n], self.rest = self.rest[:n], self.rest[n:]
        self.total += n
        return n


def reduce(chunks, kind: str, set_code: str) -> limited_stats.FileResult:
    """Read a gzip CSV stream and return its per-card counts. Raises FileError for a file that cannot be used; a truncated or corrupt
    stream fails here (gzip checks its length and CRC at the end), before anything is written."""
    raw = io.BufferedReader(_Chunks(chunks), 1 << 20)
    try:
        with gzip.GzipFile(fileobj=raw) as unzipped:
            reader = csv.reader(io.TextIOWrapper(unzipped, encoding="utf-8-sig", newline=""))
            header = next(reader, None)
            if not header:
                raise limited_stats.FileError("the file is empty")
            counter = (limited_stats.GameCounter if kind == "game" else limited_stats.PickCounter)(header, set_code)
            for row in reader:
                counter.feed(row)
            inflated = unzipped.tell()  # bytes after gunzip (the design's open question: nothing is kept, only counted)
    except (EOFError, gzip.BadGzipFile, zlib.error, UnicodeDecodeError, csv.Error) as exc:
        raise limited_stats.FileError(f"the download is truncated or corrupt ({type(exc).__name__}: {exc})") from exc
    result = counter.result()
    result.notes = [*header_facts(header), f"{inflated} bytes after gunzip", *result.notes]
    return result


CARD_PREFIXES = ("deck_", "sideboard_", "opening_hand_", "drawn_", "tutored_", "pack_card_", "pool_")


def header_facts(header: list[str]) -> list[str]:
    """What the run reports about a file's header: its width and the names of the columns that are not per-card (never a value of
    any row). The first real run (2026-10-09) did not print these; from now on every run does, so the next one settles the question."""
    plain = [c for c in header if not c.startswith(CARD_PREFIXES)]
    return [f"{len(header)} columns, {len(header) - len(plain)} of them per card", "other columns: " + ", ".join(plain[:80])]


def _modified(response: httpx.Response):
    try:
        return parsedate_to_datetime(response.headers["last-modified"])
    except (KeyError, TypeError, ValueError):
        return None


def discover(client: httpx.Client, db, scryfall_sets: list[dict], formats: list[str], today: date) -> dict:
    """The rolling window (design sections 4 and 14): per format, the ``limited_window.WINDOW_SETS`` newest recent sets whose game
    file exists. Scryfall's sets are asked about newest first, each with one HEAD per format, and the walk stops at the eighth; 403
    means 17Lands has not published the file (yet), so the set is passed over. A set already stored whose game file is 403 now (a
    withdrawn file) keeps its place and its rows, as in ``plan``. Returns ``{"window", "embargoed", "problems"}``; a HEAD that
    failed is a problem (the window is then not certain, so nothing is deleted on its strength)."""
    found, embargoed = limited_window.candidates(scryfall_sets, today)
    window: dict[str, list[str]] = {fmt: [] for fmt in formats}
    problems: list[str] = []
    for fmt in formats:
        for candidate in found:
            if len(window[fmt]) >= limited_window.WINDOW_SETS:
                break
            url = file_url("game", candidate.code, fmt)
            try:
                head = _retrying(lambda url=url: client.head(url), f"HEAD {url}")
            except FetchError as exc:
                problems.append(f"{candidate.code} {fmt}: {exc}")
                continue
            if head.status_code == 200:
                window[fmt].append(candidate.code)
            elif head.status_code in (403, 404):
                with db.sessions() as session:
                    if limited_data.previous(session, candidate.code, fmt, "game"):
                        window[fmt].append(candidate.code)
            else:
                problems.append(f"{candidate.code} {fmt}: HEAD answered {head.status_code}")
    return {"window": window, "embargoed": {code: day.isoformat() for code, day in sorted(embargoed.items())}, "problems": problems}


def remove_left(db, leaving: list[tuple[str, str]]) -> list[dict]:
    """Delete the sets that left the window, with their ``limited_sources`` rows, in one transaction."""
    removed = []
    if leaving:
        with db.sessions() as session:
            for set_code, fmt in leaving:
                removed.append({"set": set_code, "format": fmt, **limited_data.delete_set(session, set_code, fmt)})
            limited_data.record_catalog_source(session)
            session.commit()
    return removed


def scryfall_sets(settings, transport: httpx.BaseTransport | None) -> list[dict]:
    """Scryfall's list of sets (one request) through the same rate-limited client as the other jobs."""
    http = httpx.Client(transport=transport or outbound.transport(settings), timeout=httpx.Timeout(60), follow_redirects=True)
    try:
        with ScryfallClient(user_agent=USER_AGENT, client=http) as sf:
            return sf.sets()
    except (ApiError, httpx.HTTPError) as exc:
        raise SystemExit(f"sync_limited: Scryfall's list of sets could not be read ({type(exc).__name__}: {exc}); no set was chosen") from exc
    finally:
        http.close()


def plan(client: httpx.Client, db, pairs: list[tuple[str, str]]) -> list[dict]:
    """HEAD every file asked for (a game and a draft file per ``(set, format)``): what is published, how big, what version. Smallest
    first (the order they are read in)."""
    items = []
    for set_code, fmt in pairs:
        for kind in ("game", "draft"):
            url = file_url(kind, set_code, fmt)
            item = {"set": set_code, "format": fmt, "kind": kind, "url": url}
            try:
                head = _retrying(lambda url=url: client.head(url), f"HEAD {url}")
            except FetchError as exc:
                item |= {"status": "failed", "reason": str(exc)}
                items.append(item)
                continue
            if head.status_code in (403, 404):  # S3 answers 403 for a file that is not there
                with db.sessions() as session:
                    stored = limited_data.previous(session, set_code, fmt, kind)
                item |= {"status": "no_longer_published" if stored else "not_published",
                         "note": "the stored rows are kept" if stored else "17Lands has not published this file"}
            elif head.status_code != 200:
                item |= {"status": "failed", "reason": f"HEAD answered {head.status_code}"}
            else:
                try:
                    length = int(head.headers["content-length"])
                except (KeyError, ValueError):
                    length = None
                item |= {"status": "found", "length": length, "etag": head.headers.get("etag"), "modified": _modified(head)}
            items.append(item)
    return sorted(items, key=lambda i: (i.get("length") or 0, i["set"], i["format"], i["kind"]))


def read_file(client: httpx.Client, item: dict, budget: Budget) -> tuple[limited_stats.FileResult, dict]:
    """GET one file and reduce it while it streams. Returns the counts and the headers of the answer actually read. A connection
    that breaks while the file streams is a connection error like any other: the file is read again from its start (the counters
    are new each time, so nothing is counted twice), after the same waits; a corrupt or truncated file is not retried."""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return _read_once(client, item, budget)
        except httpx.TransportError as exc:
            if attempt == len(RETRY_WAITS):
                raise FetchError(f"GET {item['url']}: {type(exc).__name__}: {exc} after {len(RETRY_WAITS)} retries") from exc
            sleep(RETRY_WAITS[attempt])
    raise AssertionError("unreachable")


def _read_once(client: httpx.Client, item: dict, budget: Budget) -> tuple[limited_stats.FileResult, dict]:
    request = client.build_request("GET", item["url"], headers={"Accept-Encoding": "identity"})
    response = _retrying(lambda: client.send(request, stream=True), f"GET {item['url']}")
    try:
        if response.status_code != 200:
            raise FetchError(f"GET answered {response.status_code}")
        try:
            advertised = int(response.headers["content-length"])
        except (KeyError, ValueError):
            advertised = None
        if advertised is not None and advertised > item["length"]:
            raise FetchError(f"GET {item['url']}: Content-Length {advertised} is larger than the {item['length']} bytes HEAD advertised")
        # a real answer streams its raw (still gzipped) bytes; a twin's answer is already in memory
        chunks = response.iter_bytes() if response.is_stream_consumed else response.iter_raw()  # no re-chunking: every delivered byte is metered
        result = reduce(_metered(chunks, budget, item["length"], f"GET {item['url']}"), item["kind"], item["set"])
        return result, {"etag": response.headers.get("etag"), "modified": _modified(response),
                        "length": int(response.headers["content-length"]) if "content-length" in response.headers else None}
    finally:
        response.close()


def run(client: httpx.Client, db, pairs: list[tuple[str, str]], *, force: bool = False) -> list[dict]:
    items = plan(client, db, pairs)
    budget = Budget(MAX_RUN_BYTES)
    index = None
    for item in items:
        if item["status"] != "found":
            continue
        label = f"{item['set']} {item['format']} {item['kind']}"
        with db.sessions() as session:
            stored = limited_data.previous(session, item["set"], item["format"], item["kind"])
        if stored and not force and stored["etag"] == item["etag"] and stored["last_modified"] == item["modified"]:
            item |= {"status": "skipped", "note": "this version is already loaded"}
            continue
        if item["length"] is None or item["length"] > MAX_FILE_BYTES:
            item |= {"status": "failed", "reason": f"refused: Content-Length {item['length']} is not a size seen before (limit {MAX_FILE_BYTES} bytes)"}
            continue
        if item["length"] > budget.remaining:
            item |= {"status": "deferred", "note": f"{budget.used} of {MAX_RUN_BYTES} bytes were read this run; the next run takes {label}"}
            continue
        print(f"reading {label} ({item['length']} bytes)", flush=True)
        started = time.monotonic()
        try:
            result, got = read_file(client, item, budget)
            always, unless = limited_stats.sanity_problems(result, stored)
            if always or (unless and not force):
                raise limited_stats.FileError("refused: " + "; ".join(always + ([] if force else unless)) + ("" if always else " (--force overrides)"))
        except CapReached as exc:
            item |= {"status": "deferred", "note": f"{exc}; the next run takes {label}"}
            continue
        except (limited_stats.FileError, FetchError) as exc:
            item |= {"status": "failed", "reason": str(exc)}
            continue
        with db.sessions() as session:
            if index is None:
                index = limited_data.catalog_index(session)
            written = limited_data.replace_file(session, item["set"], item["format"], result, etag=got["etag"] or item["etag"],
                                                last_modified=got["modified"] or item["modified"], content_length=got["length"] or item["length"],
                                                index=index)
            session.commit()
        item |= {"status": "loaded", **written, "seconds": round(time.monotonic() - started, 1), "notes": result.notes}
    for item in items:
        item.pop("modified", None)
    return items


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sources", default=os.environ.get("CATALOG_SOURCES", ""),
                        help=f"comma-separated (default: $CATALOG_SOURCES, else none); this job loads {SOURCE}")
    parser.add_argument("--sets", default=os.environ.get("LIMITED_SETS", ""),
                        help="17Lands set codes, comma-separated, to read exactly (no discovery, no wait, nothing deleted); "
                             "default: $LIMITED_SETS, else the rolling window of the 8 newest sets with a game file")
    parser.add_argument("--formats", default=os.environ.get("LIMITED_FORMATS", ""),
                        help=f"from {', '.join(limited_stats.FORMATS)} (default: $LIMITED_FORMATS, else {','.join(DEFAULT_FORMATS)})")
    parser.add_argument("--force", action="store_true", help="read the files again even when this version is already loaded")
    args = parser.parse_args(argv)

    # CATALOG_SOURCES is shared with the other jobs: their names are skipped here, names nobody knows are refused.
    named = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in named if s not in (*sync_catalog.SOURCES, *sync_catalog.OTHER_JOBS)]
    if unknown:
        parser.error(f"unknown source(s): {', '.join(unknown)}")
    if SOURCE not in named:
        print(json.dumps({"enabled": [], "note": f"{SOURCE} is not enabled; see docs/compliance.md"}))
        return {}
    named_sets = bool(args.sets.strip())
    sets = [s.strip().upper() for s in args.sets.split(",") if s.strip()]
    formats = [s.strip() for s in (args.formats or ",".join(DEFAULT_FORMATS)).split(",") if s.strip()]
    if named_sets and (not sets or [s for s in sets if not SET_CODE.match(s)]):
        parser.error("--sets takes 17Lands set codes such as HOB (letters and digits)")
    if not formats or [f for f in formats if f not in limited_stats.FORMATS]:
        parser.error(f"--formats takes {', '.join(limited_stats.FORMATS)}")
    try:  # before anything is asked of anyone: the terms must have been read in the last 120 days (jobs/limited_terms.py)
        terms_age = limited_terms.check(now=limited_terms.today())
    except (limited_terms.TermsStale, OSError) as exc:
        raise SystemExit(f"sync_limited: refusing to run: {exc}") from exc

    settings = Settings()
    db = Database(settings.database_url)
    db.migrate()
    with db.sessions() as session:
        db_budget.check(session, refuse=True, stage="before the Limited load")
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    found: dict = {"window": {}, "embargoed": {}, "problems": []}
    removed: list[dict] = []
    with httpx.Client(transport=transport or outbound.transport(settings), headers=headers, timeout=httpx.Timeout(60, read=300),
                      follow_redirects=False) as client:
        if named_sets:
            pairs = [(code, fmt) for code in sets for fmt in formats]
        else:
            found = discover(client, db, scryfall_sets(settings, transport), formats, limited_terms.today())
            pairs = [(code, fmt) for fmt in formats for code in found["window"][fmt]]
            if not found["problems"]:  # a window that could not be fully worked out is no reason to delete anything
                with db.sessions() as session:
                    stored = limited_data.stored_pairs(session)
                removed = remove_left(db, limited_window.leaving(
                    stored, found["window"], {c: date.fromisoformat(d) for c, d in found["embargoed"].items()}, formats))
        items = run(client, db, pairs, force=args.force)
    report = {"sets": sorted({code for code, _ in pairs}), "formats": formats, "terms_read_days_ago": terms_age,
              "files": [{k: v for k, v in i.items() if k not in ("url", "etag")} for i in items]}
    if not named_sets:
        report |= {"window": found["window"], "waiting_after_release": found["embargoed"], "removed": removed,
                   "discovery_problems": found["problems"]}
    print(json.dumps(report, indent=2, default=str))  # before the last check, which fails the job at 70% (after its work)
    with db.sessions() as session:
        db_budget.check(session, stage="after the Limited load", fail_at_warn=True)
    failed = [i for i in items if i["status"] == "failed"]
    if failed or found["problems"]:
        raise SystemExit("sync_limited: " + "; ".join(
            ([f"{len(failed)} file(s) failed: " + "; ".join(f"{i['set']} {i['format']} {i['kind']}: {i['reason']}" for i in failed)] if failed else [])
            + ([f"the window could not be fully worked out, nothing was deleted: {'; '.join(found['problems'])}"] if found["problems"] else [])))
    return report


if __name__ == "__main__":
    main()
