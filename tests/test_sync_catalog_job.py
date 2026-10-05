"""The daily catalog job (jobs/sync_catalog.py) through the Scryfall twin: bulk-data listing, the
three .jsonl.gz files, loaded as diffs, skipped when the file version is already loaded, and
nothing loaded at all unless a source is enabled (docs/compliance.md)."""

import pytest
from sqlalchemy import func, select

from jobs import sync_catalog
from twins import Universe
from vault.db import Database
from vault.models import CatalogSource, OracleCard, OracleTagLink, Ruling


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


def counts(url):
    db = Database(url)
    with db.sessions() as s:
        out = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in (OracleCard, Ruling, OracleTagLink, CatalogSource)}
    db.engine.dispose()
    return out


def seed(universe):
    sol = universe.scryfall.find("Sol Ring")
    universe.scryfall.add_ruling(sol["oracle_id"], "Sol Ring's ability is a mana ability.")
    universe.scryfall.add_tag("mana-rock", cards={sol["oracle_id"]: "strong"})
    return sol


def test_nothing_is_loaded_unless_a_source_is_enabled(database_url, monkeypatch, universe):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("CATALOG_SOURCES", raising=False)
    assert sync_catalog.main([], transport=universe.transport) == {}
    assert universe.scryfall.calls == []
    assert counts(database_url)["oracle_cards"] == 0


def test_enabled_sources_are_loaded_recorded_and_then_skipped(database_url, monkeypatch, universe):
    monkeypatch.setenv("DATABASE_URL", database_url)
    seed(universe)
    first = sync_catalog.main(["--sources", "oracle_cards,rulings,oracle_tags"], transport=universe.transport)
    assert first["oracle_cards"]["cards"] >= 1 and first["rulings"]["written"] == 1 and first["oracle_tags"]["links"] == 1
    found = counts(database_url)
    assert found["rulings"] == 1 and found["oracle_tag_links"] == 1 and found["catalog_sources"] == 3
    assert any(p.endswith(".jsonl.gz") for p in (c.path for c in universe.scryfall.calls))
    again = sync_catalog.main(["--sources", "oracle_cards,rulings,oracle_tags"], transport=universe.transport)
    assert all("skipped" in again[name] for name in again)
    forced = sync_catalog.main(["--sources", "rulings", "--force"], transport=universe.transport)
    assert forced["rulings"]["written"] == 0  # same content: not rewritten


def test_one_source_can_be_enabled_alone(database_url, monkeypatch, universe):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CATALOG_SOURCES", "rulings")
    seed(universe)
    sync_catalog.main([], transport=universe.transport)
    found = counts(database_url)
    assert found["rulings"] == 1 and found["oracle_cards"] == 0


def test_cheapest_prices_come_with_the_daily_price_job_once_enabled(database_url, monkeypatch, universe):
    from jobs import sync_prices
    from vault.models import OraclePrice

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("CATALOG_SOURCES", raising=False)
    sol = universe.scryfall.find("Sol Ring")
    universe.scryfall.set_price(sol["id"], usd=1.5)
    sync_prices.main([], transport=universe.transport)
    db = Database(database_url)
    with db.sessions() as s:
        assert s.scalar(select(func.count()).select_from(OraclePrice)) == 0  # not enabled: nothing stored
    monkeypatch.setenv("CATALOG_SOURCES", "oracle_prices")
    report = sync_prices.main([], transport=universe.transport)
    with db.sessions() as s:
        assert s.get(OraclePrice, sol["oracle_id"]).usd == 1.5
        assert s.get(CatalogSource, "oracle_prices").rows >= 1
    db.engine.dispose()


def test_an_unknown_source_is_refused(database_url, monkeypatch, universe):
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(SystemExit):
        sync_catalog.main(["--sources", "rules,everything"], transport=universe.transport)


def test_a_source_another_job_loads_is_skipped_not_refused(database_url, monkeypatch, universe):
    """CATALOG_SOURCES is shared with the price job: oracle_prices there must not stop the catalog job."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert sync_catalog.main(["--sources", "oracle_prices"], transport=universe.transport) == {}
