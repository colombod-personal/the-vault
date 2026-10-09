"""The arithmetic, the sample-size rules and the two counters behind the Limited statistics (#178, docs/limited-data-design.md).

The fixtures are small invented files in the shape of 17Lands' public data (``tests/fixtures/limited_*_sample.csv``): the repository
holds no 17Lands data. Hand-counted numbers are in the comments next to the assertions."""

import csv
import io
from pathlib import Path

import pytest

from vault import limited_stats as ls

FIXTURES = Path(__file__).parent / "fixtures"
GAMES = (FIXTURES / "limited_game_sample.csv").read_text(encoding="utf-8")
DRAFTS = (FIXTURES / "limited_draft_sample.csv").read_text(encoding="utf-8")


def rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


def count_games(text: str = GAMES, set_code: str = "TST") -> ls.FileResult:
    header, *body = rows(text)
    counter = ls.GameCounter(header, set_code)
    for row in body:
        counter.feed(row)
    return counter.result()


def count_picks(text: str = DRAFTS, set_code: str = "TST") -> ls.FileResult:
    header, *body = rows(text)
    counter = ls.PickCounter(header, set_code)
    for row in body:
        counter.feed(row)
    return counter.result()


def rewrite(text: str, edit) -> str:
    """The same file after ``edit(header, body)`` changed its rows."""
    header, *body = rows(text)
    header, body = edit(header, body)
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="\n").writerows([header, *body])
    return out.getvalue()


# -- arithmetic -----------------------------------------------------------------------------------------------------------

def test_the_wilson_interval_matches_the_values_the_design_quotes():
    """Design section 7: at a win rate near 55% the 95% interval is about +-6.9 points at 200 games, 4.4 at 500, 3.1 at 1,000 and
    1.0 at 10,000."""
    for n, half in ((200, 6.9), (500, 4.4), (1000, 3.1), (10000, 1.0)):
        low, high = ls.wilson(round(n * 0.55), n)
        assert (high - low) * 100 / 2 == pytest.approx(half, abs=0.15), n
    low, high = ls.wilson(0, 10)  # an extreme rate stays inside 0..1 (the normal approximation would not)
    assert low == 0.0 and 0.2 < high < 0.35
    assert ls.wilson(0, 0) == (0.0, 1.0)


@pytest.mark.parametrize("n, expected", [(0, "too_few"), (199, "too_few"), (200, "low"), (999, "low"), (1000, "ok"), (50000, "ok")])
def test_the_levels_change_at_200_and_1000_games(n, expected):
    assert ls.level(n) == expected


def test_the_warning_for_too_few_games_is_the_exact_text():
    text = ls.warning_for("Test Bolt", "TST", "PremierDraft", 110, 199)
    assert text == ("Only 199 games in hand for Test Bolt in TST PremierDraft: too few to say whether it wins more or less than other "
                    "cards. The win rate (55.3%, 95% range 48.3% to 62.0%) is shown for completeness. It must not be used to rank or "
                    "recommend this card.")


def test_the_warning_for_a_small_sample_is_the_exact_text():
    text = ls.warning_for("Test Bolt", "TST", "PremierDraft", 110, 200)
    assert text == ("Small sample: 200 games in hand for Test Bolt in TST PremierDraft. Its true win rate could be anywhere from 48.1% "
                    "to 61.7% (95% range). Do not treat a difference of less than 13.7 points from another card as real.")


def test_there_is_no_warning_at_one_thousand_games():
    assert ls.warning_for("Test Bolt", "TST", "PremierDraft", 550, 1000) is None


def test_the_position_warning_names_the_smaller_sample_and_is_absent_when_both_reach_the_floor():
    assert ls.positions_warning("Test Bear", 150, 900) == "Too few packs (150) to say where Test Bear is usually last seen or taken; the average is not shown."
    assert ls.positions_warning("Test Bear", 900, 40) == "Too few packs (40) to say where Test Bear is usually last seen or taken; the average is not shown."
    assert ls.positions_warning("Test Bear", 200, 200) is None


def test_the_left_out_sentence_and_the_caveat_are_the_exact_text():
    assert ls.WARN_LEFT_OUT.format(k=7, floor=ls.SHOW_FLOOR, what="games in hand") == \
        "7 cards were left out of this ranking because they have fewer than 200 games in hand."
    assert ls.CAVEAT == ("17Lands data comes from Magic Arena players who use the 17Lands tracker, not from all players or from paper. "
                         "A win rate in hand does not show that the card causes wins.")


def test_the_attribution_names_the_creator_the_licence_the_changes_and_the_dates():
    text = ls.attribution("HOB", "PremierDraft", "2026-10-01", "2026-10-09")
    assert text == (
        "Data from 17Lands (https://www.17lands.com/public_datasets), licensed under CC BY 4.0 "
        "(https://creativecommons.org/licenses/by/4.0/), which also states that it is provided without warranty (section 5). "
        "Magic Arena games and drafts, set HOB, format PremierDraft; 17Lands files last updated 2026-10-01, read by the Vault on 2026-10-09. "
        "Changed by the Vault: the per-game and per-pick rows were reduced to per-card counts, and the percentages, intervals and "
        "sample-size warnings were computed by the Vault, so they can differ from the figures on 17lands.com. "
        "Not produced or endorsed by 17Lands.")


# -- the game counter -------------------------------------------------------------------------------------------------------

def test_the_game_counter_counts_copies_by_deck_opening_hand_and_draw_and_their_wins():
    result = count_games()
    assert (result.records, result.skipped, result.wins) == (6, 1, 4)  # 7 games; game 5 is inconsistent; games 1, 3, 4, 7 are won
    # [games_played, wins_played, opening, wins_opening, drawn, wins_drawn]
    assert result.cards["Test Bear"] == [12, 8, 4, 4, 3, 1]
    assert result.cards["Test Bolt"] == [5, 3, 1, 0, 2, 2]
    assert result.cards["Test Elf"] == [5, 3, 1, 1, 1, 1]
    assert result.cards["Front Face // Back Face"] == [5, 3, 1, 1, 2, 1]


def test_a_game_with_more_copies_in_hand_than_in_the_deck_and_sideboard_is_left_out_of_every_count():
    """Game 5 has Test Bolt twice in hand with one in the deck: its Test Bear copies must not be counted either."""
    with_it = count_games()
    without = count_games(rewrite(GAMES, lambda h, b: (h, [r for r in b if r[2] != "game-draft-5"])))
    assert with_it.cards == without.cards and without.skipped == 0 and without.records == 6


def test_tutored_copies_are_not_counted_as_drawn():
    """Game 6 has one Test Bear drawn and one tutored: only the drawn one counts (drawn total 3 = games 1, 2 and 6)."""
    assert count_games().cards["Test Bear"][4] == 3


def test_the_times_of_the_first_and_last_game_used_are_kept_and_nothing_else_about_a_game():
    result = count_games()
    assert result.first_time.isoformat() == "2026-09-10T10:00:00+00:00" and result.last_time.isoformat() == "2026-09-14T16:00:00+00:00"
    assert not hasattr(result, "rows") and set(vars(result)) == {"kind", "cards", "records", "skipped", "wins", "first_time", "last_time",
                                                                   "empty_first_picks", "first_picks", "notes"}


def test_a_card_that_never_reached_a_deck_or_a_hand_is_not_kept():
    def add_card(header, body):
        header = header + ["deck_Unplayed", "opening_hand_Unplayed", "drawn_Unplayed"]
        return header, [r + ["0", "0", "0"] for r in body]
    assert "Unplayed" not in count_games(rewrite(GAMES, add_card)).cards


def test_a_game_file_for_another_set_is_refused():
    with pytest.raises(ls.FileError, match="expansion"):
        count_games(set_code="OTH")


@pytest.mark.parametrize("drop", ["won", "expansion", "deck_Test Bear", "drawn_Test Bear"])
def test_a_game_file_without_the_columns_it_needs_is_refused_and_says_which(drop):
    def remove(header, body):
        keep = [i for i, name in enumerate(header) if name != drop]
        return [header[i] for i in keep], [[r[i] for i in keep] for r in body]
    with pytest.raises(ls.FileError) as error:
        count_games(rewrite(GAMES, remove))
    assert drop.split("_")[0] in str(error.value) or drop in str(error.value)


def test_a_game_file_with_no_card_columns_is_refused():
    header = ["expansion", "won", "game_time"]
    with pytest.raises(ls.FileError, match=r"deck_<card>"):
        ls.GameCounter(header, "TST")


# -- the pick counter -------------------------------------------------------------------------------------------------------

def test_the_pick_counter_counts_last_seen_positions_once_per_pack_round_and_stores_them_one_based():
    result = count_picks()
    assert (result.records, result.kind) == (9, "draft")
    # [seen, last_seen_sum, picked, picked_sum]; the file counts picks from 0, the counts are 1-based
    assert result.cards["Test Bear"] == [3, 12, 3, 12]  # seen in 3 pack rounds; in draft-a's first round at picks 1, 2, 3 and 9 (the wheel): 9, once
    assert result.cards["Test Bolt"] == [3, 4, 3, 4]
    assert result.cards["Test Elf"] == [2, 5, 2, 5]
    assert result.cards["Front Face // Back Face"] == [2, 6, 1, 3]


def test_a_file_with_one_based_pick_numbers_gives_the_same_counts():
    def one_based(header, body):
        i, j = header.index("pack_number"), header.index("pick_number")
        return header, [r[:i] + [str(int(r[i]) + 1)] + r[i + 1:j] + [str(int(r[j]) + 1)] + r[j + 1:] for r in body]
    assert count_picks(rewrite(DRAFTS, one_based)).cards == count_picks().cards


def test_a_file_whose_pick_numbers_start_somewhere_else_is_refused():
    def shifted(header, body):
        j = header.index("pick_number")
        return header, [r[:j] + [str(int(r[j]) + 5)] + r[j + 1:] for r in body]
    with pytest.raises(ls.FileError, match="pick numbers start at 5"):
        count_picks(rewrite(DRAFTS, shifted))


def test_drafts_that_are_not_contiguous_are_refused():
    """The last-seen computation reads one draft at a time: draft-a coming back after draft-b would be counted twice."""
    def interleave(header, body):
        return header, [body[6], *body[:6], *body[7:]]
    with pytest.raises(ls.FileError, match="not contiguous"):
        count_picks(rewrite(DRAFTS, interleave))


def test_a_missing_first_pick_is_counted_so_the_answer_can_say_so():
    def empty_first_pack(header, body):
        cols = [i for i, name in enumerate(header) if name.startswith("pack_card_")]
        for row in body:
            if row[header.index("draft_id")] == "draft-b" and row[header.index("pick_number")] == "0":
                for i in cols:
                    row[i] = "0"
        return header, body
    result = count_picks(rewrite(DRAFTS, empty_first_pack))
    assert (result.first_picks, result.empty_first_picks) == (3, 1)
    assert (count_picks().first_picks, count_picks().empty_first_picks) == (3, 0)


def test_a_draft_file_for_another_set_or_without_its_columns_is_refused():
    with pytest.raises(ls.FileError, match="expansion"):
        count_picks(set_code="OTH")
    with pytest.raises(ls.FileError, match="pack_card"):
        ls.PickCounter(["expansion", "draft_id", "pack_number", "pick_number", "pick"], "TST")


# -- the sanity checks ------------------------------------------------------------------------------------------------------

def test_a_small_file_fails_the_sanity_checks_with_the_real_thresholds():
    always, unless = ls.sanity_problems(count_games())
    assert any("only 6 games" in p for p in always) and any("only 4 cards" in p for p in always) and unless == []


def test_an_implausible_baseline_win_rate_is_refused():
    result = ls.FileResult("game", {f"c{i}": [1, 1, 1, 1, 1, 1] for i in range(200)}, records=5000, wins=4500)
    always, _ = ls.sanity_problems(result)
    assert any("outside 40% to 70%" in p for p in always)
    assert ls.sanity_problems(ls.FileResult("game", result.cards, records=5000, wins=2800))[0] == []


def test_a_file_that_would_drop_the_stored_cards_or_games_is_flagged_unless_forced():
    cards = {f"c{i}": [1, 0, 1, 0, 1, 0] for i in range(150)}
    result = ls.FileResult("game", cards, records=2000, wins=1100)
    always, unless = ls.sanity_problems(result, {"cards": 200, "records": 5000})
    assert always == [] and len(unless) == 2  # 150 of 200 cards is -25%, 2,000 of 5,000 games is -60%
    assert ls.sanity_problems(result, {"cards": 160, "records": 3000})[1] == []
