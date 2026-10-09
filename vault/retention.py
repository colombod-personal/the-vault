"""How long price history is kept (docs/catalog-design.md, Price history).

Nothing older than a year is kept, and what is kept gets sparser with age so the free database
does not fill up:

- the last 90 days: one point a day;
- the next 6 months (up to day 272): one point a week, the latest day with data in that week;
- the last 3 months (up to day 365): two points a month, the first day with data from the 1st to
  the 14th and from the 15th to the end of the month;
- older than 365 days: deleted.

The daily job runs this after writing the day's prices. It is idempotent: running it twice changes nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from .models import CollectionValue, ResetSnapshot, StagedUpload

DAILY_DAYS = 90
WEEKLY_UNTIL = DAILY_DAYS + 182  # six months of weekly points
MAX_DAYS = 365

THIN_PRICES = text("""
WITH ranked AS (
    SELECT scryfall_id, day,
           row_number() OVER (PARTITION BY scryfall_id, date_trunc('week', day) ORDER BY day DESC) AS week_rank,
           row_number() OVER (PARTITION BY scryfall_id, date_trunc('month', day), (extract(day FROM day) >= 15)
                              ORDER BY day ASC) AS half_month_rank
    FROM price_snapshots
    WHERE day < :daily_from
), drop AS (
    SELECT scryfall_id, day FROM ranked
    WHERE day < :max_from
       OR (day >= :weekly_from AND week_rank > 1)
       OR (day < :weekly_from AND half_month_rank > 1)
)
DELETE FROM price_snapshots p USING drop d WHERE p.scryfall_id = d.scryfall_id AND p.day = d.day
""")


def prune_price_snapshots(db: Session, today: date | None = None) -> int:
    """Thin and cap ``price_snapshots``. Returns the number of rows deleted."""
    today = today or date.today()
    result = db.execute(THIN_PRICES, {
        "daily_from": today - timedelta(days=DAILY_DAYS),   # rows from here on are kept daily
        "weekly_from": today - timedelta(days=WEEKLY_UNTIL),  # rows from here to daily_from: weekly
        "max_from": today - timedelta(days=MAX_DAYS),       # older than this: gone
    })
    return result.rowcount or 0


def prune_collection_values(db: Session, today: date | None = None) -> int:
    """Per-user value history is small (one row a day), so it is only capped at a year, not thinned."""
    today = today or date.today()
    return db.execute(delete(CollectionValue).where(CollectionValue.day < today - timedelta(days=MAX_DAYS))).rowcount or 0


def prune_staged_uploads(db: Session) -> int:
    """Files people uploaded for an assistant (vault.uploads) that nobody applied: a full collection with prices paid must not
    outlive its hour (#352). They are also dropped when someone starts another link, but only the daily job covers a quiet week."""
    return db.execute(delete(StagedUpload).where(StagedUpload.expires_at <= datetime.now(timezone.utc))).rowcount or 0


def prune_reset_snapshots(db: Session) -> int:
    """The undo of a collection reset (#129) lasts 7 days: the snapshot holds the copies the person removed, so it is deleted when
    its time is up, whether or not anyone asked. (It is also replaced by the next reset, and deleted when the undo is used.)"""
    return db.execute(delete(ResetSnapshot).where(ResetSnapshot.expires_at <= datetime.now(timezone.utc))).rowcount or 0


def apply(db: Session, today: date | None = None) -> dict:
    report = {"prices_deleted": prune_price_snapshots(db, today), "values_deleted": prune_collection_values(db, today),
              "uploads_deleted": prune_staged_uploads(db), "reset_snapshots_deleted": prune_reset_snapshots(db)}
    db.commit()
    return report
