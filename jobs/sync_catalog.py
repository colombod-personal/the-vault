"""Daily job: load the catalog (oracle cards, rulings, oracle tags) from Scryfall's bulk files.

    DATABASE_URL=postgres://... CATALOG_SOURCES=oracle_cards,rulings,oracle_tags python -m jobs.sync_catalog
    python -m jobs.sync_catalog --sources oracle_cards --file oracle_cards=oracle-cards.jsonl.gz

Nothing is loaded unless a source is named (``--sources`` or ``CATALOG_SOURCES``): each source is
enabled only after its terms have been checked (docs/compliance.md). A source whose bulk file version
is already loaded is skipped. Cheapest prices come from ``jobs/sync_prices.py``, which already
downloads the default-cards file.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import httpx
from mtg_toolkits.scryfall import ScryfallClient, iter_bulk_file

from vault import catalog_sync as cs
from vault import outbound
from vault.config import Settings
from vault.db import Database

USER_AGENT = "the-vault/0.1 (+https://github.com/colombod-personal/the-vault)"
SOURCES = ("oracle_cards", "rulings", "oracle_tags")


def bulk_version(entry: dict) -> str:
    """The stamp in the file name (``oracle-cards-20261003210155``), which changes when Scryfall rebuilds it."""
    uri = entry.get("jsonl_download_uri") or entry["download_uri"]
    name = uri.rsplit("/", 1)[-1]
    return name.split(".")[0]


def load(db, name: str, path: Path, version: str, entry: dict | None) -> dict:
    objects = iter_bulk_file(path)
    if name == "oracle_cards":
        result = cs.sync_oracle_cards(db, objects)
        rows = result["cards"]
    elif name == "rulings":
        result = cs.sync_rulings(db, objects)
        rows = result["rulings"]
    else:
        result = cs.sync_oracle_tags(db, objects)
        rows = result["links"]
    from datetime import datetime
    updated = datetime.fromisoformat(entry["updated_at"]) if entry and entry.get("updated_at") else None
    cs.record_source(db, name, version=version, rows=rows, source_updated_at=updated,
                     url=(entry or {}).get("uri") or None)
    db.commit()
    return result


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sources", default=os.environ.get("CATALOG_SOURCES", ""),
                        help=f"comma-separated, from {', '.join(SOURCES)} (default: $CATALOG_SOURCES, else none)")
    parser.add_argument("--file", action="append", default=[], metavar="SOURCE=PATH",
                        help="use an already-downloaded .jsonl.gz file for a source instead of downloading it")
    parser.add_argument("--force", action="store_true", help="load even when this file version is already loaded")
    args = parser.parse_args(argv)

    wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in SOURCES]
    if unknown:
        parser.error(f"unknown source(s): {', '.join(unknown)}")
    if not wanted:
        print(json.dumps({"enabled": [], "note": "no catalog source is enabled; see docs/compliance.md"}))
        return {}
    files = dict(item.split("=", 1) for item in args.file)

    settings = Settings()
    db = Database(settings.database_url)
    db.migrate()
    report: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        http = httpx.Client(transport=transport or outbound.transport(settings), timeout=120, follow_redirects=True)
        with ScryfallClient(user_agent=USER_AGENT, client=http) as sf:
            entries = {} if files and set(wanted) <= set(files) else {b["type"]: b for b in sf.bulk_data()}
            for name in wanted:
                entry = entries.get(name)
                version = bulk_version(entry) if entry else Path(files[name]).name.split(".")[0]
                with db.sessions() as session:
                    if not args.force and cs.source_is_current(session, name, version):
                        report[name] = {"skipped": f"{version} is already loaded"}
                        continue
                    path = Path(files[name]) if name in files else sf.download_bulk(name, Path(tmp) / f"{name}.jsonl.gz")
                    report[name] = {"version": version, **load(session, name, path, version, entry)}
    print(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    main()
