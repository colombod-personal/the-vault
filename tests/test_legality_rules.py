"""#428: what deck_legality applies, against the Comprehensive Rules (read live 2026-10-10, edition 2026-09-25):
100.2a (constructed: at least 60 cards, four of a name), 100.4a (a sideboard of at most 15; the four-card limit counts deck and
sideboard together), 903.5a (Commander: exactly 100 cards including the commander), 903.5b (one of each name), 903.5c (colour identity).
The companion starts outside the deck."""

from mtg_toolkits import decklist

from vault import deck_tools as dt
from vault.models import OracleCard


def card(name, identity=(), text="", type_line="Creature", legal=("modern", "commander", "legacy", "pauper", "brawl")):
    return OracleCard(oracle_id=name, name=name, type_line=type_line, oracle_text=text, color_identity=list(identity),
                      legalities={f: "legal" for f in legal})


def deck(*parts):
    """parts: (section, quantity, card)"""
    return dt.Resolved(entries=[dt.Entry(line=decklist.DeckLine(quantity=q, name=c.name, section=s), card=c) for s, q, c in parts])


BOLT = card("Lightning Bolt", "R", "Lightning Bolt deals 3 damage to any target.", "Instant")
FOREST = card("Forest", "G", "", "Basic Land — Forest")
KEEPER = card("Keeper", "G")


def kinds(result):
    return [(i["kind"], i["card"]) for i in result["issues"]]


def test_four_copies_in_the_deck_and_one_in_the_sideboard_is_five_copies():
    """Rule 100.4a: the four-card limit applies to the combined deck and sideboard. Before #428 the sideboard was not counted."""
    result = dt.legality(deck(("main", 4, BOLT), ("main", 56, FOREST), ("sideboard", 1, BOLT)), "modern")
    assert ("too_many_copies", "Lightning Bolt") in kinds(result)
    detail = next(i["detail"] for i in result["issues"] if i["kind"] == "too_many_copies")
    assert "5 copies counting the sideboard, at most 4" in detail


def test_three_in_the_deck_and_one_in_the_sideboard_is_fine_and_the_sideboard_still_has_its_own_limit_of_fifteen():
    ok = dt.legality(deck(("main", 3, BOLT), ("main", 57, FOREST), ("sideboard", 1, BOLT)), "modern")
    assert ok["legal"] is True
    big = dt.legality(deck(("main", 60, FOREST), ("sideboard", 16, FOREST)), "modern")
    assert ("sideboard_size", None) in kinds(big)


def test_a_companion_is_not_part_of_the_sixty_or_the_hundred_but_is_one_of_the_fifteen():
    sixty_with_companion_only = dt.legality(deck(("main", 59, FOREST), ("companion", 1, KEEPER)), "modern")
    assert ("deck_size", None) in kinds(sixty_with_companion_only)  # 59 in the deck: the companion does not make it 60
    commander = card("Boss", "G", type_line="Legendary Creature")
    hundred = deck(("commander", 1, commander), ("main", 99, FOREST), ("companion", 1, KEEPER))
    assert ("deck_size", None) not in kinds(dt.legality(hundred, "commander"))  # 100 in the deck, the companion outside it
    sideboard = dt.legality(deck(("main", 60, FOREST), ("sideboard", 15, FOREST), ("companion", 1, KEEPER)), "modern")
    assert ("sideboard_size", None) in kinds(sideboard)  # 15 + the companion = 16


def test_brawl_is_exactly_one_hundred_cards_like_commander():
    commander = card("Boss", "G", type_line="Legendary Creature")
    short = dt.legality(deck(("commander", 1, commander), ("main", 59, FOREST)), "brawl")
    assert ("deck_size", None) in kinds(short)
    full = dt.legality(deck(("commander", 1, commander), ("main", 99, FOREST)), "brawl")
    assert ("deck_size", None) not in kinds(full)


def test_the_answer_says_what_it_applied_and_no_longer_lists_a_check_it_now_makes():
    result = dt.legality(deck(("main", 60, FOREST)), "modern")
    assert any("rule 100.4a" in line for line in result["checked"]) and any("at least 60" in line for line in result["checked"])
    assert not any("up to N copies" in line for line in result["not_checked"])
    commander = dt.legality(deck(("commander", 1, card("Boss", "G")), ("main", 99, FOREST)), "commander")
    assert any("exactly 100" in line and "903.5a" in line for line in commander["checked"])


def test_nine_nazgul_in_a_commander_deck_are_legal_and_ten_are_not():
    nazgul = card("Nazgûl", "B", "A deck can have up to nine cards named Nazgûl.")
    boss = card("Boss", "B", type_line="Legendary Creature")
    nine = dt.legality(deck(("commander", 1, boss), ("main", 9, nazgul), ("main", 90, FOREST)), "commander")
    ten = dt.legality(deck(("commander", 1, boss), ("main", 10, nazgul), ("main", 89, FOREST)), "commander")
    assert ("too_many_copies", "Nazgûl") not in kinds(nine)
    assert ("too_many_copies", "Nazgûl") in kinds(ten)


def test_a_commander_must_be_a_legendary_creature_vehicle_or_say_it_can_be_one():
    """Rule 903.3 and 903.3a."""
    plain = card("Elf", "G")  # not legendary
    boss = card("Boss", "G", type_line="Legendary Creature — Elf")
    ship = card("Vessel", "G", type_line="Legendary Artifact — Vehicle")
    spell = card("Wrath", "R", type_line="Legendary Sorcery")
    walker = card("Jace", "U", "+1: Draw a card.", "Legendary Planeswalker — Jace")
    liege = card("Grand Duke", "B", "Grand Duke can be your commander.", "Planeswalker — Duke")
    rest = ("main", 99, FOREST)

    def run(c, fmt="commander"):
        return dt.legality(deck(("commander", 1, c), rest), fmt)

    assert ("commander_not_eligible", "Elf") in kinds(run(plain))
    assert ("commander_not_eligible", "Wrath") in kinds(run(spell))
    assert ("commander_not_eligible", "Jace") in kinds(run(walker))
    assert ("commander_not_eligible", "Boss") not in kinds(run(boss)) and ("commander_not_eligible", "Vessel") not in kinds(run(ship))
    assert ("commander_not_eligible", "Grand Duke") not in kinds(run(liege))  # 903.3a
    assert ("commander_not_eligible", "Jace") not in kinds(run(walker, "brawl"))  # Wizards' Brawl page: creature or Planeswalker


def test_two_commanders_need_a_partner_ability_on_each_and_never_more_than_two():
    """Rule 702.124a and 702.124g."""
    a = card("Thrasios", "GU", "Partner\nSomething.", "Legendary Creature — Merfolk")
    b = card("Tymna", "WB", "Partner\nSomething.", "Legendary Creature — Human")
    solo = card("Solo", "G", "Flying", "Legendary Creature — Bird")
    hero = card("Hero", "W", "Choose a Background", "Legendary Creature — Human")
    back = card("Folk Hero", "W", "Something.", "Legendary Enchantment — Background")
    third = card("Third", "R", "Partner", "Legendary Creature — Goblin")
    rest = ("main", 98, FOREST)
    ok = dt.legality(deck(("commander", 1, a), ("commander", 1, b), rest), "commander")
    assert not [k for k in kinds(ok) if k[0].startswith("commander_")]
    bad = dt.legality(deck(("commander", 1, a), ("commander", 1, solo), rest), "commander")
    assert ("commander_pair", "Solo") in kinds(bad)
    background = dt.legality(deck(("commander", 1, hero), ("commander", 1, back), rest), "commander")
    assert not [k for k in kinds(background) if k[0].startswith("commander_")]
    three = dt.legality(deck(("commander", 1, a), ("commander", 1, b), ("commander", 1, third), ("main", 97, FOREST)), "commander")
    assert any(k[0] == "commander_count" for k in kinds(three))


# -- the community formats' sizes and commander rules (vault-35i; sources read 2026-10-11) --------------------------------------

WALKER = card("Jace Test", "U", "", "Legendary Planeswalker — Jace", legal=("oathbreaker", "standardbrawl", "brawl"))
SPELL = card("Counter Test", "U", "Counter target spell.", "Instant", legal=("oathbreaker",))
OFF_COLOUR_SPELL = card("Burn Test", "R", "Burn Test deals 3 damage to any target.", "Instant", legal=("oathbreaker",))
ISLAND = card("Island", "U", "", "Basic Land — Island", legal=("oathbreaker", "standardbrawl", "gladiator", "paupercommander"))


def test_oathbreaker_is_exactly_sixty_cards_with_a_planeswalker_and_a_signature_spell():
    ok = dt.legality(deck(("commander", 1, WALKER), ("commander", 1, SPELL), ("main", 58, ISLAND)), "oathbreaker")
    assert ok["legal"] is True, ok["issues"]
    short = dt.legality(deck(("commander", 1, WALKER), ("commander", 1, SPELL), ("main", 57, ISLAND)), "oathbreaker")
    assert ("deck_size", None) in kinds(short)
    no_walker = dt.legality(deck(("commander", 1, SPELL), ("main", 59, ISLAND)), "oathbreaker")
    assert ("oathbreaker_missing", None) in kinds(no_walker)
    no_spell = dt.legality(deck(("commander", 1, WALKER), ("main", 59, ISLAND)), "oathbreaker")
    assert ("signature_spell_missing", None) in kinds(no_spell)


def test_an_oathbreaker_signature_spell_outside_the_oathbreakers_colours_is_illegal():
    bad = dt.legality(deck(("commander", 1, WALKER), ("commander", 1, OFF_COLOUR_SPELL), ("main", 58, ISLAND)), "oathbreaker")
    assert ("color_identity", "Burn Test") in kinds(bad)


def test_standard_brawl_is_exactly_sixty_with_a_legendary_creature_or_planeswalker_commander():
    boss = card("Boss", "G", type_line="Legendary Creature", legal=("standardbrawl",))
    ok = dt.legality(deck(("commander", 1, boss), ("main", 59, FOREST_SB)), "standardbrawl")
    assert ("deck_size", None) not in kinds(ok)
    short = dt.legality(deck(("commander", 1, boss), ("main", 58, FOREST_SB)), "standardbrawl")
    assert ("deck_size", None) in kinds(short)
    walker_ok = dt.legality(deck(("commander", 1, WALKER), ("main", 59, ISLAND)), "standardbrawl")
    assert ("commander_not_eligible", "Jace Test") not in kinds(walker_ok)
    plain = card("Plain Bear", "G", type_line="Creature", legal=("standardbrawl",))
    not_legendary = dt.legality(deck(("commander", 1, plain), ("main", 59, FOREST_SB)), "standardbrawl")
    assert ("commander_not_eligible", "Plain Bear") in kinds(not_legendary)


FOREST_SB = card("Forest", "G", "", "Basic Land — Forest", legal=("standardbrawl", "gladiator"))


def test_gladiator_is_at_least_a_hundred_cards_singleton_with_no_sideboard_and_no_commander_needed():
    ok = dt.legality(deck(("main", 100, FOREST_SB)), "gladiator")
    assert ok["legal"] is True, ok["issues"]
    short = dt.legality(deck(("main", 99, FOREST_SB)), "gladiator")
    assert ("deck_size", None) in kinds(short)
    side = dt.legality(deck(("main", 100, FOREST_SB), ("sideboard", 1, FOREST_SB)), "gladiator")
    assert ("sideboard_size", None) in kinds(side)
    assert not any(k == "no_commander" for k, _ in kinds(ok))


def test_pauper_commander_takes_a_creature_commander_that_need_not_be_legendary():
    uncommon = card("Uncommon Bear", "G", type_line="Creature — Bear", legal=("paupercommander",))
    spell = card("Just A Spell", "G", type_line="Sorcery", legal=("paupercommander",))
    ok = dt.legality(deck(("commander", 1, uncommon), ("main", 99, ISLAND_PDH)), "paupercommander")
    assert ("commander_not_eligible", "Uncommon Bear") not in kinds(ok)
    bad = dt.legality(deck(("commander", 1, spell), ("main", 99, ISLAND_PDH)), "paupercommander")
    assert ("commander_not_eligible", "Just A Spell") in kinds(bad)


ISLAND_PDH = card("Forest", "G", "", "Basic Land — Forest", legal=("paupercommander",))
