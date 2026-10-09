"""The Limited statistics answer (#178): GET /api/v1/catalog/limited/{set} and the MCP tool get_limited_card_stats.

Every figure carries its sample and the exact warning for its level, the attribution (17Lands, CC BY 4.0) comes first in the answer,
and the provenance keeps 17Lands' counts (a source) apart from the Vault's rates (computed). Data is loaded straight into the tables
(the job has its own tests); the numbers are invented."""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.test_agents import auth, call_tool, make_token, rpc
from tests.test_limited_stats import DRAFTS, GAMES, count_games, count_picks
from vault import limited_data
from vault.api.hal import encode_cursor
from vault import limited_stats as ls
from vault.models import LimitedGameStat, LimitedPickStat, LimitedSource

V1 = "/api/v1/catalog/limited"
MODIFIED = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def bot(app):
    with TestClient(app) as c:
        yield c


def load_fixtures(app, set_code="TST", fmt="PremierDraft"):
    with app.state.db.sessions() as db:
        for result in (count_games(), count_picks()):
            limited_data.replace_file(db, set_code, fmt, result, etag='"abc"', last_modified=MODIFIED, content_length=123, index={})
        db.commit()


def put_game(app, name, n_hand, wins_hand, *, opening=None, set_code="TST", fmt="PremierDraft", played=None):
    """One card with ``n_hand`` games in hand (``wins_hand`` of them won), split between the opening hand and later draws."""
    opening = n_hand // 2 if opening is None else opening
    won_open = wins_hand // 2
    with app.state.db.sessions() as db:
        db.add(LimitedGameStat(set_code=set_code, format=fmt, card_name=name, oracle_id=None, games_played=played or n_hand, wins_played=wins_hand,
                               opening=opening, wins_opening=won_open, drawn=n_hand - opening, wins_drawn=wins_hand - won_open,
                               in_hand=n_hand, wins_in_hand=wins_hand))
        db.commit()


def put_pick(app, name, seen, last_sum, picked, picked_sum, set_code="TST", fmt="PremierDraft"):
    with app.state.db.sessions() as db:
        db.add(LimitedPickStat(set_code=set_code, format=fmt, card_name=name, oracle_id=None, seen=seen, last_seen_sum=last_sum,
                               picked=picked, picked_sum=picked_sum))
        db.commit()


def put_source(app, kind="game", *, records=10000, wins=5600, set_code="TST", fmt="PremierDraft", first_picks=None, empty=None):
    with app.state.db.sessions() as db:
        db.add(LimitedSource(set_code=set_code, format=fmt, kind=kind, etag='"e"', last_modified=MODIFIED, content_length=1,
                             fetched_at=datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc), records=records, skipped_records=3,
                             wins=wins if kind == "game" else None, first_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
                             last_time=datetime(2026, 9, 30, tzinfo=timezone.utc), cards=3, unmatched_cards=0,
                             first_picks=first_picks, empty_first_picks=empty))
        db.commit()


@pytest.fixture
def sized(signed_in, app):
    """Cards at the edges of the sample levels (games in hand): 1000 ok, 999 low, 200 low, 199 too_few; baseline 56%."""
    put_source(app, "game")
    put_source(app, "draft", records=50000, first_picks=1000, empty=0)
    put_game(app, "Ok Card", 1000, 620)
    put_game(app, "Almost Ok", 999, 560)
    put_game(app, "Floor Card", 200, 120)
    put_game(app, "Just Under", 199, 130)
    put_game(app, "Higher Ok", 4000, 2600)
    for name, sums in (("Ok Card", (1200, 3000)), ("Floor Card", (199, 400))):
        put_pick(app, name, sums[0], sums[0] * 5, sums[1], sums[1] * 4, )
    return signed_in


def get(client, set_code="TST", **params):
    res = client.get(f"{V1}/{set_code}", params=params)
    return res


# -- access ---------------------------------------------------------------------------------------------------------------

def test_the_route_needs_a_signed_in_person(client, app):
    load_fixtures(app)
    res = client.get(f"{V1}/TST")
    assert res.status_code == 401 and "Bearer" in res.headers["www-authenticate"]


def test_the_data_is_global_every_signed_in_person_gets_the_same_answer(sized, app):
    first = get(sized).json()
    with TestClient(app) as other:
        assert other.post("/api/auth/dev-login").status_code == 200
        second = get(other).json()
    assert first["cards"] == second["cards"] and first["attribution"] == second["attribution"]
    source = open(limited_data.__file__, encoding="utf-8").read()
    assert "user_id" not in source and "User" not in source  # the route reads nothing of the person


def test_the_tables_hold_counts_only(app):
    """No row, draft id, game time, rank or deck is stored (design section 3): the columns are the aggregates and nothing else."""
    columns = {t.name: {c.name for c in t.columns} for t in (LimitedGameStat.__table__, LimitedPickStat.__table__, LimitedSource.__table__)}
    assert columns["limited_game_stats"] == {"set_code", "format", "card_name", "oracle_id", "games_played", "wins_played", "opening", "in_hand", "wins_in_hand",
                                             "wins_opening", "drawn", "wins_drawn"}
    assert columns["limited_pick_stats"] == {"set_code", "format", "card_name", "oracle_id", "seen", "last_seen_sum", "picked", "picked_sum"}
    assert not any(c.endswith("user_id") or "draft_id" in c or "deck" in c or c == "rank" for cols in columns.values() for c in cols)
    assert not {"user_id"} & columns["limited_sources"]


# -- the answer ---------------------------------------------------------------------------------------------------------------

def test_the_attribution_comes_first_and_names_the_set_the_format_and_the_dates(sized):
    body = get(sized).json()
    assert next(iter(body)) == "attribution"
    assert body["attribution"] == ls.attribution("TST", "PremierDraft", "2026-10-01", "2026-10-09")  # both files are of the same day
    assert body["platform"] == "MTG Arena" and body["set"] == "TST" and body["format"] == "PremierDraft"
    assert ls.CAVEAT in body["caveats"]
    window = body["data_window"]
    assert window["games"] == 10000 and window["baseline_win_rate"] == 0.56 and window["first_game"].startswith("2026-09-01")
    assert window["game_file"]["last_modified"] == "2026-10-01" and window["draft_file"]["last_modified"] == "2026-10-01"


def test_the_provenance_keeps_17lands_counts_and_the_vaults_rates_apart(sized):
    source, computed = get(sized).json()["provenance"]
    assert source["kind"] == "source" and source["source"] == "17Lands" and source["url"] == "https://www.17lands.com/public_datasets"
    assert source["licence"] == {"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"}
    assert source["changes"] == "Reduced to per-card counts; percentages, intervals and sample-size warnings computed by the Vault."
    assert source["as_of"] == "2026-10-01" and source["version"] == "TST PremierDraft: game file 2026-10-01, draft file 2026-10-01"
    assert source["notice"] == get(sized).json()["attribution"]
    assert "Wizards" not in source["notice"] and "Fan Content" not in source["notice"]  # card names are not claimed as Wizards' text here
    assert computed["kind"] == "computed" and computed["source"] == "The Vault" and computed["inputs"][0]["source"] == "17Lands"
    assert computed["notice"] is None and computed["licence"] is None  # computed values are not 17Lands' and carry no licence of theirs


def test_a_sorted_list_leaves_out_cards_under_200_games_in_hand_and_says_how_many(sized):
    body = get(sized).json()
    names = [c["name"] for c in body["cards"]]
    assert names == ["Higher Ok", "Ok Card", "Floor Card", "Almost Ok"]  # 65%, 62%, 60%, 56.1%; "Just Under" (199 games) is not in the list
    assert body["left_out"] == {"count": 1, "reason": "1 cards were left out of this ranking because they have fewer than 200 games in hand."}
    rates = [c["win_rate_in_hand"] for c in body["cards"]]
    assert rates == sorted(rates, reverse=True) and body["sort"] == "win_rate_in_hand" and body["total"] == 4


def test_the_level_and_the_warning_change_at_200_and_1000_games(sized):
    cards = {c["name"]: c for c in get(sized, cards=["Ok Card", "Almost Ok", "Floor Card", "Just Under"]).json()["cards"]}
    assert {n: c["sample"]["level"] for n, c in cards.items()} == {"Ok Card": "ok", "Almost Ok": "low", "Floor Card": "low", "Just Under": "too_few"}
    assert {n: c["sample"]["n"] for n, c in cards.items()} == {"Ok Card": 1000, "Almost Ok": 999, "Floor Card": 200, "Just Under": 199}
    assert cards["Ok Card"]["sample"]["warning"] is None
    assert cards["Almost Ok"]["sample"]["warning"].startswith("Small sample: 999 games in hand for Almost Ok in TST PremierDraft.")
    assert cards["Floor Card"]["sample"]["warning"].startswith("Small sample: 200 games in hand for Floor Card in TST PremierDraft.")
    assert cards["Just Under"]["sample"]["warning"] == ls.warning_for("Just Under", "TST", "PremierDraft", 130, 199)
    assert "It must not be used to rank or recommend this card." in cards["Just Under"]["sample"]["warning"]


def test_a_card_under_the_floor_still_shows_its_win_rate_flagged_with_its_range_and_the_baseline_gap(sized):
    card = get(sized, cards="Just Under").json()["cards"][0]
    assert card["games_in_hand"] == 199 and card["win_rate_in_hand"] == pytest.approx(130 / 199, abs=1e-4)
    low, high = card["win_rate_ci95"]
    assert low < card["win_rate_in_hand"] < high and card["vs_baseline_points"] == pytest.approx((130 / 199 - 0.56) * 100, abs=0.1)
    assert card["games_in_opening_hand"] == 99 and card["win_rate_opening_hand"] is None  # under the floor for its own sample: withheld


def test_a_big_sample_has_every_rate_the_interval_and_no_warning(sized):
    card = get(sized, cards="Higher Ok").json()["cards"][0]
    assert card["games_in_hand"] == 4000 and card["win_rate_in_hand"] == 0.65
    assert card["win_rate_opening_hand"] == 0.65 and card["win_rate_drawn"] == 0.65 and card["win_rate_played"] == 0.65
    assert card["sample"] == {"level": "ok", "n": 4000, "warning": None, "position_warning": None}  # no draft row: nothing to withhold


def test_positions_are_shown_at_200_packs_and_withheld_below(sized):
    cards = {c["name"]: c for c in get(sized, cards=["Ok Card", "Floor Card", "Higher Ok"]).json()["cards"]}
    ok = cards["Ok Card"]
    assert ok["times_seen"] == 1200 and ok["avg_last_seen_pick"] == 5.0 and ok["times_picked"] == 3000 and ok["avg_taken_at"] == 4.0
    assert ok["sample"]["position_warning"] is None
    floor = cards["Floor Card"]  # seen 199 times: the average is withheld, the count is not
    assert floor["times_seen"] == 199 and floor["avg_last_seen_pick"] is None and floor["avg_taken_at"] == 4.0
    assert floor["sample"]["position_warning"] == "Too few packs (199) to say where Floor Card is usually last seen or taken; the average is not shown."
    assert cards["Higher Ok"]["times_seen"] is None and cards["Higher Ok"]["avg_last_seen_pick"] is None  # no draft row for this card


def test_each_sort_orders_by_its_metric_and_applies_its_own_floor(sized):
    by_games = [c["name"] for c in get(sized, sort="games_in_hand").json()["cards"]]
    assert by_games == ["Higher Ok", "Ok Card", "Almost Ok", "Floor Card"]
    seen = get(sized, sort="avg_last_seen_pick").json()
    assert [c["name"] for c in seen["cards"]] == ["Ok Card"]  # only one card was seen 200 times or more
    assert seen["left_out"]["count"] == 4 and seen["left_out"]["reason"] == \
        "4 cards were left out of this ranking because they have fewer than 200 packs in which they were seen."
    taken = get(sized, sort="avg_taken_at").json()
    assert [c["name"] for c in taken["cards"]] == ["Floor Card", "Ok Card"] and taken["cards"][0]["avg_taken_at"] == 4.0  # tied: by name
    assert taken["left_out"]["reason"] == "3 cards were left out of this ranking because they have fewer than 200 picks."


def test_cards_are_found_by_name_or_by_either_face_and_the_rest_are_listed(signed_in, app):
    load_fixtures(app)
    body = get(signed_in, cards=["test bear", "Front Face", "Back Face", "Nonexistent Card", "Test Bear"]).json()
    assert [c["name"] for c in body["cards"]] == ["Test Bear", "Front Face // Back Face"]  # asked order; a repeat or both faces once
    assert body["not_found"] == ["Nonexistent Card"] and body["sort"] is None and body["left_out"]["count"] == 0
    bear = body["cards"][0]
    # 5 games with a copy in hand (3 with one in the opener, 3 with one drawn, one game with both counted once), 3 of them won
    assert bear["games_in_hand"] == 5 and bear["win_rate_in_hand"] == pytest.approx(3 / 5, abs=1e-4)
    assert bear["games_in_opening_hand"] == 3 and bear["games_drawn"] == 3 and bear["games_played"] == 6
    assert bear["sample"]["level"] == "too_few" and bear["sample"]["warning"].startswith("Only 5 games in hand for Test Bear in TST PremierDraft")


def test_the_sorted_list_is_paged_with_a_cursor_and_links(sized):
    first = get(sized, limit=2).json()
    assert [c["name"] for c in first["cards"]] == [c["name"] for c in get(sized).json()["cards"]][:2] and first["count"] == 2 and first["total"] == 4
    assert first["next_cursor"] and first["_links"]["next"]["href"].startswith("/api/v1/catalog/limited/TST?")
    second = sized.get(first["_links"]["next"]["href"]).json()
    assert [c["name"] for c in second["cards"]] == [c["name"] for c in get(sized).json()["cards"]][2:] and second["next_cursor"] is None
    assert get(sized, cursor="not-a-cursor").status_code == 400
    assert get(sized, cursor=encode_cursor(["x"])).status_code == 400  # a cursor of another shape


def test_an_unknown_set_or_format_says_what_is_loaded(sized):
    res = get(sized, "ZZZ")
    assert res.status_code == 404 and "no 17Lands data for ZZZ PremierDraft" in res.json()["detail"]
    assert "Loaded: TST PremierDraft (game file 2026-10-01, draft file 2026-10-01)." in res.json()["detail"]
    assert "TST TradDraft" in get(sized, format="TradDraft").json()["detail"]
    assert get(sized, "tst").status_code == 200  # the set code is not case sensitive


def test_with_nothing_loaded_the_answer_says_so(signed_in):
    assert "The Vault has no 17Lands data loaded yet." in get(signed_in).json()["detail"]


@pytest.mark.parametrize("params", [{"sort": "best"}, {"format": "Sealed"}, {"limit": 51}, {"limit": 0}, {"cards": ["x"] * 41}])
def test_bad_parameters_are_refused(sized, params):
    assert get(sized, **params).status_code == 422


def test_a_draft_file_with_many_missing_first_picks_adds_17lands_own_caveat(signed_in, app):
    put_source(app, "game")
    put_source(app, "draft", first_picks=1000, empty=15)
    put_game(app, "Ok Card", 1000, 600)
    caveats = get(signed_in).json()["caveats"]
    assert caveats == [ls.CAVEAT, ls.FIRST_PICK_CAVEAT]
    clean = get(signed_in)  # below 1%: not mentioned
    with app.state.db.sessions() as db:
        db.get(LimitedSource, ("TST", "PremierDraft", "draft")).empty_first_picks = 5
        db.commit()
    assert get(signed_in).json()["caveats"] == [ls.CAVEAT] and clean.status_code == 200


def test_when_the_two_files_have_different_dates_the_attribution_says_so(signed_in, app):
    put_source(app, "game")
    put_source(app, "draft")
    with app.state.db.sessions() as db:
        db.get(LimitedSource, ("TST", "PremierDraft", "draft")).last_modified = datetime(2026, 9, 20, tzinfo=timezone.utc)
        db.commit()
    put_game(app, "Ok Card", 1000, 600)
    body = get(signed_in).json()
    assert "17Lands files last updated game file 2026-10-01, draft file 2026-09-20," in body["attribution"]
    assert body["provenance"][0]["as_of"] == "2026-10-01"


# -- the MCP tool ---------------------------------------------------------------------------------------------------------------

def test_the_tool_is_listed_read_only_with_the_description_and_inputs_of_the_design(sized, bot):
    read = make_token(sized)
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}
    tool = tools["get_limited_card_stats"]
    assert tool["annotations"]["readOnlyHint"] is True
    assert "Win rates and pick positions for the cards of one Limited set, from 17Lands' public data (Magic Arena, CC BY 4.0)" in tool["description"]
    props = tool["inputSchema"]["properties"]
    assert set(props) == {"set", "format", "cards", "sort", "limit", "cursor"} and tool["inputSchema"]["required"] == ["set"]
    assert props["cards"]["maxItems"] == 40 and props["limit"]["maximum"] == 50
    assert props["format"]["enum"] == ["PremierDraft", "TradDraft"]


def test_the_tool_answers_with_the_attribution_the_sample_and_provenance_even_to_a_read_only_token(sized, bot):
    read = make_token(sized)
    result = call_tool(bot, read, "get_limited_card_stats", set="tst", cards=["Almost Ok", "Just Under"])
    assert result["isError"] is False, result["content"][0]["text"]
    body = result["structuredContent"]
    assert body["attribution"].startswith("Data from 17Lands (https://www.17lands.com/public_datasets), licensed under CC BY 4.0")
    assert [c["sample"]["level"] for c in body["cards"]] == ["low", "too_few"]
    assert [b["kind"] for b in body["provenance"]] == ["source", "computed"] and body["provenance"][0]["licence"]["name"] == "CC BY 4.0"
    paged = call_tool(bot, read, "get_limited_card_stats", set="TST", limit=2)["structuredContent"]
    again = call_tool(bot, read, "get_limited_card_stats", set="TST", limit=2, cursor=paged["next_cursor"])["structuredContent"]
    assert paged["count"] == 2 and again["count"] == 2 and again["next_cursor"] is None


def test_an_unknown_set_is_an_error_that_lists_what_is_loaded(sized, bot):
    read = make_token(sized)
    result = call_tool(bot, read, "get_limited_card_stats", set="ZZZ")
    assert result["isError"] is True and "Loaded: TST PremierDraft" in result["content"][0]["text"]
    bad = rpc(bot, "tools/call", {"name": "get_limited_card_stats", "arguments": {"set": "TST", "sort": "best"}}, read).json()
    assert bad["error"]["code"] == -32602  # not one of the sorts the tool lists


def test_the_server_instructions_and_the_prompts_carry_the_limited_rules(sized, bot):
    from vault.api.mcp import INSTRUCTIONS
    for needle in ("get_limited_card_stats", "17Lands", "CC BY 4.0", "below 200 games in hand never rank", "not paper Magic",
                   "never say a win rate shows the card causes wins", "17Lands does not endorse the Vault"):
        assert needle in " ".join(INSTRUCTIONS.split()), needle


def test_whoami_lists_the_loaded_sets_with_the_17lands_notice(signed_in, app, bot):
    load_fixtures(app)
    read = make_token(signed_in)
    data = call_tool(bot, read, "whoami")["structuredContent"]
    entry = data["data"]["sources"]["limited_17lands"]
    assert entry["sets"] == ["TST PremierDraft: game file 2026-10-01, draft file 2026-10-01"] and entry["rows"] == 8
    block = next(b for b in data["provenance"] if b["source"] == "17Lands")
    assert block["licence"]["name"] == "CC BY 4.0" and block["notice"].startswith("Data from 17Lands") and block["changes"]
    assert "Not produced or endorsed by 17Lands." in block["notice"]
    json.dumps(data)


# -- review of #418: the cursor, the descriptions ----------------------------------------------------------------------------------

def test_a_refresh_between_two_pages_neither_skips_nor_repeats_a_card(sized, app):
    """The cursor is a keyset (the metric's value and the card's name): after page one (Higher Ok, Ok Card) the weekly refresh adds a card
    that ranks first and changes a card that is still to come. An item offset would now repeat Ok Card on page two."""
    first = get(sized, limit=2).json()
    assert [c["name"] for c in first["cards"]] == ["Higher Ok", "Ok Card"]
    put_game(app, "Fresh Top", 3000, 2400)  # 80%: ranks before everything on page one
    with app.state.db.sessions() as db:
        row = db.get(LimitedGameStat, ("TST", "PremierDraft", "Almost Ok"))
        row.opening, row.drawn, row.in_hand, row.wins_in_hand = 500, 499, 999, 540  # still last, changed
        db.commit()
    second = sized.get(first["_links"]["next"]["href"]).json()
    assert [c["name"] for c in second["cards"]] == ["Floor Card", "Almost Ok"]  # no repeat of page one, none of the rest skipped
    assert second["cards"][1]["games_in_hand"] == 999 and second["next_cursor"] is None


def test_the_cursor_is_the_metric_and_the_name_not_an_offset(sized):
    from vault.api.hal import decode_cursor
    cursor = get(sized, limit=1).json()["next_cursor"]
    assert decode_cursor(cursor) == [-0.65, "Higher Ok"]
    by_games = get(sized, limit=1, sort="games_in_hand").json()["next_cursor"]
    assert decode_cursor(by_games) == [-4000, "Higher Ok"]


def test_the_description_and_the_sort_input_say_which_sample_each_sort_uses(sized, bot):
    read = make_token(sized)
    tool = {t["name"]: t for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}["get_limited_card_stats"]
    text = " ".join(tool["description"].split())
    assert "games in hand for win_rate_in_hand and games_in_hand, packs seen for avg_last_seen_pick, picks for avg_taken_at" in text
    assert "a card under 200 games in hand is left out of a sorted list" not in text  # the old, wrong sentence
    sort = " ".join(tool["inputSchema"]["properties"]["sort"]["description"].split())
    assert "only cards with 200 or more games in hand" in sort and "seen in 200 or more packs" in sort and "picked 200 or more times" in sort
    from pathlib import Path
    api = " ".join((Path(__file__).parent.parent / "docs" / "api.md").read_text(encoding="utf-8").split())
    assert "packs seen (`times_seen`) for `avg_last_seen_pick`, picks (`times_picked`) for `avg_taken_at`" in api


def test_games_in_hand_is_the_union_stored_not_opening_plus_drawn(signed_in, app):
    """A card in hand in 6 games (2 opened, 3 drawn, 1 both): in_hand is 6 where opening + drawn would say 7."""
    put_source(app, "game")
    put_game(app, "Union Card", 6, 4, opening=3)
    with app.state.db.sessions() as db:
        row = db.get(LimitedGameStat, ("TST", "PremierDraft", "Union Card"))
        row.opening, row.drawn = 3, 4  # 7 together
        db.commit()
    card = get(signed_in, cards="Union Card").json()["cards"][0]
    assert card["games_in_hand"] == 6 and card["games_in_opening_hand"] == 3 and card["games_drawn"] == 4
    assert card["win_rate_in_hand"] == pytest.approx(4 / 6, abs=1e-4)
