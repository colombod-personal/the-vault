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
