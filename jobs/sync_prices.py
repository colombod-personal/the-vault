"""Daily job: download Scryfall's bulk card file and update prices for everyone.

    DATABASE_URL=postgres://... python -m jobs.sync_prices            # download + sync
    python -m jobs.sync_prices --file default-cards.jsonl.gz           # use a local file

Runs from GitHub Actions (.github/workflows/sync-prices.yml) once a day.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import httpx
from mtg_toolkits.scryfall import ScryfallClient

from jobs import db_budget
from vault import catalog_sync, outbound, retention
from vault.config import Settings
from vault.db import Database
from vault.sync import sync_from_file

USER_AGENT = "the-vault/0.1 (+https://github.com/colombod-personal/the-vault)"


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="use an already-downloaded default_cards .jsonl.gz file")
    args = parser.parse_args(argv)

    settings = Settings()
    db = Database(settings.database_url)
    db.migrate()
    with db.sessions() as session:
        db_budget.check(session, stage="before the price sync")  # warns only: prices keep syncing
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(args.file) if args.file else None
        if path is None:
            http = httpx.Client(transport=transport or outbound.transport(settings), timeout=120, follow_redirects=True)
            with ScryfallClient(user_agent=USER_AGENT, client=http) as sf:
                path = sf.download_bulk("default_cards", Path(tmp) / "default-cards.jsonl.gz")
        with db.sessions() as session:
            report = sync_from_file(session, path)
            # Cheapest price of every card, only once that source is enabled (docs/compliance.md).
            if "oracle_prices" in os.environ.get("CATALOG_SOURCES", "").split(","):
                report["oracle_prices"] = catalog_sync.sync_cheapest_from_file(session, path)
            report["retention"] = retention.apply(session)  # one year at most, thinned (docs/catalog-design.md)
            print(json.dumps(report, indent=2))
        with db.sessions() as session:
            db_budget.check(session, stage="after the price sync", fail_at_warn=True)  # fails the job at 70% (after its work)


if __name__ == "__main__":
    main()
