"""Price history retention (vault.retention): daily for 90 days, weekly for 6 months, two a month
up to a year, nothing older; the value chart is capped at a year; and running it again changes nothing."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from vault import retention
from vault.db import Database
from vault.models import CollectionValue, PriceSnapshot, User

TODAY = date(2027, 1, 15)


@pytest.fixture
def db(database_url):
    database = Database(database_url)
    with database.sessions() as session:
        yield session
    database.engine.dispose()


def fill(db, card="c1", days=400):
    db.add_all(PriceSnapshot(scryfall_id=card, day=TODAY - timedelta(days=i), usd=1.0 + i / 100) for i in range(days))
    db.commit()


def kept(db, card="c1"):
    return sorted((TODAY - d).days for d in db.scalars(select(PriceSnapshot.day).where(PriceSnapshot.scryfall_id == card)))


def test_the_last_90_days_are_kept_every_day(db):
    fill(db)
    retention.prune_price_snapshots(db, TODAY)
    ages = kept(db)
    assert ages[:91] == list(range(91))  # day 0 to day 90


def test_the_next_six_months_keep_one_point_a_week_the_latest_day_in_the_week(db):
    fill(db)
    retention.prune_price_snapshots(db, TODAY)
    weekly = [a for a in kept(db) if 90 < a <= 272]
    weeks = {(TODAY - timedelta(days=a)).isocalendar()[:2] for a in weekly}
    assert len(weekly) == len(weeks)  # at most one a week
    assert 24 <= len(weekly) <= 28
    for a in weekly:  # nothing later in that week survived in the daily window either way
        d = TODAY - timedelta(days=a)
        later_same_week = [x for x in kept(db) if (TODAY - timedelta(days=x)) > d and (TODAY - timedelta(days=x)).isocalendar()[:2] == d.isocalendar()[:2] and x > 90]
        assert later_same_week == []


def test_the_last_three_months_keep_two_points_a_month(db):
    fill(db)
    retention.prune_price_snapshots(db, TODAY)
    monthly = [TODAY - timedelta(days=a) for a in kept(db) if 272 < a <= 365]
    per_month: dict = {}
    for d in monthly:
        per_month.setdefault((d.year, d.month), []).append(d.day)
    assert per_month and all(len(days) <= 2 for days in per_month.values())
    for days in per_month.values():
        assert sum(1 for x in days if x < 15) <= 1 and sum(1 for x in days if x >= 15) <= 1


def test_nothing_older_than_a_year_is_kept(db):
    fill(db)
    retention.prune_price_snapshots(db, TODAY)
    assert max(kept(db)) <= 365


def test_a_second_run_changes_nothing(db):
    fill(db)
    assert retention.prune_price_snapshots(db, TODAY) > 0
    before = kept(db)
    assert retention.prune_price_snapshots(db, TODAY) == 0 and kept(db) == before


def test_each_card_is_thinned_on_its_own(db):
    fill(db, "c1", 120)
    fill(db, "c2", 120)
    retention.prune_price_snapshots(db, TODAY)
    assert kept(db, "c1") == kept(db, "c2") and len(kept(db, "c1")) < 120


def test_about_122_points_remain_of_a_full_year(db):
    fill(db)
    retention.prune_price_snapshots(db, TODAY)
    assert 115 <= len(kept(db)) <= 128


def test_the_value_chart_is_capped_at_a_year_but_not_thinned(db):
    user = User(name="Ann")
    db.add(user)
    db.flush()
    db.add_all(CollectionValue(user_id=user.id, day=TODAY - timedelta(days=i), market_usd=1, cost_usd=1, copies=1, priced_copies=1)
               for i in (0, 1, 100, 365, 366, 500))
    db.commit()
    assert retention.prune_collection_values(db, TODAY) == 2
    ages = sorted((TODAY - d).days for d in db.scalars(select(CollectionValue.day)))
    assert ages == [0, 1, 100, 365]
