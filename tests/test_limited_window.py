"""The rolling window of the Limited job and the terms re-read (#417, docs/limited-data-design.md sections 4 and 14), against the digital
twins of Scryfall (the list of sets) and of 17Lands' S3 host (HEAD answers 200 for a file and 403 for one that is not there).

Dates are fixed: the job's clock reads 2026-10-10 (``env`` in tests/test_sync_limited_job.py). The files are the invented fixtures of
tests/fixtures; nothing touches the network."""

from datetime import date

import httpx
import pytest

from jobs import limited_terms, limited_window, sync_limited
from tests.test_limited_stats import DRAFTS, GAMES
from tests.test_sync_limited_job import database, env, napping, rows, small_files, states  # noqa: F401  (fixtures)
from twins import Universe
from vault.models import CatalogSource, LimitedGameStat, LimitedPickStat, LimitedSource

TODAY = date(2026, 10, 10)
BOTH = ("PremierDraft", "TradDraft")
ARGS = ["--sources", "limited_17lands"]  # no --sets: the job works the window out itself


@pytest.fixture
def universe():
    """The twins without the seed cards: Scryfall's list of sets holds only the sets a test adds."""
    u = Universe(seed=False)
    yield u
    assert not u.escapes, u.escapes


def add_set(universe, code, released, set_type="expansion"):
    """A set in Scryfall's list (the twin builds /sets from the cards it holds)."""
    universe.scryfall.add_card(f"{code} Card", code.lower(), "1", set_type=set_type, released_at=released, set_name=f"Set {code}")


def publish(universe, code, formats=BOTH, kinds=("game", "draft")):
    for fmt in formats:
        if "game" in kinds:
            universe.seventeenlands.publish("game", code, fmt, GAMES.replace("TST", code))
        if "draft" in kinds:
            universe.seventeenlands.publish("draft", code, fmt, DRAFTS.replace("TST", code))


def month_sets(universe, count):
    """``count`` expansions released on the 1st of consecutive months of 2026 (A01, A02...), all published in both formats."""
    codes = []
    for n in range(1, count + 1):
        code = f"A{n:02d}"
        add_set(universe, code, f"2026-{n:02d}-01")
        publish(universe, code)
        codes.append(code)
    return codes


def run(universe, *more, args=ARGS):
    return sync_limited.main([*args, *more], transport=universe.transport)


def stored_sets(url, model=LimitedSource):
    return sorted({(r.set_code, r.format) for r in rows(url, model)})


def asked(universe):
    """The (set, format) pairs that 17Lands was asked about with HEAD for a game file, in order."""
    out = []
    for c in universe.seventeenlands.calls:
        if c.method == "HEAD" and "/game_data/" in c.path:
            name = c.path.rsplit("/", 1)[1].split(".")
            out.append((name[1], name[2]))
    return out


def file_status(report, set_code, fmt, kind):
    return next(f["status"] for f in report["files"] if (f["set"], f["format"], f["kind"]) == (set_code, fmt, kind))


# -- the pure rules -------------------------------------------------------------------------------------------------------

def test_months_before_clamps_the_day_and_crosses_years():
    assert limited_window.months_before(date(2026, 10, 10), 30) == date(2024, 4, 10)
    assert limited_window.months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert limited_window.months_before(date(2026, 1, 15), 1) == date(2025, 12, 15)


def test_candidates_are_the_four_kinds_of_draftable_sets_of_the_last_30_months_newest_first_and_too_new_sets_wait():
    sets = [{"code": c, "set_type": t, "released_at": d} for c, t, d in (
        ("abc", "expansion", "2026-09-01"), ("cor", "core", "2026-06-13"), ("mas", "masters", "2025-03-07"), ("inn", "draft_innovation", "2025-12-12"),
        ("tok", "token", "2026-09-01"), ("prm", "promo", "2026-09-01"), ("fun", "funny", "2026-09-01"), ("alc", "alchemy", "2026-09-01"),
        ("old", "expansion", "2024-04-09"),          # 30 months and a day ago: outside
        ("edge", "expansion", "2024-04-10"),         # exactly 30 months ago: inside
        ("new", "expansion", "2026-10-01"),          # 9 days ago: waits
        ("last", "expansion", "2026-09-26"),         # 14 days ago: read
        ("soon", "expansion", "2026-12-04"),         # not released yet: waits
        ("nodate", "expansion", None), ("bad code!", "expansion", "2026-01-01"))]
    found, waiting = limited_window.candidates(sets, TODAY)
    assert [c.code for c in found] == ["LAST", "ABC", "COR", "INN", "MAS", "EDGE"]
    assert waiting == {"NEW": date(2026, 10, 1), "SOON": date(2026, 12, 4)}


def test_a_set_leaves_when_it_is_stored_in_a_looked_at_format_and_not_in_the_window_nor_waiting():
    stored = {("A", "PremierDraft"), ("B", "PremierDraft"), ("W", "PremierDraft"), ("A", "TradDraft"), ("Z", "Sealed")}
    window = {"PremierDraft": ["A"], "TradDraft": ["A", "C"]}
    waiting = {"W": date(2026, 10, 5)}
    assert limited_window.leaving(stored, window, waiting, ["PremierDraft", "TradDraft"]) == [("B", "PremierDraft")]
    assert limited_window.leaving(stored, window, waiting, ["TradDraft"]) == []  # another format was not looked at
    assert limited_window.leaving(stored, {"PremierDraft": []}, waiting, ["PremierDraft"]) == []  # an empty window removes nothing


# -- the window against the twins -----------------------------------------------------------------------------------------------

def test_the_weekly_run_needs_no_input_both_formats_the_eight_newest_sets_and_nothing_older_is_even_asked_about(env, universe, small_files):
    codes = month_sets(universe, 9)  # A01..A09, all published in both formats
    report = run(universe)
    assert report["window"] == {fmt: list(reversed(codes[1:])) for fmt in BOTH}  # A09..A02: the ninth (A01, the oldest) is not in
    assert {(f["set"], f["format"]) for f in report["files"]} == {(c, fmt) for c in codes[1:] for fmt in BOTH}
    assert set(states(report).values()) == {"loaded"} and len(report["files"]) == 8 * 2 * 2
    assert ("A01", "PremierDraft") not in asked(universe) and ("A01", "TradDraft") not in asked(universe)  # the walk stops at the eighth
    assert stored_sets(env) == sorted({(c, fmt) for c in codes[1:] for fmt in BOTH})
    db = database(env)
    with db.sessions() as s:
        assert s.get(CatalogSource, "limited_17lands").rows == 8 * 2 * 8  # 4 game rows and 4 pick rows per set and format
    db.engine.dispose()


def test_a_ninth_set_arrives_the_oldest_goes_with_its_rows_in_the_same_run(env, universe, small_files):
    codes = month_sets(universe, 8)  # A01..A08
    add_set(universe, "A09", "2026-09-20")  # released, but 17Lands has not published it: S3 answers 403
    first = run(universe)
    assert first["window"]["PremierDraft"] == list(reversed(codes)) and first["removed"] == []
    assert ("A09", "PremierDraft") in asked(universe) and ("A09", "PremierDraft") not in stored_sets(env)  # asked, passed over (403)
    assert [c.status for c in universe.seventeenlands.calls if "A09" in c.path] == [403, 403]
    assert len(stored_sets(env)) == 16

    universe.seventeenlands.calls.clear()
    publish(universe, "A09")  # the ninth set is published
    second = run(universe)
    assert second["window"]["PremierDraft"] == ["A09", *reversed(codes[1:])]
    assert sorted((r["set"], r["format"]) for r in second["removed"]) == [("A01", "PremierDraft"), ("A01", "TradDraft")]
    assert all(r["game_rows"] == 4 and r["pick_rows"] == 4 and r["files"] == 2 for r in second["removed"])
    for model in (LimitedSource, LimitedGameStat, LimitedPickStat):
        assert {r.set_code for r in rows(env, model)} == {*codes[1:], "A09"}, model.__name__  # no row of the oldest set is left in any table
    assert {f["status"] for f in second["files"] if f["set"] == "A09"} == {"loaded"}
    assert {f["status"] for f in second["files"] if f["set"] != "A09"} == {"skipped"}  # the seven that stayed were not read again
    db = database(env)
    with db.sessions() as s:
        assert s.get(CatalogSource, "limited_17lands").rows == 8 * 2 * 8  # the source row follows the rows
    db.engine.dispose()


def test_a_set_is_not_read_until_14_days_after_its_release_and_is_then(env, universe, small_files, monkeypatch):
    month_sets(universe, 2)
    add_set(universe, "NEW", "2026-10-01")  # released 9 days ago
    publish(universe, "NEW")
    report = run(universe)
    assert report["waiting_after_release"] == {"NEW": "2026-10-01"}
    assert all(code != "NEW" for code, _ in asked(universe)) and all(f["set"] != "NEW" for f in report["files"])
    assert "NEW" not in {s for s, _ in stored_sets(env)}
    monkeypatch.setattr(limited_terms, "today", lambda: date(2026, 10, 15))  # 14 days after 2026-10-01
    later = run(universe)
    assert later["waiting_after_release"] == {} and later["window"]["PremierDraft"][0] == "NEW"
    assert ("NEW", "PremierDraft") in stored_sets(env)


def test_a_set_that_waits_is_never_deleted_even_when_it_was_loaded_by_hand(env, universe, small_files):
    month_sets(universe, 2)
    add_set(universe, "NEW", "2026-10-05")
    publish(universe, "NEW")
    run(universe, args=["--sources", "limited_17lands", "--sets", "NEW"])  # a manual fill: no wait for the one named
    assert ("NEW", "PremierDraft") in stored_sets(env)
    report = run(universe)
    assert report["removed"] == [] and ("NEW", "PremierDraft") in stored_sets(env)  # kept; the weekly run does not read it again either
    assert all(f["set"] != "NEW" for f in report["files"])


def test_only_the_draftable_set_types_of_the_last_30_months_are_asked_about(env, universe, small_files):
    month_sets(universe, 1)
    for code, kind, day in (("TOK", "token", "2026-08-01"), ("ALC", "alchemy", "2026-08-01"), ("MAS", "masters", "2026-02-01"),
                            ("OLD", "expansion", "2024-03-01")):
        add_set(universe, code, day, kind)
        publish(universe, code)
    run(universe)
    assert {code for code, _ in asked(universe)} == {"A01", "MAS"}  # the token and Alchemy sets and the old one are never asked about
    assert {code for code, _ in stored_sets(env)} == {"A01", "MAS"}


def test_a_set_with_a_game_file_in_one_format_only_is_in_that_formats_window_only(env, universe, small_files):
    add_set(universe, "AAA", "2026-08-01")
    add_set(universe, "BBB", "2026-07-01")
    publish(universe, "AAA", formats=("PremierDraft",))
    publish(universe, "BBB")
    report = run(universe)
    assert report["window"] == {"PremierDraft": ["AAA", "BBB"], "TradDraft": ["BBB"]}
    assert ("AAA", "TradDraft") not in stored_sets(env)


def test_a_set_with_a_game_file_but_no_draft_file_yet_is_in_the_window_and_loads_what_exists(env, universe, small_files):
    add_set(universe, "AAA", "2026-08-01")
    publish(universe, "AAA", formats=("PremierDraft",), kinds=("game",))
    report = run(universe)
    assert report["window"]["PremierDraft"] == ["AAA"]
    assert states(report) == {("PremierDraft", "game"): "loaded", ("PremierDraft", "draft"): "not_published"}


def test_a_file_withdrawn_from_a_stored_set_keeps_the_set_its_place_and_its_rows(env, universe, small_files):
    codes = month_sets(universe, 8)
    run(universe)
    universe.seventeenlands.unpublish("game", "A08", "PremierDraft")  # the newest set's game file is withdrawn
    report = run(universe)
    assert report["window"]["PremierDraft"] == list(reversed(codes)) and report["removed"] == []  # still in; nothing pushed out
    assert ("A08", "PremierDraft") in stored_sets(env, LimitedGameStat)
    assert file_status(report, "A08", "PremierDraft", "game") == "no_longer_published"


def test_nothing_is_deleted_when_the_window_could_not_be_worked_out(env, universe, small_files, napping):
    codes = month_sets(universe, 8)
    run(universe)
    add_set(universe, "A09", "2026-09-20")
    publish(universe, "A09")
    universe.seventeenlands.fail_next("/analysis_data/game_data/game_data_public.A09.PremierDraft.csv.gz", 500, times=10)
    with pytest.raises(SystemExit) as error:
        run(universe)
    assert "nothing was deleted" in str(error.value) and "A09 PremierDraft" in str(error.value)
    stored = stored_sets(env)
    assert set(stored) == {(c, fmt) for c in codes for fmt in BOTH} | {("A09", "TradDraft")}  # A01 is still there in both formats
    assert ("A09", "PremierDraft") not in stored  # the one that failed was not guessed at; the other format went on
    assert napping[-3:] == [2, 8, 30]


def test_nothing_is_deleted_when_nothing_is_published(env, universe, small_files):
    month_sets(universe, 2)
    run(universe)
    for code in ("A01", "A02"):
        for fmt in BOTH:
            for kind in ("game", "draft"):
                universe.seventeenlands.unpublish(kind, code, fmt)
    add_set(universe, "A03", "2026-03-01")  # not published either (403)
    report = run(universe)
    assert report["removed"] == [] and len(stored_sets(env)) == 4
    assert report["window"]["PremierDraft"] == ["A02", "A01"]  # the two stored ones keep their places; A03 is passed over


def test_a_failing_list_of_sets_stops_the_run_before_17lands_is_asked_about_anything(env, universe, small_files):
    month_sets(universe, 2)
    run(universe)
    universe.seventeenlands.calls.clear()

    def broken(request):
        if request.url.host == "api.scryfall.com":
            return httpx.Response(503, json={"object": "error", "status": 503, "code": "unavailable", "details": "try later"})
        return universe.handle(request)

    with pytest.raises(SystemExit, match="Scryfall's list of sets could not be read"):
        sync_limited.main(ARGS, transport=httpx.MockTransport(broken))
    assert universe.seventeenlands.calls == [] and len(stored_sets(env)) == 4


def test_naming_sets_reads_exactly_those_with_no_discovery_no_wait_and_nothing_deleted(env, universe, small_files):
    month_sets(universe, 2)
    add_set(universe, "NEW", "2026-10-09")
    publish(universe, "NEW")
    run(universe)
    before = len(universe.scryfall.calls)
    report = run(universe, args=["--sources", "limited_17lands", "--sets", "new,A01", "--formats", "TradDraft"])
    assert {(f["set"], f["format"]) for f in report["files"]} == {("NEW", "TradDraft"), ("A01", "TradDraft")}
    assert len(universe.scryfall.calls) == before and "window" not in report and "removed" not in report  # Scryfall was not asked
    assert ("A02", "PremierDraft") in stored_sets(env) and ("NEW", "TradDraft") in stored_sets(env)


def test_the_formats_default_to_both_and_one_can_still_be_asked_for(env, universe, small_files):
    month_sets(universe, 1)
    assert run(universe)["formats"] == list(BOTH) == list(sync_limited.DEFAULT_FORMATS)
    report = run(universe, "--formats", "PremierDraft")
    assert report["formats"] == ["PremierDraft"] and {f["format"] for f in report["files"]} == {"PremierDraft"}


def test_a_run_for_one_format_never_deletes_the_other_format(env, universe, small_files):
    month_sets(universe, 8)
    run(universe)
    add_set(universe, "A09", "2026-09-20")
    publish(universe, "A09")
    report = run(universe, "--formats", "PremierDraft")
    assert [(r["set"], r["format"]) for r in report["removed"]] == [("A01", "PremierDraft")]
    assert ("A01", "TradDraft") in stored_sets(env)


def test_the_per_run_cap_applies_to_the_window_too_the_rest_waits_for_the_next_run(env, universe, small_files, monkeypatch):
    month_sets(universe, 8)
    total = sum(len(f["body"]) for f in universe.seventeenlands.files.values())
    monkeypatch.setattr(sync_limited, "MAX_RUN_BYTES", total // 2)
    first = run(universe)
    counts = {s: sum(f["status"] == s for f in first["files"]) for s in ("loaded", "deferred")}
    assert counts["loaded"] > 0 and counts["deferred"] > 0 and counts["loaded"] + counts["deferred"] == 8 * 2 * 2
    got = sum(len(universe.seventeenlands.files[p]["body"]) for p in {c.path for c in universe.seventeenlands.calls if c.method == "GET"})
    assert got <= total // 2 + 1000  # the files read stay under the cap (a GET that reached it stopped there)
    monkeypatch.setattr(sync_limited, "MAX_RUN_BYTES", 800_000_000)
    second = run(universe)
    assert {f["status"] for f in second["files"]} <= {"loaded", "skipped"}
    assert sum(f["status"] == "loaded" for f in second["files"]) == counts["deferred"]
    assert len(stored_sets(env, LimitedSource)) == 16


def test_the_report_says_what_a_real_header_holds_the_column_names_and_where_pick_numbers_start_never_a_value(env, universe, small_files):
    add_set(universe, "AAA", "2026-08-01")
    publish(universe, "AAA", formats=("PremierDraft",))
    report = run(universe)
    game = next(f for f in report["files"] if f["kind"] == "game")["notes"]
    draft = next(f for f in report["files"] if f["kind"] == "draft")["notes"]
    assert game[0].endswith("of them per card") and game[1].startswith("other columns: expansion,")
    assert f"{len(GAMES.encode())} bytes after gunzip" in game  # the uncompressed size, which the first real run did not measure
    assert all(isinstance(f["seconds"], float) for f in report["files"])  # and the time each file took
    assert "won" in game[1] and "deck_Test Bear" not in game[1]
    assert "pick_number starts at 0" in draft  # the invented fixture is 0-based; the first real run could not print this


# -- the terms re-read ----------------------------------------------------------------------------------------------------------

def test_the_recorded_date_is_the_one_the_owner_set_and_the_gate_agrees():
    from tests.test_compliance_gate import gate_rows
    assert limited_terms.terms_read_on() == date(2026, 10, 9)
    assert gate_rows()["limited_17lands"][3] <= "2026-10-09"  # the gate row is the first reading, the line the latest


@pytest.mark.parametrize("day, runs", [(date(2026, 10, 9), True), (date(2027, 2, 6), True),   # 0 and 120 days
                                      (date(2027, 2, 7), False), (date(2027, 6, 1), False)])  # 121 and 235 days
def test_the_job_refuses_to_run_once_the_terms_were_read_more_than_120_days_ago(env, universe, small_files, monkeypatch, day, runs):
    month_sets(universe, 1)
    monkeypatch.setattr(limited_terms, "today", lambda: day)
    if runs:
        assert run(universe)["terms_read_days_ago"] == (day - date(2026, 10, 9)).days
        return
    with pytest.raises(SystemExit, match="refusing to run: the 17Lands terms were last read .* days ago .*2026-10-09") as error:
        run(universe)
    assert "Re-read the pages" in str(error.value)
    assert universe.seventeenlands.calls == [] and universe.scryfall.calls == [] and rows(env, LimitedSource) == []  # nothing asked, nothing stored
    with pytest.raises(SystemExit, match="refusing to run"):  # a manual run with sets named is refused too: there is no way around it
        run(universe, args=["--sources", "limited_17lands", "--sets", "A01"])


def test_the_job_refuses_when_the_date_line_is_missing_or_not_a_date(env, universe, small_files, monkeypatch, tmp_path):
    month_sets(universe, 1)
    path = tmp_path / "compliance.md"
    for text in ("no such line here", "17Lands terms read on: 2026-13-45"):
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(limited_terms, "COMPLIANCE", path)
        with pytest.raises(SystemExit, match="refusing to run"):
            run(universe)
    monkeypatch.setattr(limited_terms, "COMPLIANCE", tmp_path / "missing.md")
    with pytest.raises(SystemExit, match="refusing to run"):
        run(universe)
    assert universe.seventeenlands.calls == []


def test_a_disabled_source_prints_that_and_does_not_look_at_the_date(env, universe, monkeypatch):
    monkeypatch.setattr(limited_terms, "today", lambda: date(2030, 1, 1))
    assert sync_limited.main([], transport=universe.transport) == {}


def test_the_date_line_reads_with_or_without_bold_and_the_age_counts_days(tmp_path):
    path = tmp_path / "c.md"
    path.write_text("text\n\n**17Lands terms read on: 2026-07-01**\n", encoding="utf-8")
    assert limited_terms.terms_read_on(path) == date(2026, 7, 1) and limited_terms.age_days(path, date(2026, 10, 9)) == 100
    path.write_text("17Lands terms read on: 2026-07-01\n", encoding="utf-8")
    assert limited_terms.check(path, date(2026, 10, 29)) == 120
    with pytest.raises(limited_terms.TermsStale):
        limited_terms.check(path, date(2026, 10, 30))
