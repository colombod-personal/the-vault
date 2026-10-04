"""The Neon storage guard (jobs/db_budget.py): logs usage, warns at 70%, refuses at 85%."""

import json

import pytest

from jobs import db_budget, sync_catalog
from vault.db import Database


@pytest.fixture
def db(database_url):
    database = Database(database_url)
    with database.sessions() as session:
        yield session
    database.engine.dispose()


def used_mb(db):
    return db_budget.usage(db)["database_mb"]


def test_usage_names_the_database_size_and_the_largest_tables(db):
    report = db_budget.usage(db)
    assert report["database_mb"] > 0 and 0 < len(report["tables_mb"]) <= db_budget.TOP_TABLES


def test_below_70_percent_it_only_logs(db, monkeypatch, capsys):
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", str(used_mb(db) * 3))
    report = db_budget.check(db, refuse=True, stage="test")
    out = capsys.readouterr().out
    assert report["used"] == "33%" and "::warning::" not in out
    assert json.loads(out.splitlines()[0])["db_budget"]["stage"] == "test"


def test_at_70_percent_it_warns_but_does_not_stop(db, monkeypatch, capsys):
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", str(used_mb(db) / 0.75))
    db_budget.check(db, refuse=True)
    assert "::warning::" in capsys.readouterr().out


def test_at_85_percent_a_job_that_adds_data_stops_but_one_that_does_not_carries_on(db, monkeypatch, capsys):
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", str(used_mb(db) / 0.9))
    db_budget.check(db, refuse=False)  # the price sync: warns, keeps syncing
    with pytest.raises(SystemExit, match="not adding data"):
        db_budget.check(db, refuse=True)


def test_the_catalog_job_refuses_before_downloading_anything_when_the_database_is_full(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", "1")
    with pytest.raises(SystemExit, match="not adding data"):
        sync_catalog.main(["--sources", "rulings"], transport=None)
