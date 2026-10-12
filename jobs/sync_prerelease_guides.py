"""Daily job: find Wizards' prerelease guide and event page for each upcoming set (vault-064.10.8, docs/prerelease-guides.md).

    DATABASE_URL=postgres://... CATALOG_SOURCES=prerelease_guides python -m jobs.sync_prerelease_guides
    python -m jobs.sync_prerelease_guides --sources prerelease_guides --today 2026-10-12

It runs as a step of the daily ``sync-catalog`` workflow, after the catalog load. From Scryfall's list of sets (one request) it makes
sure every upcoming expansion or core set has a row in ``set_prerelease_guides`` the day Scryfall lists it (dates worked out from the
release date, marked unverified). For each set whose prerelease is within 45 days and whose pages are not yet verified it reads the
two sitemaps Wizards publishes for machines (magic.wizards.com and wpn.wizards.com, one request each), matches the set to a page,
and checks each page it found with one request. It keeps links and a few facts, never page text. A set with nothing found gets a row
that says so. A verified fact is never overwritten: a different one is written to ``source_change_note``.

Nothing runs unless ``prerelease_guides`` is named in ``--sources`` or the repository variable ``CATALOG_SOURCES``
(docs/compliance.md "Source gate": the Wizards General Terms, clause 2.2, are read and recorded there; the switch is the owner's).
The only hosts contacted are scryfall.com's API, magic.wizards.com and wpn.wizards.com. The manual seed (``vault/prerelease_seed.py``)
is applied first: it adds the rows that are missing and touches none that exist.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone

import httpx
from mtg_toolkits.scryfall import ApiError, ScryfallClient

from jobs import db_budget, sync_catalog
from vault import catalog_sync, outbound, prerelease_guides, prerelease_seed
from vault.config import Settings
from vault.db import Database
from vault.models import SetPrereleaseGuide

SOURCE = prerelease_guides.SOURCE_NAME
USER_AGENT = sync_catalog.USER_AGENT


def scryfall_sets(settings, transport: httpx.BaseTransport | None) -> list[dict]:
    """Scryfall's list of sets (one request) through the same client as the other jobs."""
    http = httpx.Client(transport=transport or outbound.transport(settings), timeout=httpx.Timeout(60), follow_redirects=True)
    try:
        with ScryfallClient(user_agent=USER_AGENT, client=http) as sf:
            return sf.sets()
    except (ApiError, httpx.HTTPError) as exc:
        raise SystemExit(f"sync_prerelease_guides: Scryfall's list of sets could not be read ({type(exc).__name__}: {exc}); "
                         "nothing was looked for") from exc
    finally:
        http.close()


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sources", default=os.environ.get("CATALOG_SOURCES", ""),
                        help=f"comma-separated (default: $CATALOG_SOURCES, else none); this job loads {SOURCE}")
    parser.add_argument("--today", default="", help="YYYY-MM-DD, for a dry run on another day (default: today, UTC)")
    args = parser.parse_args(argv)

    named = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in named if s not in (*sync_catalog.SOURCES, *sync_catalog.OTHER_JOBS)]
    if unknown:
        parser.error(f"unknown source(s): {', '.join(unknown)}")
    if SOURCE not in named:
        print(json.dumps({"enabled": [], "note": f"{SOURCE} is not enabled; see docs/compliance.md"}))
        return {}
    try:
        day = date.fromisoformat(args.today) if args.today else prerelease_guides.today()
    except ValueError:
        parser.error("--today takes a date such as 2026-10-12")

    settings = Settings()
    db = Database(settings.database_url)
    db.migrate()
    with db.sessions() as session:
        db_budget.check(session, refuse=True, stage="before the prerelease guide run")
    sets = prerelease_guides.upcoming_sets(scryfall_sets(settings, transport), day)
    now = datetime.now(timezone.utc)
    with db.sessions() as session:
        seeded = prerelease_seed.apply_seed(session)
        with httpx.Client(transport=transport or outbound.transport(settings), timeout=httpx.Timeout(30, connect=10)) as client:
            report = prerelease_guides.discover(session, client, sets, day, now=now)
        rows = session.query(SetPrereleaseGuide).count()
        verified = session.query(SetPrereleaseGuide).filter(SetPrereleaseGuide.status == "verified").count()
        catalog_sync.record_source(session, SOURCE, version=f"{rows} sets, {verified} verified, looked {day.isoformat()}"[:60], rows=rows,
                                   url=prerelease_guides.MAGIC_SITEMAP)
        session.commit()
    out = {"day": day.isoformat(), "seed_rows_added": seeded, "upcoming_sets": [s["code"] for s in sets], "rows": rows,
           "verified_rows": verified, **report.as_dict()}
    print(json.dumps(out, indent=2, default=str))
    for item in report.needs_a_person:  # shows on the run's summary page: Wizards has published no guide near the prerelease
        print(f"::warning::{item['name']} ({item['set'].upper()}): prerelease starts {item['prerelease_start']} and no player guide is "
              "verified yet. The table says so to anyone who asks; a person can look at magic.wizards.com/en/play-events/prerelease.")
    for problem in report.problems:
        print(f"::warning::sync_prerelease_guides: {problem}")
    if report.problems:  # after the work and the report: a sitemap that could not be read or a page that did not fit must be noticed
        raise SystemExit("sync_prerelease_guides: " + "; ".join(report.problems))
    return out


if __name__ == "__main__":
    main()
