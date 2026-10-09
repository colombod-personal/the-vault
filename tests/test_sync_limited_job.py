"""The 17Lands job (jobs/sync_limited.py) against the digital twin of 17Lands' S3 host (twins/seventeenlands.py): it reads a file once
as a stream, keeps per-card counts only, reads a version once, and writes nothing when a file fails (#178, design section 4).

The files are the invented fixtures of tests/fixtures; nothing here touches the network and no real 17Lands file exists on any machine
that runs these tests."""

import builtins
import gzip
from pathlib import Path

import pytest
from sqlalchemy import func, select

from jobs import sync_limited
from tests.test_catalog_api import card
from tests.test_limited_stats import DRAFTS, GAMES, rewrite
from twins import Universe
from vault import catalog_sync as cs
from vault import limited_stats as ls
from vault.db import Database
from vault.models import CatalogSource, LimitedGameStat, LimitedPickStat, LimitedSource

ARGS = ["--sources", "limited_17lands", "--sets", "TST"]
BEAR, DFC = "33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def small_files(monkeypatch):
    """The fixtures hold 6 games and 4 cards: lower the floors of the sanity checks (a test of its own uses the real ones)."""
    monkeypatch.setattr(ls, "MIN_RECORDS", 5)
    monkeypatch.setattr(ls, "MIN_CARDS", 3)


@pytest.fixture
def napping(monkeypatch):
    """The waits between retries, recorded instead of slept."""
    waits = []
    monkeypatch.setattr(sync_limited, "sleep", waits.append)
    return waits


@pytest.fixture
def env(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("CATALOG_SOURCES", raising=False)
    monkeypatch.delenv("NEON_STORAGE_LIMIT_MB", raising=False)
    return database_url


def publish(universe, games=GAMES, drafts=DRAFTS, fmt="PremierDraft", set_code="TST"):
    universe.seventeenlands.publish("game", set_code, fmt, games)
    universe.seventeenlands.publish("draft", set_code, fmt, drafts)


def run(universe, *more, args=ARGS):
    return sync_limited.main([*args, *more], transport=universe.transport)


def states(report):
    return {(f["format"], f["kind"]): f["status"] for f in report["files"]}


def database(url):
    return Database(url)


def rows(url, model):
    db = database(url)
    with db.sessions() as s:
        out = s.scalars(select(model).order_by(*model.__table__.primary_key.columns)).all()
    db.engine.dispose()
    return out


def requests(universe, method):
    return [c.path for c in universe.seventeenlands.calls if c.method == method]


def load_catalog(url):
    db = database(url)
    with db.sessions() as s:
        cs.sync_oracle_cards(s, [card(BEAR, "Test Bear", "Vanilla."),
                                 card(DFC, "Front Face // Back Face", "Transforms.", layout="transform")])
        s.commit()
    db.engine.dispose()


# -- the gate -------------------------------------------------------------------------------------------------------------

def test_nothing_is_read_unless_the_source_is_enabled(env, universe, small_files):
    publish(universe)
    assert sync_limited.main([], transport=universe.transport) == {}
    assert sync_limited.main(["--sources", "oracle_prices"], transport=universe.transport) == {}  # another job's source: skipped
    assert universe.seventeenlands.calls == []
    assert rows(env, LimitedGameStat) == []


def test_a_source_nobody_knows_is_refused(env, universe):
    with pytest.raises(SystemExit):
        sync_limited.main(["--sources", "limited_17lands,some_new_source"], transport=universe.transport)
    assert universe.seventeenlands.calls == []


def test_the_catalog_job_skips_this_source_instead_of_refusing_it(env, universe):
    from jobs import sync_catalog
    assert sync_catalog.main(["--sources", "limited_17lands"], transport=universe.transport) == {}


@pytest.mark.parametrize("argument", [["--sets", "H O B"], ["--sets", ""], ["--formats", "PickTwoDraft"], ["--formats", "Sealed"]])
def test_only_set_codes_and_the_two_formats_are_accepted(env, universe, argument):
    args = ["--sources", "limited_17lands", "--sets", "TST"] if "--sets" not in argument else ["--sources", "limited_17lands"]
    if argument[1] == "":
        argument = ["--sets", " , "]
    with pytest.raises(SystemExit):
        sync_limited.main([*args, *argument], transport=universe.transport)
    assert universe.seventeenlands.calls == []


# -- a load -----------------------------------------------------------------------------------------------------------------

def test_a_load_keeps_the_counts_the_catalog_match_and_the_version_of_each_file(env, universe, small_files):
    load_catalog(env)
    publish(universe)
    report = run(universe)
    assert states(report) == {("PremierDraft", "game"): "loaded", ("PremierDraft", "draft"): "loaded"}
    games = {g.card_name: g for g in rows(env, LimitedGameStat)}
    assert set(games) == {"Test Bear", "Test Bolt", "Test Elf", "Front Face // Back Face"}
    bear = games["Test Bear"]
    assert (bear.games_played, bear.wins_played, bear.opening, bear.wins_opening, bear.drawn, bear.wins_drawn) == (12, 8, 4, 4, 3, 1)
    assert bear.oracle_id == BEAR and games["Front Face // Back Face"].oracle_id == DFC  # matched by name
    assert games["Test Bolt"].oracle_id is None  # not in the catalog: stays unmatched, still kept
    picks = {p.card_name: p for p in rows(env, LimitedPickStat)}
    assert (picks["Test Bear"].seen, picks["Test Bear"].last_seen_sum, picks["Test Bear"].picked, picks["Test Bear"].picked_sum) == (3, 12, 3, 12)
    db = database(env)
    with db.sessions() as s:
        game = s.get(LimitedSource, ("TST", "PremierDraft", "game"))
        draft = s.get(LimitedSource, ("TST", "PremierDraft", "draft"))
        assert (game.records, game.skipped_records, game.wins, game.cards, game.unmatched_cards) == (6, 1, 4, 4, 2)
        assert game.etag == universe.seventeenlands.files[universe.seventeenlands.path("game", "TST", "PremierDraft")]["etag"]
        assert game.last_modified.isoformat() == "2026-10-01T12:00:00+00:00" and game.first_time.isoformat() == "2026-09-10T10:00:00+00:00"
        assert (draft.records, draft.first_picks, draft.empty_first_picks) == (9, 3, 0)
        row = s.get(CatalogSource, "limited_17lands")  # what whoami shows
        assert row.rows == 8 and row.url == "https://www.17lands.com/public_datasets" and "2026-10-01" in row.version
    db.engine.dispose()


def test_the_files_are_asked_for_politely_and_only_the_two_kinds_we_use(env, universe, small_files):
    publish(universe)
    universe.seventeenlands.publish_raw("replay", "TST", "PremierDraft", b"never read")
    run(universe)
    calls = universe.seventeenlands.calls
    assert {c.method for c in calls} == {"HEAD", "GET"}
    assert all(c.headers["user-agent"] == "the-vault/0.1 (+https://github.com/colombod-personal/the-vault)" for c in calls)
    assert all(c.headers["accept-encoding"] == "identity" for c in calls if c.method == "GET")
    assert not any("replay" in c.path for c in calls)  # the largest files are never touched
    assert [c.path.split("/")[2] for c in calls if c.method == "GET"] and len(requests(universe, "GET")) == 2
    assert requests(universe, "GET") == sorted(requests(universe, "GET"), key=lambda p: len(universe.seventeenlands.files[p]["body"]))  # smallest first


def test_the_files_are_never_written_to_disk(env, universe, small_files, monkeypatch, tmp_path):
    publish(universe)
    real_open = builtins.open

    def guarded(file, mode="r", *args, **kwargs):
        assert not any(c in str(mode) for c in "wax+"), f"opened {file!r} for writing"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    monkeypatch.chdir(tmp_path)
    run(universe)
    assert list(tmp_path.iterdir()) == []  # nothing left behind in the working directory
    source = Path(sync_limited.__file__).read_text(encoding="utf-8")
    assert "tempfile" not in source and "write_bytes" not in source and "NamedTemporaryFile" not in source


def test_the_run_is_idempotent_a_version_already_loaded_is_not_read_again(env, universe, small_files):
    publish(universe)
    run(universe)
    before = [(g.card_name, g.opening, g.drawn) for g in rows(env, LimitedGameStat)]
    gets = len(requests(universe, "GET"))
    again = run(universe)
    assert states(again) == {("PremierDraft", "game"): "skipped", ("PremierDraft", "draft"): "skipped"}
    assert len(requests(universe, "GET")) == gets  # only HEAD requests the second time
    assert [(g.card_name, g.opening, g.drawn) for g in rows(env, LimitedGameStat)] == before
    forced = run(universe, "--force")
    assert states(forced) == {("PremierDraft", "game"): "loaded", ("PremierDraft", "draft"): "loaded"}
    assert len(requests(universe, "GET")) == gets + 2
    assert [(g.card_name, g.opening, g.drawn) for g in rows(env, LimitedGameStat)] == before  # replaced, not doubled


def test_a_new_version_of_a_file_replaces_its_rows_and_only_its_rows(env, universe, small_files):
    publish(universe)
    run(universe)
    lost = GAMES.replace("game-draft-1,2026-09-10 10:00:00,True", "game-draft-1,2026-09-10 10:00:00,False")  # game 1 is a loss now
    universe.seventeenlands.publish("game", "TST", "PremierDraft", lost)  # the same content would give the same ETag; this differs
    report = run(universe)
    assert states(report) == {("PremierDraft", "game"): "loaded", ("PremierDraft", "draft"): "skipped"}
    bear = next(g for g in rows(env, LimitedGameStat) if g.card_name == "Test Bear")
    assert (bear.games_played, bear.wins_played, bear.wins_opening, bear.wins_drawn) == (12, 6, 3, 0)  # game 1: 2 copies, 1 opening, 1 drawn
    assert len(rows(env, LimitedPickStat)) == 4  # the draft rows were not touched


def test_a_set_and_format_other_than_the_default_can_be_asked_for(env, universe, small_files):
    publish(universe, fmt="TradDraft")
    report = sync_limited.main(["--sources", "limited_17lands", "--sets", "tst", "--formats", "TradDraft,PremierDraft"], transport=universe.transport)
    assert states(report) == {("TradDraft", "game"): "loaded", ("TradDraft", "draft"): "loaded",
                              ("PremierDraft", "game"): "not_published", ("PremierDraft", "draft"): "not_published"}
    assert {g.format for g in rows(env, LimitedGameStat)} == {"TradDraft"}


# -- what is refused: nothing is written --------------------------------------------------------------------------------------

def failed(universe, *args):
    with pytest.raises(SystemExit) as error:
        run(universe, *args)
    return str(error.value)


def test_a_truncated_download_writes_nothing_and_keeps_the_rows_already_stored(env, universe, small_files):
    publish(universe)
    run(universe)
    stored = [(g.card_name, g.opening) for g in rows(env, LimitedGameStat)]
    cut = gzip.compress(GAMES.replace("TST", "TST").encode(), mtime=0)
    universe.seventeenlands.publish_raw("game", "TST", "PremierDraft", cut[: len(cut) // 2])
    message = failed(universe)
    assert "truncated or corrupt" in message and "PremierDraft game" in message
    assert [(g.card_name, g.opening) for g in rows(env, LimitedGameStat)] == stored
    db = database(env)
    with db.sessions() as s:
        assert s.get(LimitedSource, ("TST", "PremierDraft", "game")).etag != universe.seventeenlands.files[
            universe.seventeenlands.path("game", "TST", "PremierDraft")]["etag"]  # the version stored is still the good one
    db.engine.dispose()


def test_a_first_load_of_a_truncated_file_leaves_the_tables_empty(env, universe, small_files):
    cut = gzip.compress(GAMES.encode(), mtime=0)
    universe.seventeenlands.publish_raw("game", "TST", "PremierDraft", cut[:-10])
    failed(universe)
    assert rows(env, LimitedGameStat) == [] and rows(env, LimitedSource) == []


def test_a_file_without_the_columns_it_needs_is_refused_and_says_which(env, universe, small_files):
    universe.seventeenlands.publish("game", "TST", "PremierDraft", rewrite(GAMES, lambda h, b: (["wins" if c == "won" else c for c in h], b)))
    assert "lacks the column(s) won" in failed(universe)
    assert rows(env, LimitedGameStat) == []


def test_a_file_for_another_set_is_refused(env, universe, small_files):
    universe.seventeenlands.publish("game", "TST", "PremierDraft", GAMES.replace("TST", "OTH"))
    assert "expansion" in failed(universe) and rows(env, LimitedGameStat) == []


def test_the_sanity_checks_refuse_a_file_that_is_too_small(env, universe):
    """With the real thresholds, the 6-game fixture is refused: at least 1,000 games and 100 cards are needed."""
    publish(universe)
    message = failed(universe)
    assert "only 6 games used; at least 1000 are needed" in message and "only 4 cards found; at least 100 are needed" in message
    assert rows(env, LimitedGameStat) == [] and rows(env, LimitedSource) == []


def test_a_new_file_that_loses_most_of_the_stored_cards_is_refused_unless_forced(env, universe, small_files):
    publish(universe)
    run(universe)
    few = rewrite(GAMES, lambda h, b: ([c for c in h if "Bolt" not in c and "Elf" not in c and "Front" not in c],
                                       [[v for v, c in zip(r, h) if "Bolt" not in c and "Elf" not in c and "Front" not in c] for r in b]))
    universe.seventeenlands.publish("game", "TST", "PremierDraft", few)
    assert "would drop the stored cards from 4 to 1" in failed(universe) or "only 1 cards found" in failed(universe)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(ls, "MIN_CARDS", 1)
    monkey.setattr(ls, "BASELINE_RANGE", (0.0, 1.0))  # one card's columns left, so game 5 is no longer inconsistent
    try:
        assert "would drop the stored cards from 4 to 1" in failed(universe)
        report = run(universe, "--force")
        assert states(report)[("PremierDraft", "game")] == "loaded"
        assert {g.card_name for g in rows(env, LimitedGameStat)} == {"Test Bear"}
    finally:
        monkey.undo()


def test_a_file_far_larger_than_any_seen_is_refused_from_its_content_length_without_downloading_it(env, universe, small_files):
    publish(universe)
    universe.seventeenlands.publish_raw("game", "TST", "PremierDraft", b"x", advertised_length=500_000_000)
    assert "Content-Length 500000000" in failed(universe)
    assert [p for p in requests(universe, "GET") if "game_data" in p] == []
    assert len(rows(env, LimitedPickStat)) == 4  # the other file was still read


def test_the_per_run_byte_cap_defers_the_rest_to_the_next_run(env, universe, small_files, monkeypatch):
    publish(universe)
    sizes = sorted(len(f["body"]) for f in universe.seventeenlands.files.values())
    monkeypatch.setattr(sync_limited, "MAX_RUN_BYTES", sizes[0] + 1)
    first = run(universe)
    assert sorted(states(first).values()) == ["deferred", "loaded"]
    monkeypatch.setattr(sync_limited, "MAX_RUN_BYTES", 800_000_000)
    second = run(universe)
    assert sorted(states(second).values()) == ["loaded", "skipped"]
    assert len(rows(env, LimitedGameStat)) == 4 and len(rows(env, LimitedPickStat)) == 4


def test_the_database_budget_stops_the_job_before_it_asks_for_anything(env, universe, small_files, monkeypatch):
    publish(universe)
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", "0.001")  # the test database is far over 85% of this
    with pytest.raises(SystemExit, match="not adding data"):
        run(universe)
    assert universe.seventeenlands.calls == []


# -- the host's behaviour -------------------------------------------------------------------------------------------------------

def test_a_file_that_is_not_published_is_reported_and_is_not_a_failure(env, universe, small_files):
    report = run(universe)  # nothing published: S3 answers 403 for a file that does not exist
    assert set(states(report).values()) == {"not_published"}
    assert [c.status for c in universe.seventeenlands.calls] == [403, 403]


def test_a_file_that_was_withdrawn_keeps_its_stored_rows_and_the_run_does_not_fail(env, universe, small_files):
    publish(universe)
    run(universe)
    universe.seventeenlands.unpublish("game", "TST", "PremierDraft")
    report = run(universe)
    assert states(report) == {("PremierDraft", "game"): "no_longer_published", ("PremierDraft", "draft"): "skipped"}
    assert len(rows(env, LimitedGameStat)) == 4


def test_server_errors_and_429_are_retried_with_the_waits_of_the_design_and_retry_after_wins(env, universe, small_files, napping):
    publish(universe)
    universe.seventeenlands.fail_next("/analysis_data/*", 503, times=2)
    universe.seventeenlands.fail_next("/analysis_data/*", 429, times=1, retry_after="7")
    report = run(universe)
    assert set(states(report).values()) == {"loaded"}
    assert napping == [2, 8, 7]  # 503 -> 2 s, 503 -> 8 s, 429 with Retry-After: 7 s


def test_after_three_retries_the_file_fails_and_the_others_are_still_read(env, universe, small_files, napping):
    publish(universe)
    universe.seventeenlands.fail_next("/analysis_data/game_data/*", 500, times=4)
    with pytest.raises(SystemExit) as error:
        run(universe)
    assert napping == [2, 8, 30] and "after 3 retries" in str(error.value)
    assert len(rows(env, LimitedPickStat)) == 4 and rows(env, LimitedGameStat) == []


def test_other_client_errors_are_not_retried(env, universe, small_files, napping):
    publish(universe)
    universe.seventeenlands.fail_next("/analysis_data/draft_data/*", 400, times=1)
    with pytest.raises(SystemExit) as error:
        run(universe)
    assert napping == [] and "HEAD answered 400" in str(error.value)


def test_a_connection_error_is_retried(env, universe, small_files, napping, monkeypatch):
    import httpx
    publish(universe)
    real = universe.handle
    seen = {"n": 0}

    def flaky(request):
        seen["n"] += 1
        if seen["n"] == 1:
            raise httpx.ConnectError("reset", request=request)
        return real(request)

    transport = httpx.MockTransport(flaky)
    report = sync_limited.main(ARGS, transport=transport)
    assert set(states(report).values()) == {"loaded"} and napping == [2]
