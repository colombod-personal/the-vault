"""Daily job: download Scryfall's bulk card file and update prices for everyone.

    DATABASE_URL=postgres://... python -m jobs.sync_prices            # download + sync
    python -m jobs.sync_prices --file default-cards.jsonl.gz           # use a local file

Runs from GitHub Actions (.github/workflows/sync-prices.yml) once a day.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from mtg_toolkits.scryfall import ScryfallClient

from vault.config import Settings
from vault.db import Database
from vault.sync import sync_from_file

USER_AGENT = "the-vault/0.1 (+https://github.com/colombod-personal/the-vault)"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="use an already-downloaded default_cards .jsonl.gz file")
    args = parser.parse_args(argv)

    db = Database(Settings().database_url)
    db.create_all()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(args.file) if args.file else None
        if path is None:
            with ScryfallClient(user_agent=USER_AGENT) as sf:
                path = sf.download_bulk("default_cards", Path(tmp) / "default-cards.jsonl.gz")
        with db.sessions() as session:
            print(json.dumps(sync_from_file(session, path), indent=2))


if __name__ == "__main__":
    main()
