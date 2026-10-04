"""Database size guard for Neon's free plan (1 GB of storage per project; writes fail when it is full).

    DATABASE_URL=postgres://... python -m jobs.db_budget

Every job calls :func:`check` first and last. It logs the database size and the largest tables.
At 70% of the limit it prints a GitHub Actions warning; at 85% a job that adds data (the catalog
load) stops with a clear message, so imports and sign-ins keep their headroom. The limit comes from
``NEON_STORAGE_LIMIT_MB`` (default 1024). Compute hours and network transfer are not visible from
inside the database: check them in the Neon console each month (README, Costs).
"""

from __future__ import annotations

import json
import os

from sqlalchemy import text
from sqlalchemy.orm import Session

DEFAULT_LIMIT_MB = 1024
WARN_AT = 0.70
STOP_AT = 0.85
TOP_TABLES = 8


def usage(db: Session) -> dict:
    size = db.scalar(text("SELECT pg_database_size(current_database())"))
    rows = db.execute(text(
        "SELECT relname, pg_total_relation_size(oid) FROM pg_class "
        "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace "
        "ORDER BY pg_total_relation_size(oid) DESC LIMIT :n"), {"n": TOP_TABLES}).all()
    return {"database_mb": round(size / 1048576, 1), "tables_mb": {name: round(b / 1048576, 1) for name, b in rows}}


def check(db: Session, *, refuse: bool = False, stage: str = "") -> dict:
    """Log usage; warn at 70%; with ``refuse`` stop (SystemExit) at 85%. Returns the report."""
    limit = float(os.environ.get("NEON_STORAGE_LIMIT_MB") or DEFAULT_LIMIT_MB)
    report = usage(db) | {"limit_mb": limit, "stage": stage}
    share = report["database_mb"] / limit
    report["used"] = f"{share:.0%}"
    print(json.dumps({"db_budget": report}))
    if share >= WARN_AT:
        print(f"::warning::The database uses {report['used']} of its {limit:.0f} MB free storage. "
              "Free space (see docs/catalog-design.md, Neon budget) before it fills: writes fail when it is full.")
    if refuse and share >= STOP_AT:
        raise SystemExit(f"db_budget: the database is {report['used']} full (limit {limit:.0f} MB, stop at {STOP_AT:.0%}); "
                         "not adding data. Free space or raise NEON_STORAGE_LIMIT_MB if the plan changed.")
    return report


def main() -> None:
    from vault.config import Settings
    from vault.db import Database

    db = Database(Settings().database_url)
    with db.sessions() as session:
        check(session)


if __name__ == "__main__":
    main()
