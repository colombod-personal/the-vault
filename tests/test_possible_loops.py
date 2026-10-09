"""The optional "possible loops" note on find_combos (#172, docs/possible-loops-design.md): the Vault's own reading of a deck's card text,
off by default, labelled the Vault's and not Commander Spellbook's, never claiming a loop the arithmetic does not support.

The Sliver cards below are the Oracle texts the Vault's own catalog served (get_card_oracle, 2026-10-09; Scryfall's Oracle data, Wizards'
text): the test universe has no Slivers, so they are loaded into the test catalog as a faithful fixture. The other cards are invented."""

import json
import random
import re

import pytest
from fastapi.testclient import TestClient

from tests.test_deck_api import CARDS, card
from twins import Universe
from vault import catalog_sync as cs
from vault import combos, possible_loops as pl
from vault.app import create_app
from vault.config import Settings

V1 = "/api/v1/decks"
C = pl.CardText

QUEEN = C("Sliver Queen", "Legendary Creature — Sliver", "{2}: Create a 1/1 colorless Sliver creature token.")
MANAWEFT = C("Manaweft Sliver", "Creature — Sliver", 'Sliver creatures you control have "{T}: Add one mana of any color."')
GEMHIDE = C("Gemhide Sliver", "Creature — Sliver", 'All Slivers have "{T}: Add one mana of any color."')
HEART = C("Heart Sliver", "Creature — Sliver", "All Sliver creatures have haste.")
HEARTSTONE = C("Heartstone", "Artifact", "Activated abilities of creatures cost {1} less to activate. This effect can't reduce the mana in that cost to less than one mana.")
REFLECTION = C("Mana Reflection", "Enchantment", "If you tap a permanent for mana, it produces twice as much of that mana instead.")
TRIO = [QUEEN, MANAWEFT, HEART]

# the words the design forbids in anything the reading says about a possible loop (design section 5, rule 2)
FORBIDDEN = re.compile(r"infinite|combo|guarantee|wins? the game|proven", re.IGNORECASE)


def only(cards, **kw):
    out = pl.read(cards, **kw)
    return out, (out["loops"][0] if out["loops"] else None)


def strings(value):
    """Every string in a nested answer, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from strings(v)


# -- reading a card's text into facts: the shapes it accepts, and the near misses it must not ---------------------------------------

def facts_of(*cards):
    return pl.read_facts(list(cards))


@pytest.mark.parametrize("text, cost, types", [
    ("{2}: Create a 1/1 colorless Sliver creature token.", (2, ()), {"Sliver"}),
    ("{1}{W}: Create a 1/1 white Soldier creature token.", (1, ("W",)), {"Soldier"}),
    ("{3}: Create a 2/2 green Elf Warrior creature token.", (3, ()), {"Elf", "Warrior"}),
    ("{2}: Create a 1/1 colorless Sliver creature token. (Reminder text is removed.)", (2, ()), {"Sliver"}),
])
def test_a_token_ability_that_costs_only_mana_is_read(text, cost, types):
    facts = facts_of(C("Maker", "Creature — Test", text))
    assert len(facts.makers) == 1 and not facts.skipped
    maker = facts.makers[0]
    assert (maker.cost.generic, maker.cost.coloured) == cost and set(maker.types) == types and maker.is_creature


@pytest.mark.parametrize("text", [
    "Create a 1/1 colorless Sliver creature token.",                                  # no cost: not an activated ability
    "{T}: Create a 1/1 colorless Sliver creature token.",                              # taps: a cost in another resource
    "{2}, {T}: Create a 1/1 colorless Sliver creature token.",
    "{X}: Create a 1/1 colorless Sliver creature token.",                              # an X
    "{2}, Sacrifice a creature: Create a 1/1 colorless Sliver creature token.",
    "{2}, Discard a card: Create a 1/1 colorless Sliver creature token.",
    "{2}, Pay 2 life: Create a 1/1 colorless Sliver creature token.",
    "{W/U}: Create a 1/1 colorless Sliver creature token.",                            # hybrid is not priced
    "{2}: Create two 1/1 colorless Sliver creature tokens.",                           # a count
    "{2}: Create a 1/1 colorless Sliver creature token. Activate only once each turn.",  # a rider
    "{2}: Create a 1/1 colorless Sliver creature token with haste.",                    # a clause the reading cannot price
    "{2}: Create a 1/1 colorless Sliver creature token if you control a Wizard.",
    "{2}: Create a X/X colorless Sliver creature token.",
    "{2}: Create a Treasure token.",                                                   # not a creature
])
def test_a_token_ability_with_anything_the_reading_cannot_price_is_not_used(text):
    facts = facts_of(C("Maker", "Creature — Test", text))
    assert facts.makers == []
    assert pl.read([C("Maker", "Creature — Test", text), MANAWEFT, HEART])["loops"] == []


def test_a_token_line_it_could_not_read_is_named_so_the_answer_says_what_it_skipped():
    out = pl.read([C("Maker", "Creature — Test", "{2}, {T}: Create a 1/1 colorless Sliver creature token."), MANAWEFT, HEART])
    assert out["loops"] == [] and out["not_read"][0]["card"] == "Maker" and "not only mana" in out["not_read"][0]["why"]


def test_a_lord_that_grants_a_token_ability_is_not_mistaken_for_a_token_maker():
    lord = C("Lord", "Creature — Elf", 'Elf creatures you control have "{2}: Create a 1/1 green Elf creature token."')
    facts = facts_of(lord)
    assert facts.makers == [] and facts.skipped == []


@pytest.mark.parametrize("text, scope, mana, any_colour", [
    ('Sliver creatures you control have "{T}: Add one mana of any color."', {"Sliver"}, 1, True),
    ('All Slivers have "{T}: Add one mana of any color."', {"Sliver"}, 1, True),
    ('All Sliver creatures have "{T}: Add {G}."', {"Sliver"}, 1, False),
    ('Creatures you control have "{T}: Add {C}{C}."', None, 2, False),
    ('All creatures have "{T}: Add one mana of any color."', None, 1, True),
    ("Elf creatures you control have “{T}: Add {G}.”", {"Elf"}, 1, False),   # curly quotes, as some sources print them
])
def test_a_grant_of_a_mana_ability_is_read_from_the_text(text, scope, mana, any_colour):
    facts = facts_of(C("Lord", "Creature — Test", text))
    assert len(facts.taps) == 1
    grant = facts.taps[0]
    assert (None if grant.scope is None else set(grant.scope)) == scope and grant.mana == mana and grant.any_colour is any_colour


@pytest.mark.parametrize("text", [
    'Sliver creatures you control have "{T}: Add one mana of any color. Spend this mana only to cast Sliver spells."',
    'Sliver creatures you control have "{T}: Add {G} or {W}."',
    'Sliver creatures you control have "{T}: Add {X}."',
    'Sliver creatures you control have "{T}, Sacrifice this creature: Add {R}{R}."',
    'Each opponent\'s creatures have "{T}: Add {C}."',
    'Sliver creatures you control have "{T}: Add one mana of any color" unless you control a Wizard.',
])
def test_a_grant_with_any_extra_clause_or_another_shape_is_not_used(text):
    assert facts_of(C("Lord", "Creature — Test", text)).taps == []


@pytest.mark.parametrize("text, scope", [("All Sliver creatures have haste.", {"Sliver"}), ("Creatures you control have haste.", None),
                                         ("Goblin creatures you control have haste.", {"Goblin"}), ("All creatures have haste.", None)])
def test_a_grant_of_haste_is_read(text, scope):
    haste = facts_of(C("Lord", "Creature — Test", text)).hastes
    assert len(haste) == 1 and (None if haste[0].scope is None else set(haste[0].scope)) == scope


@pytest.mark.parametrize("text", ["All Sliver creatures have haste until end of turn.", "Sliver creatures you control have flying.",
                                  "Target creature gains haste until end of turn.", "Creatures you control have haste if you attacked."])
def test_anything_else_is_not_haste_for_every_token(text):
    assert facts_of(C("Lord", "Creature — Test", text)).hastes == []


# -- the arithmetic, on the real Sliver texts ---------------------------------------------------------------------------------------

def test_the_sliver_trio_is_an_engine_one_mana_short_not_a_loop_and_shows_its_arithmetic():
    """The real example of #172: pay {2}, a Sliver token that taps for {1} at once (haste): net -1 per token."""
    out, loop = only(TRIO)
    assert out["total"] == 1 and loop["confidence"] == "one_short"
    assert loop["cards"] == ["Sliver Queen", "Manaweft Sliver", "Heart Sliver"]
    assert loop["steps"] == ["Pay {2} (Sliver Queen): a 1/1 Sliver token.", "It has haste (Heart Sliver) and taps for 1 mana (Manaweft Sliver).",
                             "Net: -1 mana per token."]
    assert loop["net"] == "-1 mana per pass" and loop["closed_by"] == []
    assert "one mana short of a loop" in loop["reading"] and "possible loop:" not in loop["reading"]
    assert "lowers {2} by one" in loop["needs"] and "one more mana" in loop["needs"]


def test_a_cost_reduction_with_a_floor_of_one_closes_the_sliver_engine_and_is_named():
    out, loop = only(TRIO + [HEARTSTONE])
    assert loop["confidence"] == "closes"
    assert loop["closed_by"] == [{"cards": ["Heartstone"], "how": "Heartstone lowers {2} to {1}", "net": "0 mana per pass"}]
    assert loop["cards"] == ["Sliver Queen", "Manaweft Sliver", "Heart Sliver", "Heartstone"] and loop["needs"] is None
    assert loop["net"] == "-1 mana per pass from these cards alone"  # the arithmetic of the named pieces, never rounded up
    assert "Heartstone" in loop["reading"] and loop["steps"][-1] == "Heartstone lowers {2} to {1}. Net: 0 mana per pass."


def test_a_doubling_of_mana_closes_it_and_both_alternatives_are_listed_not_added_up():
    out, loop = only(TRIO + [REFLECTION, HEARTSTONE])
    assert loop["confidence"] == "closes"
    assert [c["cards"] for c in loop["closed_by"]] == [["Heartstone"], ["Mana Reflection"]]  # each alone suffices
    assert "Heartstone or Mana Reflection" in loop["reading"]


def test_a_second_card_that_grants_the_same_mana_does_not_add_a_second_mana_a_creature_taps_once():
    _, loop = only(TRIO + [GEMHIDE])
    assert loop["confidence"] == "one_short" and loop["steps"][1] == "It has haste (Heart Sliver) and taps for 1 mana (Gemhide Sliver)."


def test_grants_are_never_added_up_the_biggest_one_wins():
    big = C("Big Lord", "Creature — Sliver", 'Sliver creatures you control have "{T}: Add {R}{R}."')
    queen = C("Red Queen", "Creature — Sliver", "{1}{R}: Create a 1/1 red Sliver creature token.")
    _, loop = only([queen, MANAWEFT, big, HEART])
    assert loop["confidence"] == "closes" and loop["net"] == "0 mana per pass" and "Big Lord" in loop["steps"][1]


def test_without_haste_nothing_is_reported_because_a_new_token_cannot_tap_this_turn():
    assert pl.read([QUEEN, MANAWEFT])["loops"] == []


def test_a_grant_to_another_type_gives_no_report():
    elf_lord = C("Elf Lord", "Creature — Elf", 'Elf creatures you control have "{T}: Add one mana of any color."')
    assert pl.read([QUEEN, elf_lord, HEART])["loops"] == []
    goblin_haste = C("Goblin Haste", "Creature — Goblin", "Goblin creatures you control have haste.")
    assert pl.read([QUEEN, MANAWEFT, goblin_haste])["loops"] == []


def test_a_one_mana_cost_with_a_one_mana_tap_closes_with_a_net_of_zero():
    cheap = C("Cheap Queen", "Creature — Sliver", "{1}: Create a 1/1 colorless Sliver creature token.")
    _, loop = only([cheap, MANAWEFT, HEART])
    assert loop["confidence"] == "closes" and loop["net"] == "0 mana per pass" and loop["closed_by"] == []
    assert "possible loop:" in loop["reading"]


def test_a_gap_of_two_or_more_is_not_reported_unless_something_closes_it():
    dear = C("Dear Queen", "Creature — Sliver", "{3}: Create a 1/1 colorless Sliver creature token.")
    assert pl.read([dear, MANAWEFT, HEART])["loops"] == []
    assert pl.read([dear, MANAWEFT, HEART, REFLECTION])["loops"] == []  # a closer that does not close the whole gap does not make it an engine
    assert pl.read([dear, MANAWEFT, HEART, HEARTSTONE])["loops"] == []
    _, loop = only([dear, MANAWEFT, HEART, REFLECTION, HEARTSTONE])  # {3} -> {2} and the tap makes 2
    assert loop["confidence"] == "closes" and loop["closed_by"][0]["cards"] == ["Heartstone", "Mana Reflection"]


def test_a_cost_reduction_never_takes_a_cost_below_one_mana():
    one = C("One Queen", "Creature — Sliver", "{1}: Create a 1/1 colorless Sliver creature token.")
    dear = C("Dear Queen", "Creature — Sliver", "{3}: Create a 1/1 colorless Sliver creature token.")
    training = C("Training Grounds", "Enchantment", "Activated abilities of creatures you control cost {2} less to activate. This effect can't reduce the mana in that cost to less than one mana.")
    assert pl._paid(pl.read_facts([one]).makers[0], pl.Reducer("Training Grounds", 2)).total == 1
    assert pl._paid(pl.read_facts([dear]).makers[0], pl.Reducer("Training Grounds", 2)).total == 1
    assert pl.read_facts([training]).reducers == [pl.Reducer("Training Grounds", 2)]


def test_a_cost_reduction_does_not_help_a_token_maker_that_is_not_a_creature():
    wonder = C("Token Altar", "Artifact", "{2}: Create a 1/1 colorless Sliver creature token.")
    _, loop = only([wonder, MANAWEFT, HEART, HEARTSTONE])
    assert loop["confidence"] == "one_short" and loop["closed_by"] == []


def test_a_coloured_cost_needs_mana_of_that_colour_from_the_grant():
    red = C("Red Maker", "Creature — Sliver", "{1}{R}: Create a 1/1 red Sliver creature token.")
    green = C("Green Lord", "Creature — Sliver", 'Sliver creatures you control have "{T}: Add {G}{G}."')
    assert pl.read([red, green, HEART])["loops"] == []                 # it cannot make the red mana the cost needs
    _, loop = only([red, MANAWEFT, HEART])                               # any colour can
    assert loop["confidence"] == "one_short" and loop["steps"][0].startswith("Pay {1}{R}")


def test_a_reading_whose_cards_all_sit_in_one_spellbook_combo_is_left_to_spellbook():
    names = ["Sliver Queen", "Manaweft Sliver", "Heart Sliver"]
    out = pl.read(TRIO, listed_by_spellbook=[names])
    assert out["loops"] == [] and out["also_listed_by_spellbook"] == 1
    assert pl.read(TRIO, listed_by_spellbook=[["sliver queen", "heart sliver"]])["total"] == 1  # a smaller combo does not cover it
    assert pl.read(TRIO + [HEARTSTONE], listed_by_spellbook=[names])["total"] == 1                   # a card Spellbook's combo lacks


def test_nothing_found_says_what_was_covered_and_never_that_the_deck_has_no_loops():
    out = pl.read([C("Sol Ring", "Artifact", "{T}: Add {C}{C}.")])
    assert out["loops"] == [] and out["covers"] == ["token for mana"] and "says nothing about other shapes" in out["summary"]
    assert "Finding none says only that these patterns found none" in out["note"]
    assert not FORBIDDEN.search(" ".join(strings(out)))


# -- never a false claim ------------------------------------------------------------------------------------------------------------

PLAIN_LINE = "{%d}: Create a 1/1 colorless Sliver creature token."
BLOCKED_LINES = ["{%d}: Create a 1/1 colorless Sliver creature token. Activate only once each turn.",
               "{%d}, {T}: Create a 1/1 colorless Sliver creature token.", "{%d}: Create two 1/1 colorless Sliver creature tokens.", "{X}: Create a 1/1 colorless Sliver creature token."]
GRANTS = ['Sliver creatures you control have "{T}: Add one mana of any color."', 'All Slivers have "{T}: Add {G}{G}."',
          'Sliver creatures you control have "{T}: Add {G} or {W}."', 'Elf creatures you control have "{T}: Add {G}."',
          'Sliver creatures you control have "{T}: Add one mana of any color. Spend this mana only to cast creature spells."']
HASTES = ["All Sliver creatures have haste.", "Elf creatures you control have haste.", "Sliver creatures you control have haste until end of turn."]
CLOSERS = [HEARTSTONE.text, REFLECTION.text, "Activated abilities of creatures cost {1} less to activate.", "If you tap a permanent for mana, it produces one additional mana."]


def invented_deck(rng):
    cards = []
    for i in range(rng.randint(1, 4)):
        line = rng.choice([PLAIN_LINE] * 3 + BLOCKED_LINES)
        cards.append(C(f"Maker {i}", rng.choice(["Creature — Sliver", "Artifact"]), line % rng.randint(1, 4) if "%d" in line else line))
    for kind, lines in (("Lord", GRANTS), ("Hasty", HASTES), ("Closer", CLOSERS)):
        for i in range(rng.randint(1 if kind != "Closer" else 0, 2)):
            cards.append(C(f"{kind} {i}", "Creature — Sliver", rng.choice(lines)))
    return cards


def test_random_decks_of_invented_cards_never_get_a_claim_the_arithmetic_does_not_support():
    """`closes` only when, by the matched facts, net >= 0 (alone, or with the named closers), and one_short only at exactly -1."""
    rng = random.Random(172)
    claims, seen = 0, set()
    for _ in range(400):
        deck = invented_deck(rng)
        out = pl.read(deck)
        facts = pl.read_facts(deck)
        for loop in out["loops"]:
            claims += 1
            seen.add(loop["confidence"])
            maker = next(m for m in facts.makers if m.card == loop["cards"][0])
            assert maker.cost.total >= 1 and "{T}" not in maker.cost.text()
            tap = max((g.mana for g in facts.taps if pl._covers(g.scope, maker.types)), default=0)
            assert tap >= 1 and any(pl._covers(h.scope, maker.types) for h in facts.hastes)
            base = tap - maker.cost.total
            if loop["confidence"] == "one_short":
                assert base == -1 and loop["closed_by"] == []
            elif base < 0:
                assert loop["closed_by"], "closes below zero only through named cards"
                named = {r.card for r in facts.reducers} | {m.card for m in facts.multipliers}
                for option in loop["closed_by"]:
                    assert set(option["cards"]) <= named
                    assert option["net"] in ("0 mana per pass",) or option["net"].startswith("+")
            else:
                assert loop["closed_by"] == []
        assert not FORBIDDEN.search(" ".join(strings(out)))
    assert claims > 40 and seen == {"closes", "one_short"}  # the generator does produce both kinds: the test is not vacuous


def test_a_blocker_on_the_token_line_removes_the_claim_whatever_else_the_deck_holds():
    for line in BLOCKED_LINES:
        deck = [C("Maker", "Creature — Sliver", line % 1 if "%d" in line else line), MANAWEFT, HEART, HEARTSTONE, REFLECTION]
        assert pl.read(deck)["loops"] == [], line


# -- the wording the design fixes ---------------------------------------------------------------------------------------------------

def test_nothing_the_reading_says_contains_a_word_the_design_forbids_and_each_loop_names_what_it_is_not():
    for deck in (TRIO, TRIO + [HEARTSTONE], TRIO + [REFLECTION, HEARTSTONE], [C("Sol Ring", "Artifact", "{T}: Add {C}{C}.")]):
        out = pl.read(deck)
        text = " ".join(strings(out))
        assert not FORBIDDEN.search(text), FORBIDDEN.search(text)
        assert out["source"] == "the Vault's reading of card text" and out["not_from"] == "Commander Spellbook"
        assert "not Commander Spellbook's" in out["note"] and out["note"].startswith("A reading of the text, not a proof")
        for loop in out["loops"]:
            assert loop["assumes"] and loop["verify"] and loop["pattern"] == "token for mana" and loop["confidence"] in ("closes", "one_short")
            assert "get_card_oracle" in loop["verify"] and "judge" in loop["verify"]
    # the module's own fixed strings too, before any card is read
    assert not FORBIDDEN.search(" ".join([pl.SOURCE, pl.NOT_FROM, pl.NOTE, pl.VERIFY, *pl.ASSUMES]))


# -- the answer ----------------------------------------------------------------------------------------------------------------------

def oid(n):
    return f"{n:08d}-0000-0000-0000-000000000000"


SLIVER_CARDS = [card(20 + i, c.name, c.type_line, ["G"], 2, text=c.text) for i, c in enumerate([QUEEN, MANAWEFT, HEART, HEARTSTONE])]
DECK = "Commander\n1 Sliver Queen\n\nDeck\n1 Manaweft Sliver\n1 Heart Sliver\n1 Test Rock\n"


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def client(database_url, universe):
    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver")
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        extra = [dict(CARDS[0], oracle_id=f"9000000{i}-0000-0000-0000-000000000000", name=n) for i, n in enumerate(["Thassa's Oracle", "Demonic Consultation"])]
        with app.state.db.sessions() as db:
            cs.sync_oracle_cards(db, CARDS + extra + SLIVER_CARDS)
            cs.record_source(db, "oracle_cards", version="v", rows=1)
            db.commit()
        yield c
    app.state.db.engine.dispose()


def ask(client, text=DECK, **extra):
    return client.post(f"{V1}/combos", json={"text": text, **extra})


def test_the_note_is_off_by_default_and_the_answer_is_what_it_was(client):
    plain = ask(client).json()
    assert ask(client, include_possible_loops=False).json() == plain
    assert set(plain["result"]) == {"included", "almost_included", "totals", "notes", "limits"} and len(plain["provenance"]) == 2


def test_with_the_flag_the_sliver_deck_gets_the_vaults_reading_apart_from_spellbooks_lists(client, universe):
    plain = ask(client).json()
    res = ask(client, include_possible_loops=True)
    assert res.status_code == 200
    body = res.json()
    for key in ("included", "almost_included", "totals", "notes", "limits"):  # Spellbook's part is untouched
        assert body["result"][key] == plain["result"][key]
    assert body["result"]["included"] == []
    loops = body["result"]["possible_loops"]
    assert loops["source"] == "the Vault's reading of card text" and loops["not_from"] == "Commander Spellbook"
    assert loops["loops"][0]["confidence"] == "one_short" and loops["loops"][0]["cards"] == ["Sliver Queen", "Manaweft Sliver", "Heart Sliver"]
    assert not FORBIDDEN.search(" ".join(strings(loops)))
    # provenance: Spellbook's two blocks stay, and the reading is a third, computed, from Scryfall's Oracle data, with the notice
    blocks = body["provenance"]
    assert [b["kind"] for b in blocks] == ["source", "computed", "computed"] and blocks[0]["source"] == "Commander Spellbook"
    mine = blocks[2]
    assert mine["origin"] == "the Vault's reading of card text" and mine["source"] == "The Vault" and mine["notice"]
    assert [i["source"] for i in mine["inputs"]] == ["Scryfall"] and mine["inputs"][0]["notice"]
    assert "Commander Spellbook" not in json.dumps(mine)  # never listed as a Spellbook source
    assert len([c for c in universe.spellbook.calls if c.path == "/find-my-combos"]) == 2  # still one call per question


def test_a_card_that_closes_the_gap_is_named_in_the_answer(client):
    body = ask(client, DECK + "1 Heartstone\n", include_possible_loops=True).json()
    loop = body["result"]["possible_loops"]["loops"][0]
    assert loop["confidence"] == "closes" and loop["closed_by"][0]["cards"] == ["Heartstone"]


def test_a_deck_with_no_such_engine_says_what_the_patterns_cover(client):
    body = ask(client, "Deck\n1 Test Rock\n", include_possible_loops=True).json()
    loops = body["result"]["possible_loops"]
    assert loops["loops"] == [] and loops["covers"] == ["token for mana"] and "says nothing about other shapes" in loops["summary"]


def test_spellbook_failing_does_not_take_the_note_down_and_the_answer_says_so(client, universe):
    universe.spellbook.fail_next("/find-my-combos", 500)
    res = ask(client, include_possible_loops=True)
    assert res.status_code == 200
    body = res.json()
    assert "Commander Spellbook" in body["result"]["spellbook"]["unavailable"] and "says nothing about whether the deck has any" in body["result"]["spellbook"]["unavailable"]
    assert "included" not in body["result"] and "totals" not in body["result"]  # no list at all, never an empty one that reads as "none"
    assert body["result"]["possible_loops"]["loops"][0]["confidence"] == "one_short"
    assert [b["origin"] for b in body["provenance"]] == ["the Vault's reading of card text"]  # nothing of Spellbook's was used
    universe.spellbook.fail_next("/find-my-combos", 500)
    assert ask(client).status_code == 502  # without the flag the error is what it was


def test_a_busy_spellbook_client_does_not_take_the_note_down_either(client, universe, monkeypatch):
    monkeypatch.setattr(combos, "GUARD", combos.UpstreamGuard(rate_per_minute=1))
    assert ask(client).status_code == 200
    assert ask(client).status_code == 503  # the client's own rate limit, as before
    res = ask(client, include_possible_loops=True)
    assert res.status_code == 200 and "unavailable" in res.json()["result"]["spellbook"] and res.json()["result"]["possible_loops"]["total"] == 1


def test_a_combo_spellbook_lists_for_the_same_cards_is_theirs_and_the_vaults_duplicate_is_dropped(client, universe):
    universe.spellbook.add_combo(["Sliver Queen", "Manaweft Sliver", "Heart Sliver"], ["Infinite tokens"], "Their description.")
    body = ask(client, include_possible_loops=True).json()
    assert body["result"]["included"][0]["cards"] == ["Sliver Queen", "Manaweft Sliver", "Heart Sliver"]
    loops = body["result"]["possible_loops"]
    assert loops["loops"] == [] and loops["also_listed_by_spellbook"] == 1


def test_another_persons_deck_is_a_404_with_the_flag_as_without(client):
    saved = client.post("/api/v1/decks", json={"name": "Slivers", "text": DECK}).json()["id"]
    assert ask(client, text=None, deck_id=saved, include_possible_loops=True).json()["result"]["possible_loops"]["total"] == 1
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": "bob@example.com"}).status_code == 200
    for flag in (False, True):
        assert client.post(f"{V1}/combos", json={"deck_id": saved, "include_possible_loops": flag}).status_code == 404


# -- the tool ------------------------------------------------------------------------------------------------------------------------

def mcp_call(client, token, tool, **arguments):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    res = client.post("/api/mcp", json=body, headers={"Authorization": "Bearer " + token})
    assert res.status_code == 200, res.text
    return res.json()["result"]


def test_the_find_combos_tool_takes_the_flag_and_says_what_it_does_in_its_description(client):
    from vault.api import mcp
    tool = mcp.BY_NAME["find_combos"]
    flag = tool.schema()["inputSchema"]["properties"]["include_possible_loops"]
    assert flag["type"] == "boolean" and flag["default"] is False
    assert "include_possible_loops" in tool.description and "not Spellbook's" in tool.description and "off by default" in tool.description
    token = client.post("/api/v1/me/tokens", json={"name": "agent", "scopes": ["read", "write"]}).json()["token"]
    out = mcp_call(client, token, "find_combos", text=DECK, include_possible_loops=True)
    assert not out.get("isError"), out
    loops = out["structuredContent"]["result"]["possible_loops"]
    assert loops["loops"][0]["confidence"] == "one_short"
    assert out["structuredContent"]["provenance"][-1]["origin"] == "the Vault's reading of card text"
    plain = mcp_call(client, token, "find_combos", text=DECK)["structuredContent"]["result"]
    assert "possible_loops" not in plain
