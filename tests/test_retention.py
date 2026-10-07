"""Price history retention (vault.retention): daily for 90 days, weekly for 6 months, two a month
up to a year, nothing older; the value chart is capped at a year; and running it again changes nothing."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from tests.ids import sid
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
    db.add_all(PriceSnapshot(scryfall_id=sid(card), day=TODAY - timedelta(days=i), usd=1.0 + i / 100) for i in range(days))
    db.commit()


def kept(db, card="c1"):
    return sorted((TODAY - d).days for d in db.scalars(select(PriceSnapshot.day).where(PriceSnapshot.scryfall_id == sid(card))))


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


# -- months with missing days, and the exact tier boundaries (#63) -------------------------------------------------------------
# TODAY = 2027-01-15, so age 90 is 2026-10-17 (Saturday), age 272 is 2026-04-18 (Saturday) and age 365 is 2026-01-15.

def put(db, ages, card="b1"):
    db.add_all(PriceSnapshot(scryfall_id=sid(card), day=TODAY - timedelta(days=a), usd=1.0) for a in ages)
    db.commit()


def test_a_month_where_the_1st_and_the_15th_have_no_data_keeps_the_nearest_day_after_each(db):
    feb = lambda d: (TODAY - date(2026, 2, d)).days  # noqa: E731  (the age of 2026-02-d)
    mar = lambda d: (TODAY - date(2026, 3, d)).days  # noqa: E731
    put(db, [feb(d) for d in (3, 4, 5, 17, 18)] + [mar(d) for d in (10, 11)] + [mar(d) for d in (29, 30)])
    retention.prune_price_snapshots(db, TODAY)
    days = sorted(TODAY - timedelta(days=a) for a in kept(db, "b1"))
    # February: the 1st and the 15th have no data, so the 3rd and the 17th stand for them. March: the first half keeps the 10th,
    # the second half the 29th (the first day with data on or after the 15th).
    assert days == [date(2026, 2, 3), date(2026, 2, 17), date(2026, 3, 10), date(2026, 3, 29)]


def test_a_month_with_data_only_in_its_second_half_keeps_one_point_and_one_with_only_its_first_half_too(db):
    put(db, [(TODAY - date(2026, 2, d)).days for d in (20, 21, 28)] + [(TODAY - date(2026, 3, d)).days for d in (2, 3)])
    retention.prune_price_snapshots(db, TODAY)
    assert sorted(TODAY - timedelta(days=a) for a in kept(db, "b1")) == [date(2026, 2, 20), date(2026, 3, 2)]


def test_age_272_is_still_in_the_weekly_tier_and_age_273_in_the_twice_a_month_tier(db):
    # 2026-04-16 (age 274), -17 (273) and -18 (272) share an ISO week and a half-month. Weekly rule for 272: it is the week's
    # latest day, so it stays. Twice-a-month rule for 273 and 274: the half-month's first day (274) stays, 273 goes.
    # If the boundary were one day earlier, 272 would be judged as 'second row of the half-month' and go; one day later, 273 would be
    # judged weekly and go with 274 (not the week's latest).
    # And the other side of the line: with only 272 and 273, the first is the week's latest and the second the half-month's first,
    # so both stay; had 273 been weekly it would lose to 272 in the same week.
    put(db, [272, 273, 274])
    put(db, [272, 273], card="b2")
    retention.prune_price_snapshots(db, TODAY)
    assert kept(db, "b1") == [272, 274]
    assert kept(db, "b2") == [272, 273]


def test_age_90_is_the_last_daily_day_and_age_91_the_first_weekly_one(db):
    # 2026-10-17 (age 90), -16 (91) and -15 (92) share an ISO week. Day 90 is daily, so it stays whatever else is in its week;
    # of the weekly rows 91 and 92, the later (91) stays and 92 goes.
    put(db, [90, 91, 92])
    retention.prune_price_snapshots(db, TODAY)
    assert kept(db, "b1") == [90, 91]


def test_age_365_is_kept_and_age_366_is_not(db):
    put(db, [365, 366, 400])
    assert retention.prune_price_snapshots(db, TODAY) == 2
    assert kept(db, "b1") == [365]


# -- the charts after thinning (#63) ------------------------------------------------------------------------------------------

def test_the_value_chart_and_the_cards_price_history_still_work_after_a_year_is_thinned(signed_in, app):
    """The collection value chart reads ``collection_values`` (capped, not thinned) and a card's price history reads the last 90
    daily snapshots: after retention has thinned a full year of snapshots, the chart still has every day and the history is still
    90 consecutive daily points ending today, and today's value is computed from the latest snapshot as before."""
    from tests.test_api import BULK, V1, all_cards, upload
    from vault.prices import compute_values
    from vault.sync import sync

    today = date.today()
    upload(signed_in)
    with app.state.db.sessions() as db:
        sync(db, BULK, day=today)  # the four printings, today's prices (Sol Ring foil: 3.00)
        user = db.scalars(select(User)).first()
        db.add_all(PriceSnapshot(scryfall_id=sid("sol"), day=today - timedelta(days=i), usd=1.0 + i / 100, usd_foil=3.0 + i / 100)
                   for i in range(1, 400))
        db.add_all(CollectionValue(user_id=user.id, day=today - timedelta(days=i), market_usd=4.53 + i / 50, cost_usd=2.83, copies=7, priced_copies=7)
                   for i in range(1, 400))
        db.commit()
        deleted = retention.apply(db, today)
        remaining = db.scalar(select(func.count()).select_from(PriceSnapshot).where(PriceSnapshot.scryfall_id == sid("sol")))
        compute_values(db, today)
    assert deleted["prices_deleted"] > 250 and 115 <= remaining <= 128  # a year of Sol Ring is about 122 points now
    history = signed_in.get(f"{V1}/collection/history", params={"limit": 500}).json()["items"]
    days = [h["day"] for h in history]
    assert days == [(today - timedelta(days=i)).isoformat() for i in range(365, -1, -1)]  # every day of the year, oldest first
    assert history[-1]["market"] == 4.53 and history[-1]["copies"] == 7  # today's value from today's snapshot, as before thinning
    sol = next(c for c in all_cards(signed_in) if c["name"] == "Sol Ring")
    points = signed_in.get(sol["_links"]["self"]["href"]).json()["price_history"]
    assert [p["day"] for p in points] == [(today - timedelta(days=i)).isoformat() for i in range(89, -1, -1)]  # 90 daily points, no gap
    assert points[-1]["price"] == 3.0 and points[0]["price"] == 3.89  # foil price, today back to 89 days ago
