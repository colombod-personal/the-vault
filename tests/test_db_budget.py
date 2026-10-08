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
    # #307: the size of a real database moves by a page between two reads, so the size is fixed here and the percentage is exact
    monkeypatch.setattr(db_budget, "usage", lambda _db: {"database_mb": 10.0, "tables_mb": {}})
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", "30")
    report = db_budget.check(db, refuse=True, stage="test")
    out = capsys.readouterr().out
    assert report["used"] == "33%" and "::warning::" not in out
    assert json.loads(out.splitlines()[0])["db_budget"]["stage"] == "test"


def test_a_database_size_that_moves_between_reads_does_not_change_the_band(db, monkeypatch, capsys):
    """#307: the same real database measured twice may differ by 0.1 MB; one third of the limit stays well under 70%."""
    real = db_budget.usage(db)["database_mb"]
    for size in (real, real + 0.1, real - 0.1):
        monkeypatch.setattr(db_budget, "usage", lambda _db, size=size: {"database_mb": size, "tables_mb": {}})
        monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", str(real * 3))
        db_budget.check(db, refuse=True)
    assert "::warning::" not in capsys.readouterr().out


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


# -- failing the job at 70% (#64) --------------------------------------------------------------------------------------------

def test_at_70_percent_the_last_check_fails_the_job_with_a_clear_message_and_hands_it_to_the_workflow(db, monkeypatch, tmp_path, capsys):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", str(used_mb(db) / 0.75))
    with pytest.raises(SystemExit) as stop:
        db_budget.check(db, stage="after the price sync", fail_at_warn=True)
    message = str(stop.value)
    assert "75% of the" in message and "Failing the job on purpose" in message and "GitHub issue" in message and "docs/catalog-design.md" in message
    assert "::warning::" in capsys.readouterr().out
    line = out.read_text(encoding="utf-8").strip()
    assert line.startswith("storage_alert=Neon storage is at 75% of the") and "\n" not in line  # one line: the alert job reads it as an input


def test_below_70_percent_the_last_check_passes_even_when_asked_to_fail(db, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", str(used_mb(db) * 2))
    assert db_budget.check(db, fail_at_warn=True)["used"] == "50%"
    assert not (tmp_path / "out").exists()


def test_the_price_job_does_its_work_first_and_fails_at_the_end(database_url, monkeypatch, tmp_path):
    """Failing before the work would stop the retention that frees space: the prices are stored, the thinning runs, then the job
    ends red with the message and the workflow output."""
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from jobs import sync_prices
    from tests.test_twins import CSV
    from twins import Universe
    from vault.app import create_app
    from vault.config import Settings
    from vault.models import PriceSnapshot

    universe = Universe()
    settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True)
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as client:
        client.post("/api/auth/dev-login")
        client.post("/api/v1/imports", files={"file": ("e.csv", CSV, "text/csv")})
    database = Database(database_url)
    with database.sessions() as session:
        monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", str(used_mb(session) / 0.75))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    with pytest.raises(SystemExit, match="Failing the job on purpose"):
        sync_prices.main([], transport=universe.transport)
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(PriceSnapshot)) > 0  # the work was done
    assert (tmp_path / "out").read_text(encoding="utf-8").startswith("storage_alert=")
    database.engine.dispose()
