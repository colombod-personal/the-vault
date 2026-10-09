"""The mana curve simulator (vault/simulate.py, #137): repeatable, and right on decks whose answer is obvious."""

from types import SimpleNamespace

import pytest

from vault.simulate import ASSUMPTIONS, SimCard, from_oracle, simulate

LAND = SimCard("Forest", land=True)


def deck(lands: int, spells: list[SimCard], size: int) -> list[SimCard]:
    filler = [SimCard("Bear", cmc=2)] * (size - lands - len(spells))
    return [LAND] * lands + spells + filler


def test_same_seed_same_answer_and_assumptions_are_listed():
    d = deck(37, [], 100)
    a, b = simulate(d, multiplayer=True, seed=7), simulate(d, multiplayer=True, seed=7)
    assert a == b and a["assumptions"] == ASSUMPTIONS


def test_stable_across_seeds_at_1000_games():
    d = deck(37, [SimCard("Ogre", cmc=3)] * 20, 100)
    values = [simulate(d, multiplayer=True, seed=s)["headline"]["five_mana_by_turn_5"] for s in range(4)]
    assert max(values) - min(values) < 6  # percentage points


def test_a_deck_of_lands_makes_every_land_drop():
    out = simulate([LAND] * 100, multiplayer=True, seed=1)
    assert out["headline"]["missed_a_land_drop_by_turn_4"] == 0 and out["per_turn"][4]["mana_available"] == 5


def test_ten_lands_rarely_reach_five_mana_by_turn_five():
    assert simulate(deck(10, [], 100), multiplayer=True, seed=1)["headline"]["five_mana_by_turn_5"] < 10


def test_a_deck_of_seven_drops_has_to_discard_far_more_than_a_balanced_deck():
    titans = simulate(deck(30, [SimCard("Titan", cmc=7)] * 70, 100), multiplayer=True, seed=1)["headline"]
    balanced = simulate(deck(37, [SimCard("Ogre", cmc=3)] * 30, 100), multiplayer=True, seed=1)["headline"]
    assert titans["discarded_by_turn_5"] > 40 and titans["discarded_by_turn_5"] > 3 * balanced["discarded_by_turn_5"]


def test_no_maximum_hand_size_means_no_discard_and_says_why():
    tower = SimCard("Reliquary Tower", land=True, no_max_hand=True, plan=("no maximum hand size",))
    d = [tower] + deck(29, [SimCard("Titan", cmc=7)] * 70, 99)
    out = simulate(d, commander=SimCard("Big Commander", cmc=6), multiplayer=True, seed=3)
    assert "Reliquary Tower: no maximum hand size" in out["discard_may_be_the_plan"]


def test_on_the_play_skips_the_first_draw_only_in_two_player_games():
    d = deck(24, [], 60)
    duel = simulate(d, on_the_play=True, multiplayer=False, samples=1, seed=2)["samples"][0]["turns"][0]
    edh = simulate(deck(37, [], 100), on_the_play=True, multiplayer=True, samples=1, seed=2)["samples"][0]["turns"][0]
    assert duel["drew"] is None and edh["drew"] is not None


def test_ramp_is_read_from_card_text():
    card = lambda **k: SimpleNamespace(**{"name": "x", "cmc": 1, "type_line": "Artifact", "oracle_text": "", **k})  # noqa: E731
    assert from_oracle(card(name="Sol Ring", oracle_text="{T}: Add {C}{C}.")).rock == 2
    assert from_oracle(card(name="Arcane Signet", oracle_text="{T}: Add one mana of any color in your commander's color identity.")).rock == 1
    assert from_oracle(card(name="Llanowar Elves", type_line="Creature — Elf Druid", oracle_text="{T}: Add {G}.")).dork == 1
    assert from_oracle(card(name="Cultivate", type_line="Sorcery", cmc=3, oracle_text="Search your library for up to two basic land "
                            "cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.")).ramp_lands == 1
    assert from_oracle(card(name="Guildgate", type_line="Land — Gate", oracle_text="This land enters tapped.")).tapped
    assert from_oracle(card(name="Tower", type_line="Land", oracle_text="You have no maximum hand size.")).no_max_hand


def test_too_small_a_deck_is_refused():
    with pytest.raises(ValueError):
        simulate([LAND] * 8, turns=6)


def test_playing_a_land_with_no_maximum_hand_size_stops_the_discards():
    """#137 (audit): only a cast spell lifted the limit; a land that is played (Reliquary Tower) never did, so the
    numbers still counted discards while the text said discarding was the plan."""
    tower = SimCard("Reliquary Tower", land=True, no_max_hand=True)
    plain = SimCard("Plain Land", land=True)
    big = [SimCard("Titan", cmc=9)] * 60
    with_tower = simulate([tower] * 40 + big, multiplayer=True, seed=5)["headline"]["discarded_by_turn_5"]
    without = simulate([plain] * 40 + big, multiplayer=True, seed=5)["headline"]["discarded_by_turn_5"]
    assert without > 0 and with_tower == 0


# -- #137: "a 40-land 100-card deck makes every land drop" ---------------------------------------------------------
# As worded this cannot be true of any shuffled deck: with 40 lands in 100 cards a miss is possible (and the simulation, like the
# real game, shows one in about five games by turn 4). The audit found the only test used 100 lands, which makes every drop
# trivially. What can be checked, and is checked here with 40 lands: the early drops are all but certain, the chance of
# missing one later matches the exact odds, and more lands means fewer misses.

def hypergeometric_at_least(lands: int, deck: int, drawn: int, k: int) -> float:
    from math import comb
    return sum(comb(lands, i) * comb(deck - lands, drawn - i) for i in range(k, min(lands, drawn) + 1)) / comb(deck, drawn)


def forty_lands(seed: int, **kwargs) -> dict:
    return simulate(deck(40, [], 100), multiplayer=True, seed=seed, games=5000, turns=6, **kwargs)


def test_a_40_land_100_card_deck_makes_the_first_two_land_drops_all_but_always():
    for seed in (1, 2, 3):
        turns = forty_lands(seed)["per_turn"]
        assert turns[0]["all_land_drops_so_far"] >= 99.5 and turns[1]["all_land_drops_so_far"] >= 99.5, seed


def test_a_40_land_100_card_deck_can_miss_a_drop_and_the_miss_rate_matches_the_exact_odds():
    """The mulligan rate is exactly the chance a 7-card hand does not hold 2 to 5 lands; the chance of making every drop to
    turn N is at least the exact no-mulligan odds of N lands in the first 7+N cards (mulligans only help here)."""
    from math import comb

    p = lambda k: comb(40, k) * comb(60, 7 - k) / comb(100, 7)  # noqa: E731
    exact_mulligan = 100 * (p(0) + p(1) + p(6) + p(7))
    for seed in (1, 2, 3):
        out = forty_lands(seed)
        assert abs(out["headline"]["mulligan_rate"] - exact_mulligan) < 1.5, (seed, out["headline"]["mulligan_rate"], exact_mulligan)
        for turn in (3, 4, 5, 6):
            made_all = out["per_turn"][turn - 1]["all_land_drops_so_far"]
            floor = 100 * hypergeometric_at_least(40, 100, 7 + turn, turn)  # on the draw / multiplayer: 7 + one draw a turn
            assert floor - 1.5 < made_all < floor + 12, (seed, turn, made_all, floor)
        by_turn_4 = out["per_turn"][3]["all_land_drops_so_far"]
        assert 75 < by_turn_4 < 86  # about one game in five misses a drop by turn 4: "every drop" is not what 40 of 100 gives
        assert abs(out["headline"]["missed_a_land_drop_by_turn_4"] - (100 - by_turn_4)) < 0.2


def test_more_lands_means_fewer_missed_drops():
    seven = lambda lands: simulate(deck(lands, [], 100), multiplayer=True, seed=5, games=3000, turns=6)["per_turn"]  # noqa: E731
    thirty, forty, hundred = seven(30), seven(40), seven(100)
    for turn in range(2, 6):
        assert thirty[turn]["all_land_drops_so_far"] < forty[turn]["all_land_drops_so_far"] < hundred[turn]["all_land_drops_so_far"] + 0.001
    assert hundred[5]["all_land_drops_so_far"] == 100.0  # 100 lands: every drop, which is all the old test showed


def test_the_margin_is_what_a_percentage_can_be_off_at_this_many_games():
    """#138: the page prints 'good to about 3 points either way' from this field instead of computing it (95%, worst case)."""
    from vault.simulate import margin_points
    assert margin_points(1000) == 3.1 and margin_points(200) == 6.9 and margin_points(5000) == 1.4 and margin_points(1) == 98.0
    d = deck(37, [SimCard("Ogre", cmc=3)] * 20, 100)
    for games in (200, 1000):
        out = simulate(d, multiplayer=True, games=games, seed=3)
        assert out["margin_points"] == margin_points(games)
        # the claim itself: two runs of the same deck differ by no more than twice the margin (each is within it of the truth)
        other = simulate(d, multiplayer=True, games=games, seed=4)
        assert abs(out["headline"]["five_mana_by_turn_5"] - other["headline"]["five_mana_by_turn_5"]) <= 2 * out["margin_points"]
