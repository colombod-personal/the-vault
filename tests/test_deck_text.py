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


# -- bounded input (#349): the library's header pattern backtracks cubically on spaces (1,600 took 14.7 s) -------------------

def timed(text):
    import time

    started = time.monotonic()
    deck = deck_text.parse_text(text)
    return time.monotonic() - started, deck


def test_no_input_of_50000_characters_takes_more_than_half_a_second():
    cases = ["x" + " " * 49_000 + "!", "a" * 49_000 + "!", ("b" * 299 + "\n") * 160, "1 a" + " " * 49_000 + "b",
             " " * 49_000 + "*CMDR*", "1 Sol Ring" + " " * 49_000 + "\n1 Island", "# " + "a " * 24_000 + "!", "\t" * 49_000 + "!"]
    for text in cases:
        spent, _ = timed(text)
        assert spent < 0.5, (text[:20], spent)


def test_ordinary_lists_are_read_exactly_as_before_and_a_long_line_is_cut_not_dropped():
    text = ("Commander\n1 Sliver Overlord\n\nDeck\n2x Sol Ring (C21) 263 [Ramp]\n1   Island\nSideboard\n1 Duress\n"
            "1 Muscle Sliver [" + ", ".join(f"Category{n}" for n in range(12)) + "]")
    assert [(l.name, l.quantity, l.section) for l in deck_text.parse_text(text).lines] == \
        [(l.name, l.quantity, l.section) for l in decklist.parse_text(text.replace("1   Island", "1 Island")).lines]
    long = "1 Sol Ring" + " " * 5_000 + "(C21) 263"
    assert [(l.name, l.quantity) for l in deck_text.parse_text(long).lines] == [("Sol Ring", 1)]  # collapsed, not dropped


def test_a_saved_deck_keeps_its_text_byte_for_byte_whatever_the_reader_does(signed_in):
    text = "1 Sol Ring" + " " * 2_000 + "\n1   Island\n" + "x" * 1_000 + "\n"
    saved = signed_in.post("/api/v1/decks", json={"name": "Long lines", "text": text})
    assert saved.status_code == 201
    assert signed_in.get(f"/api/v1/decks/{saved.json()['id']}").json()["text"] == text


def test_the_cuts_and_adds_of_the_validator_are_length_capped_on_rest_too(signed_in):
    body = {"text": "1 Sol Ring", "format": "commander", "adds": ["x" * 301], "cuts": []}
    assert signed_in.post("/api/v1/decks/validate-changes", json=body).status_code == 422
    body["adds"] = ["x" * 300]
    assert signed_in.post("/api/v1/decks/validate-changes", json=body).status_code in (200, 503)  # a long name is just unknown
