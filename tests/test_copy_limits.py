"""#423: how many copies of a card a deck may hold. Cards that carry their own rule ('A deck can have any number of cards named X',
'up to nine cards named X') beat the format's limit in every format, Commander included. The texts are the real ones from Scryfall's
search of every paper card that says 'a deck can have' (2026-10-09)."""

import pytest

from vault import deck_tools as dt
from vault.models import OracleCard


def card(name, text, type_line="Creature", fmt="commander"):
    return OracleCard(oracle_id=name, name=name, type_line=type_line, oracle_text=text, legalities={fmt: "legal"})


ANY = ["Cid, Timeless Artificer", "Dragon's Approach", "Hare Apparent", "Persistent Petitioners", "Rat Colony", "Relentless Rats",
       "Shadowborn Apostle", "Slime Against Humanity", "Sphinx's Approach", "Tempest Hawk", "Templar Knight"]


@pytest.mark.parametrize("name", ANY)
@pytest.mark.parametrize("fmt", ["commander", "modern", "pauper", "legacy", "brawl"])
def test_any_number_of_cards_named_is_unlimited_in_every_format(name, fmt):
    assert dt.copy_limit(card(name, f"A deck can have any number of cards named {name}."), fmt) is None


@pytest.mark.parametrize("name,word,count", [("Nazgûl", "nine", 9), ("Seven Dwarves", "seven", 7)])
@pytest.mark.parametrize("fmt", ["commander", "modern", "pauper", "vintage", "brawl"])
def test_up_to_n_cards_named_allows_exactly_n_in_every_format(name, word, count, fmt):
    assert dt.copy_limit(card(name, f"Some ability.\nA deck can have up to {word} cards named {name}."), fmt) == count


def test_up_to_a_digit_is_read_too_and_the_phrase_is_found_on_a_face():
    assert dt.copy_limit(card("X", "A deck can have up to 12 cards named X."), "modern") == 12
    two_faced = OracleCard(oracle_id="d", name="A // B", type_line="Creature // Creature", oracle_text=None, legalities={"modern": "legal"},
                           faces=[{"name": "A", "oracle_text": "A deck can have up to nine cards named A."}, {"name": "B", "oracle_text": ""}])
    assert dt.copy_limit(two_faced, "modern") == 9


def test_everything_else_keeps_the_formats_limit():
    plain = card("Sol Ring", "{T}: Add {C}{C}.", "Artifact")
    assert dt.copy_limit(plain, "commander") == 1 and dt.copy_limit(plain, "modern") == 4
    restricted = OracleCard(oracle_id="r", name="Black Lotus", type_line="Artifact", oracle_text="", legalities={"vintage": "restricted"})
    assert dt.copy_limit(restricted, "vintage") == 1
    assert dt.copy_limit(card("Forest", "", "Basic Land — Forest"), "commander") is None
    assert dt.copy_limit(card("Once More with Feeling", "DCI ruling — A deck can have only one card named Once More with Feeling."), "modern") == 4


def test_the_deck_ideas_filter_uses_the_same_rule():
    from vault import deck_ideas
    nazgul = card("Nazgûl", "A deck can have up to nine cards named Nazgûl.")
    assert deck_ideas.copy_limit(nazgul, "commander") == 9


def test_deck_rule_reads_the_three_sentences_and_nothing_else():
    assert dt.deck_rule("A deck can have any number of cards named Rat Colony.") == ("any", None)
    assert dt.deck_rule("A deck can have up to nine cards named Nazgûl.") == ("up_to", 9)
    assert dt.deck_rule("DCI ruling — A deck can have only one card named Once More with Feeling.") == ("one", 1)
    assert dt.deck_rule("Flying") == (None, None)


def test_an_unread_wording_is_reported_but_the_known_ones_and_ordinary_text_are_not():
    assert dt.unread_deck_rule("A deck can have no more than three cards named Foo.")  # a wording nobody wrote a reader for
    assert dt.unread_deck_rule("Your deck must contain at least 120 cards.")
    assert not dt.unread_deck_rule("A deck can have up to nine cards named Nazgûl.")
    assert not dt.unread_deck_rule("A deck can have any number of cards named Rat Colony.")
    assert not dt.unread_deck_rule("Draw a card, then put a card from your hand on top of your library. Your deck is shuffled.")

