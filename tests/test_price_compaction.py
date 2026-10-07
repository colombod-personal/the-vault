"""Migration 0110 compacts price_snapshots (#63): integer cents, a native uuid key, the unread column dropped.

Run on a copy of real-shaped rows: Scryfall ids (random v4 UUIDs, as Scryfall's are), a date, six float prices with the
gaps real cards have (no foil, no etched), plus the junk an old table can hold: a price above the plausible maximum, infinity,
NaN, a malformed id and an upper-case id. The test checks every value, the bytes a row before and after (the numbers in
docs/catalog-design.md come from the same query), the way back (downgrade), and that the app's readers work on the new table."""

from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

import vault.db
from vault.db import Database
from vault.models import PriceSnapshot

ROWS = 30_000  # 3,000 printings x 10 days: enough for stable bytes-a-row, quick in CI
PRICE_COLUMNS = ("usd", "usd_foil", "usd_etched", "eur", "eur_foil")


@pytest.fixture
def database(blank_database_url):
    db = Database(blank_database_url)
    yield db
    db.engine.dispose()


def alembic(database, revision, direction="upgrade"):
    config = Config()
    config.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))
    with database.engine.begin() as conn:
        config.attributes["connection"] = conn
        getattr(command, direction)(config, revision)


def bytes_a_row(database) -> float:
    with database.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM ANALYZE price_snapshots"))
        total = conn.execute(text("SELECT pg_total_relation_size('price_snapshots')")).scalar()
        rows = conn.execute(text("SELECT count(*) FROM price_snapshots")).scalar()
    return total / rows


def fill(database):
    """The table as 0109 left it, with ROWS real-shaped rows plus the edge cases."""
    with database.engine.begin() as conn:
        conn.execute(text(f"""
            INSERT INTO price_snapshots
            SELECT p.id::text, (date '2026-09-01' + d), round((random() * 30)::numeric, 2),
                   CASE WHEN random() < .6 THEN round((random() * 60)::numeric, 2) END,
                   CASE WHEN random() < .05 THEN round((random() * 60)::numeric, 2) END,
                   round((random() * 25)::numeric, 2),
                   CASE WHEN random() < .6 THEN round((random() * 50)::numeric, 2) END,
                   CASE WHEN random() < .05 THEN round((random() * 50)::numeric, 2) END
            FROM (SELECT gen_random_uuid() AS id FROM generate_series(1, {ROWS // 10})) p, generate_series(0, 9) d"""))
        conn.execute(text("""
            INSERT INTO price_snapshots (scryfall_id, day, usd, usd_foil, usd_etched, eur, eur_foil, eur_etched) VALUES
              ('00000000-0000-4000-8000-000000000001', '2026-10-01', 0.29, 1.15, NULL, 0.2, NULL, 9.99),
              ('00000000-0000-4000-8000-000000000002', '2026-10-01', 'Infinity', 1e308, 12345678.9, 'NaN', -0.5, NULL),
              ('00000000-0000-4000-8000-000000000003', '2026-10-01', NULL, NULL, NULL, NULL, NULL, NULL),
              ('00000000-0000-4000-8000-000000000004', '2026-10-01', 10000000, 0.005, 0.015, 1234.565, 0, NULL),
              ('NOT-A-SCRYFALL-ID', '2026-10-01', 1.0, NULL, NULL, NULL, NULL, NULL),
              ('ABCDEF00-0000-4000-8000-0000000000AA', '2026-10-01', 2.5, NULL, NULL, NULL, NULL, NULL)"""))


def old_rows(database, ids):
    with database.engine.connect() as conn:
        return {r[0]: r[1:] for r in conn.execute(text(
            "SELECT scryfall_id, usd, usd_foil, usd_etched, eur, eur_foil FROM price_snapshots WHERE scryfall_id = ANY(:ids)"), {"ids": ids})}


def test_the_migration_turns_real_shaped_rows_into_cents_under_a_uuid_key_and_the_row_gets_smaller(database):
    alembic(database, "0109")
    fill(database)
    before_bytes = bytes_a_row(database)
    with database.engine.connect() as conn:
        everything = {(str(r[0]), r[1]): r[2:] for r in conn.execute(text(
            "SELECT scryfall_id, day, usd, usd_foil, usd_etched, eur, eur_foil FROM price_snapshots WHERE day < '2026-09-20'"))}
    assert len(everything) == ROWS

    alembic(database, "0110")
    after_bytes = bytes_a_row(database)

    with database.engine.connect() as conn:
        columns = {c: t for c, t in conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'price_snapshots'"))}
        assert columns == {"scryfall_id": "uuid", "day": "date", **{f"{c}_cents": "integer" for c in PRICE_COLUMNS}}  # eur_etched is gone
        rows = {str(r[0]): r[2:] for r in conn.execute(text(
            "SELECT scryfall_id, day, usd_cents, usd_foil_cents, usd_etched_cents, eur_cents, eur_foil_cents FROM price_snapshots WHERE day = '2026-10-01'"))}
        by_day = {(str(r[0]), r[1]): r[2:] for r in conn.execute(text(
            "SELECT scryfall_id, day, usd_cents, usd_foil_cents, usd_etched_cents, eur_cents, eur_foil_cents FROM price_snapshots WHERE day < '2026-09-20'"))}
        total = conn.execute(text("SELECT count(*) FROM price_snapshots")).scalar()

    assert total == ROWS + 5  # the malformed id's row is the only one removed: no printing has such an id
    assert "not-a-scryfall-id" not in rows
    assert rows["00000000-0000-4000-8000-000000000001"] == (29, 115, None, 20, None)  # 0.29 is 29 cents, not 28.999...
    assert rows["00000000-0000-4000-8000-000000000002"] == (None, None, None, None, -50)  # junk is no price; a negative stays
    assert rows["00000000-0000-4000-8000-000000000003"] == (None,) * 5
    assert rows["00000000-0000-4000-8000-000000000004"] == (1_000_000_000, 1, 2, 123457, 0)  # the maximum stays; rounding to cents
    assert rows["abcdef00-0000-4000-8000-0000000000aa"] == (250, None, None, None, None)  # an upper-case id is the same uuid
    for (key, day), dollars in everything.items():  # every ordinary value is exactly its old dollars times 100
        got = by_day[(key, day)]
        assert got == tuple(None if d is None else round(d * 100) for d in dollars), (key, day, dollars, got)

    print(f"price_snapshots bytes a row: before {before_bytes:.0f}, after {after_bytes:.0f} ({after_bytes / before_bytes:.0%})")
    assert after_bytes < 0.75 * before_bytes, (before_bytes, after_bytes)  # the point of the migration: at least a quarter smaller


def test_the_primary_key_still_stops_a_second_row_for_the_same_printing_and_day(database):
    alembic(database, "0110")
    with database.engine.begin() as conn:
        conn.execute(text("INSERT INTO price_snapshots (scryfall_id, day, usd_cents) VALUES ('11111111-1111-4111-8111-111111111111', '2026-10-01', 5)"))
    with pytest.raises(Exception, match="price_snapshots_pkey|duplicate key"):
        with database.engine.begin() as conn:
            conn.execute(text("INSERT INTO price_snapshots (scryfall_id, day, usd_cents) VALUES ('11111111-1111-4111-8111-111111111111', '2026-10-01', 6)"))


def test_the_app_reads_and_writes_the_new_table_in_dollars(database):
    alembic(database, "0110")
    with database.sessions() as db:
        db.add(PriceSnapshot(scryfall_id="22222222-2222-4222-8222-222222222222", day=date(2026, 10, 1), usd=1.23, usd_foil=4.5,
                             eur=float("inf"), usd_etched=1.7e308))
        db.commit()
        row = db.scalars(select(PriceSnapshot)).one()
        assert (row.usd, row.usd_foil, row.eur, row.usd_etched, row.eur_foil) == (1.23, 4.5, None, None, None)
        assert (row.usd_cents, row.usd_foil_cents) == (123, 450)
        assert row.for_finish("foil") == 4.5 and row.for_finish("nonfoil") == 1.23 and row.for_finish("etched") is None
        assert row.for_finish("foil", "eur") is None
        row.usd = 0.1 + 0.2  # float noise is rounded away
        db.commit()
        assert db.scalars(select(PriceSnapshot.usd_cents)).one() == 30


def test_the_way_back_restores_the_old_table_with_the_same_prices(database):
    alembic(database, "0109")
    fill(database)
    ids = [f"00000000-0000-4000-8000-00000000000{i}" for i in (1, 3)]
    before = old_rows(database, ids)
    alembic(database, "0110")
    alembic(database, "0109", "downgrade")
    with database.engine.connect() as conn:
        columns = {c: t for c, t in conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'price_snapshots'"))}
        assert columns["scryfall_id"] == "character varying" and columns["usd"] == "double precision" and "eur_etched" in columns
    assert old_rows(database, ids) == {k: tuple(v) for k, v in before.items()}  # the same dollars, to the cent, under the same text key


def test_a_database_made_by_create_all_of_the_current_models_is_left_alone(database):
    from vault.db import Base

    Base.metadata.create_all(database.engine)
    database.migrate()  # 0110 sees usd_cents and does nothing
    with database.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_name = 'price_snapshots'")).scalar() == 7
