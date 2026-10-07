"""Put the stores' reviewer demo account back as it was (issue #239).

    DATABASE_URL=postgres://... python -m jobs.seed_reviewer            # create it if missing
    DATABASE_URL=postgres://... python -m jobs.seed_reviewer --reset    # delete its decks and collection, then recreate
    DATABASE_URL=postgres://... python -m jobs.seed_reviewer --csv my_cards.csv   # reset, then load your own Dragon Shield export

The demo account only holds made-up data (vault/reviewer.py), except with --csv: that loads a real collection into a private
copy of the Vault (a local database). Never point --csv at the production database: store reviewers sign in to that account. The first reviewer sign-in creates it too; the daily price
sync (jobs/sync_prices.py) then matches its cards to printings, like any import.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from vault import reviewer
from vault.config import Settings
from vault.db import Database


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete the demo decks and collection first")
    parser.add_argument("--csv", type=Path, help="a Dragon Shield export to load instead of the made-up collection (implies --reset)")
    args = parser.parse_args(argv)
    db = Database(Settings().database_url)
    db.migrate()
    with db.sessions() as session:
        user = reviewer.seed(session, reset=args.reset or bool(args.csv), collection=args.csv.read_bytes() if args.csv else None)
        print(f"demo account {reviewer.EMAIL} is ready (user {user.id})")


if __name__ == "__main__":
    main()
