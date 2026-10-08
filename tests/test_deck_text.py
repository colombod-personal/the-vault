"""vault/deck_text.py: a Moxfield ``*CMDR*`` line is a Commander-section line without its marker (#332)."""

from mtg_toolkits import decklist

from vault import deck_text


def lines(text):
    return [(l.name, l.set_code, l.collector_number, l.section, l.quantity) for l in deck_text.parse_text(text).lines]


def test_a_marked_line_becomes_a_commander_with_its_printing_and_the_marker_gone():
    got = lines("1 Atraxa, Praetors' Voice (CMM) 1068 *CMDR*\n1 Sol Ring (C21) 263\n2 Island")
    assert got == [("Atraxa, Praetors' Voice", "cmm", "1068", "commander", 1), ("Sol Ring", "c21", "263", "main", 1),
                   ("Island", None, None, "main", 2)]


def test_a_foil_marker_and_a_lower_case_marker_work_and_partners_are_both_commanders():
    got = lines("1 Thrasios, Triton Hero *cmdr*\n1 Tymna the Weaver (CLB) 1 *F* *CMDR*")
    assert [(n, s) for n, _, _, s, _ in got] == [("Thrasios, Triton Hero", "commander"), ("Tymna the Weaver", "commander")]


def test_a_list_without_a_marker_is_read_exactly_as_before():
    text = "Commander\n1 Sliver Overlord\n\nDeck\n1 Sol Ring\nSideboard\n1 Duress"
    assert deck_text.parse_text(text) == decklist.parse_text(text)


def test_a_marked_line_after_a_sideboard_header_is_still_a_commander_and_the_rest_keeps_its_section():
    got = lines("Sideboard\n1 Duress\n1 Sliver Overlord *CMDR*")
    assert ("Sliver Overlord", None, None, "commander", 1) in got and ("Duress", None, None, "sideboard", 1) in got
