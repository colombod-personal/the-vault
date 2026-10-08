"""The Archidekt cache counts what it asks and what it saves (#133): calls to Archidekt and cache hits in the last minute and the
hit rate, in the log line of each read, with counts only (no deck, no person)."""

from datetime import datetime, timezone

import pytest

from vault import archidekt_cache as ac
from vault.db import Database


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_rate_counts_the_last_minute_only_and_gives_the_hit_rate():
    clock = Clock()
    rate = ac.Rate(clock)
    assert rate.note("miss") == {"calls_last_minute": 1, "hits_last_minute": 0, "hit_rate": 0.0}
    clock.now += 10
    for _ in range(3):
        counts = rate.note("hit")
    assert counts == {"calls_last_minute": 1, "hits_last_minute": 3, "hit_rate": 0.75}
    clock.now += 55  # the miss, 65 seconds old, drops out; the hits are 55 seconds old
    assert rate.note("hit") == {"calls_last_minute": 0, "hits_last_minute": 4, "hit_rate": 1.0}
    clock.now += 61
    assert rate.note("miss") == {"calls_last_minute": 1, "hits_last_minute": 0, "hit_rate": 0.0}


@pytest.fixture
def db(database_url):
    database = Database(database_url)
    with database.sessions() as session:
        yield session
    database.engine.dispose()


def test_each_read_logs_the_counts_and_nothing_that_names_a_deck_or_a_person(db, monkeypatch, caplog):
    clock = Clock()
    monkeypatch.setattr(ac, "RATE", ac.Rate(clock))
    now = datetime.now(timezone.utc)
    fetched = []

    def fetch(deck_id):
        fetched.append(deck_id)
        return {"name": "Secret deck name", "cards": []}

    with caplog.at_level("INFO", logger="vault.archidekt_cache"):
        ac.read(db, 424242, fetch, now=now)  # miss: one call to Archidekt
        clock.now += 1
        ac.read(db, 424242, fetch, now=now)  # hit
        ac.read(db, 424242, fetch, now=now)  # hit
    lines = [r.getMessage() for r in caplog.records if "archidekt deck cache" in r.getMessage()]
    assert fetched == [424242]
    assert lines == ["archidekt deck cache=miss calls_last_minute=1 hits_last_minute=0 hit_rate=0.0",
                     "archidekt deck cache=hit calls_last_minute=1 hits_last_minute=1 hit_rate=0.5",
                     "archidekt deck cache=hit calls_last_minute=1 hits_last_minute=2 hit_rate=0.67"]
    assert not any("424242" in line or "Secret" in line for line in lines)  # counts only
