"""Functional equivalents (vault.equivalents, #166, docs/functional-equivalents.md): the 22 roles, the rule behind each, and the
matcher that says whether one card does the same job as another. The card texts are the short Oracle texts of well-known cards
(the owner's examples first: Doubling Season, Rhystic Study, Cyclonic Rift, Fierce Guardianship, Parallel Lives)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from vault import equivalents as eq

DOC = (Path(__file__).resolve().parent.parent / "docs" / "functional-equivalents.md").read_text(encoding="utf-8")


def card(name, type_line, text, faces=None, cmc=None):
    return SimpleNamespace(name=name, type_line=type_line, oracle_text=text, faces=faces, cmc=cmc)


DOUBLING_SEASON = ("Doubling Season", "Enchantment", "If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.\n"
                   "If an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead.")
PARALLEL_LIVES = ("Parallel Lives", "Enchantment", "If an effect would create one or more creature tokens under your control, it creates twice that many of those tokens instead.")
ANOINTED = ("Anointed Procession", "Enchantment", "If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.")
MONDRAK = ("Mondrak, Glory Dominus", "Legendary Creature — Phyrexian Horror", "If one or more tokens would be created under your control, twice that many of those tokens are created instead.")
VORINCLEX = ("Vorinclex, Monstrous Raider", "Legendary Creature — Phyrexian Elk",
             "Trample, haste\nIf you would put one or more counters on a permanent or player, put twice that many of each of those kinds of counters on that permanent or player instead.\n"
             "If an opponent would put one or more counters on a permanent or player, they put half that many of each of those kinds of counters on that permanent or player instead, rounded down.")
RHYSTIC = ("Rhystic Study", "Enchantment", "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.")
ARENA = ("Phyrexian Arena", "Enchantment", "At the beginning of your upkeep, you draw a card and you lose 1 life.")
REMORA = ("Mystic Remora", "Enchantment", "Cumulative upkeep {1}\nWhenever an opponent casts a noncreature spell, you may draw a card unless that player pays {4}.")
DIVINATION = ("Divination", "Sorcery", "Draw two cards.")
RIFT = ("Cyclonic Rift", "Instant", "Return target nonland permanent you don't control to its owner's hand.\n"
        "Overload {6}{U} (You may cast this spell for its overload cost. If you do, change its text by replacing all instances of \"target\" with \"each.\")")
EVACUATION = ("Evacuation", "Instant", "Return all creatures to their owners' hands.")
UNSUMMON = ("Unsummon", "Instant", "Return target creature to its owner's hand.")
WRATH = ("Wrath of God", "Sorcery", "Destroy all creatures. They can't be regenerated.")
GUARDIANSHIP = ("Fierce Guardianship", "Instant", "Flash\nIf you control a commander, you may cast this spell without paying its mana cost.\nCounter target noncreature spell.")
FORCE = ("Force of Will", "Instant", "You may pay 1 life and exile a blue card from your hand rather than pay this spell's mana cost.\nCounter target spell.")
DAZE = ("Daze", "Instant", "You may return an Island you control to its owner's hand rather than pay this spell's mana cost.\nCounter target spell unless its controller pays {1}.")
COUNTERSPELL = ("Counterspell", "Instant", "Counter target spell.")
TITHE = ("Smothering Tithe", "Enchantment", "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token.")
DOCKSIDE = ("Dockside Extortionist", "Creature — Goblin Pirate",
            "When Dockside Extortionist enters the battlefield, create X Treasure tokens, where X is the number of artifacts and enchantments your opponents control.")
SOL_RING = ("Sol Ring", "Artifact", "{T}: Add {C}{C}.")
MIND_STONE = ("Mind Stone", "Artifact", "{T}: Add {C}.\n{1}, {T}, Sacrifice Mind Stone: Draw a card.")
ELVES = ("Llanowar Elves", "Creature — Elf Druid", "{T}: Add {G}.")
CULTIVATE = ("Cultivate", "Sorcery", "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.")
CURIO = ("Cloudstone Curio", "Artifact", "Whenever a nonartifact permanent enters the battlefield under your control, you may return another target permanent you control "
         "that shares a card type with it to its owner's hand.")
RHYS = ("Rhys the Redeemed", "Legendary Creature — Elf Warrior", "{2}{G/W}, {T}: Create a 1/1 green and white Elf Warrior creature token.\n"
        "{4}{G/W}{G/W}, {T}: For each creature token you control, create a token that's a copy of that creature.")


def roles(spec, **kw):
    return eq.roles_of(card(*spec, **kw))


# (card, {role: (strength, rule id)} it must get, roles it must NOT get)
CASES = [
    (SOL_RING, {"mana-rock": ("core", "mana-rock-taps")}, ("mana-creature",)),
    (ELVES, {"mana-creature": ("core", "mana-creature-taps")}, ("mana-rock",)),
    (("Arcane Signet", "Artifact", "{T}: Add one mana of any color in your commander's color identity."), {"mana-rock": ("core", "mana-rock-taps")}, ()),
    (("Command Tower", "Land", "{T}: Add one mana of any color in your commander's color identity."), {}, ("mana-rock", "mana-creature")),  # a land is a land
    (("Dark Ritual", "Instant", "Add {B}{B}{B}."), {}, ("mana-rock",)),  # a ritual is not a rock
    (CULTIVATE, {"land-ramp": ("core", "land-ramp-fetch")}, ("tutor",)),
    (("Wood Elves", "Creature — Elf Scout", "When Wood Elves enters the battlefield, search your library for a Forest card, put that card onto the battlefield, then shuffle."),
     {"land-ramp": ("core", "land-ramp-fetch")}, ("tutor",)),
    (("Exploration", "Enchantment", "You may play an additional land on each of your turns."), {"land-ramp": ("core", "land-ramp-extra-drop")}, ()),
    (("Evolving Wilds", "Land", "{T}, Sacrifice Evolving Wilds: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle."), {}, ("land-ramp", "tutor")),
    (TITHE, {"treasure": ("core", "treasure-create")}, ("draw-once", "draw-engine")),  # the opponent's draw is a condition, not a draw of yours
    (DOCKSIDE, {"treasure": ("core", "treasure-create")}, ("mana-creature",)),
    (("Deadly Dispute", "Instant", "As an additional cost to cast this spell, sacrifice an artifact or creature.\nDraw two cards and create a Treasure token."),
     {"draw-once": ("core", "draw-once"), "treasure": ("core", "treasure-create")}, ("sacrifice-outlet",)),
    (("Mana Reflection", "Enchantment", "If you tap a permanent for mana, it produces twice as much of that mana instead."), {"mana-multiplier": ("core", "mana-doubling")}, ()),
    (("Mirari's Wake", "Enchantment", "Creatures you control get +1/+1.\nWhenever you tap a land for mana, add one mana of any type that land produced."),
     {"mana-multiplier": ("core", "mana-doubling")}, ()),
    (DIVINATION, {"draw-once": ("core", "draw-once")}, ("draw-engine",)),
    (("Night's Whisper", "Sorcery", "You draw two cards and you lose 2 life."), {"draw-once": ("core", "draw-once")}, ()),
    (("Mulldrifter", "Creature — Elemental", "Flying\nWhen Mulldrifter enters the battlefield, draw two cards.\nEvoke {2}{U} (You may cast this spell for its evoke cost.)"),
     {"draw-once": ("core", "draw-once")}, ("draw-engine",)),  # an enters trigger happens once
    (RHYSTIC, {"draw-engine": ("core", "draw-engine")}, ("draw-once",)),
    (ARENA, {"draw-engine": ("core", "draw-engine")}, ("draw-once",)),
    (REMORA, {"draw-engine": ("core", "draw-engine")}, ()),
    (("Howling Mine", "Artifact", "At the beginning of each player's draw step, if Howling Mine is untapped, that player draws an additional card."),
     {"draw-engine": ("core", "draw-engine")}, ()),
    (("Sensei's Divining Top", "Artifact", "{1}: Look at the top three cards of your library, then put them back in any order.\n{T}: Draw a card, then put Sensei's Divining Top on top of its owner's library."),
     {"draw-engine": ("core", "draw-engine")}, ()),
    (MIND_STONE, {"mana-rock": ("core", "mana-rock-taps"), "draw-once": ("incidental", "draw-once")}, ("draw-engine",)),  # the draw spends the card: once, and a side effect
    (("Psychosis Crawler", "Artifact Creature — Phyrexian Horror", "Psychosis Crawler's power and toughness are each equal to the number of cards in your hand.\nWhenever you draw a card, each opponent loses 1 life."),
     {}, ("draw-once", "draw-engine")),  # a payoff that watches draws
    (("Cycler", "Creature — Wall", "Defender\nCycling {2} ({2}, Discard this card: Draw a card.)"), {}, ("draw-once",)),  # reminder text is not the card's effect
    (("Demonic Tutor", "Sorcery", "Search your library for a card, put that card into your hand, then shuffle."), {"tutor": ("core", "tutor-library")}, ("land-ramp",)),
    (("Regrowth", "Sorcery", "Return target card from your graveyard to your hand."), {"recursion": ("core", "recursion-to-hand")}, ("reanimate",)),
    (("Eternal Witness", "Creature — Human Shaman", "When Eternal Witness enters the battlefield, you may return target card from your graveyard to your hand."), {"recursion": ("core", "recursion-to-hand")}, ()),
    (("Reanimate", "Sorcery", "Put target creature card from a graveyard onto the battlefield under your control. You lose life equal to its mana value."),
     {"reanimate": ("core", "reanimate-to-battlefield")}, ("recursion",)),
    (("Animate Dead", "Enchantment — Aura", "Enchant creature card in a graveyard\nWhen Animate Dead enters the battlefield, if it's on the battlefield, it loses \"enchant creature card in a graveyard\" and gains "
      "\"enchant creature put onto the battlefield with Animate Dead.\" Return enchanted creature card to the battlefield under your control and attach Animate Dead to it."),
     {"reanimate": ("core", "reanimate-to-battlefield")}, ()),
    (RHYS, {"token-maker": ("core", "token-create")}, ("token-doubler",)),
    (("Raise the Alarm", "Instant", "Create two 1/1 white Soldier creature tokens."), {"token-maker": ("core", "token-create")}, ()),
    (DOUBLING_SEASON, {"token-doubler": ("core", "token-doubling"), "counter-doubler": ("core", "counter-doubling")}, ("token-maker",)),
    (PARALLEL_LIVES, {"token-doubler": ("core", "token-doubling")}, ("counter-doubler", "token-maker")),
    (ANOINTED, {"token-doubler": ("core", "token-doubling")}, ("counter-doubler", "token-maker")),
    (MONDRAK, {"token-doubler": ("core", "token-doubling")}, ("counter-doubler",)),
    (VORINCLEX, {"counter-doubler": ("core", "counter-doubling")}, ("token-doubler",)),
    (("Hardened Scales", "Enchantment", "If one or more +1/+1 counters would be put on a creature you control, that many plus one +1/+1 counters are put on it instead."), {}, ("counter-doubler",)),  # adds one, does not double
    (("Cathars' Crusade", "Enchantment", "Whenever a creature enters the battlefield under your control, put a +1/+1 counter on each creature you control."), {}, ("counter-doubler", "token-maker")),
    (COUNTERSPELL, {"counterspell": ("core", "counter-spell")}, ("free-counterspell",)),
    (("Stifle", "Instant", "Counter target activated or triggered ability."), {"counterspell": ("core", "counter-spell")}, ()),
    (GUARDIANSHIP, {"counterspell": ("core", "counter-spell"), "free-counterspell": ("core", "counter-free")}, ()),
    (FORCE, {"counterspell": ("core", "counter-spell"), "free-counterspell": ("core", "counter-free")}, ("bounce",)),
    (DAZE, {"counterspell": ("core", "counter-spell"), "free-counterspell": ("core", "counter-free")}, ("bounce",)),  # returning your own land is a cost, not bounce
    (("Swords to Plowshares", "Instant", "Exile target creature. Its controller gains life equal to its power."), {"spot-removal": ("core", "removal-target")}, ("lifegain", "sweeper")),
    (("Doom Blade", "Instant", "Destroy target nonblack creature."), {"spot-removal": ("core", "removal-target")}, ()),
    (("Lightning Bolt", "Instant", "Lightning Bolt deals 3 damage to any target."), {"spot-removal": ("core", "removal-target")}, ()),
    (("Pacifism", "Enchantment — Aura", "Enchant creature\nEnchanted creature can't attack or block."), {"spot-removal": ("core", "removal-target")}, ()),
    (("Diabolic Edict", "Instant", "Target player sacrifices a creature."), {"spot-removal": ("core", "removal-edict")}, ("sacrifice-outlet",)),
    (("Scavenging Ooze Test", "Instant", "Exile target creature card from a graveyard."), {}, ("spot-removal",)),  # a card in a graveyard is not a permanent
    (RIFT, {"bounce": ("core", "bounce-owner-hand"), "sweeper": ("core", "sweeper-overload")}, ("spot-removal",)),
    (EVACUATION, {"bounce": ("core", "bounce-owner-hand"), "sweeper": ("core", "sweeper-bounce-all")}, ()),
    (UNSUMMON, {"bounce": ("core", "bounce-owner-hand")}, ("sweeper",)),
    (CURIO, {}, ("bounce", "treasure", "token-maker")),  # returns your own permanents: no role the Vault knows
    (("Ephemeral Rescue", "Instant", "Return target creature you control to its owner's hand."), {}, ("bounce",)),  # rescuing your own creature is not bounce
    (WRATH, {"sweeper": ("core", "sweeper-coarse")}, ("spot-removal", "bounce")),
    (("Anger of the Gods", "Sorcery", "Anger of the Gods deals 3 damage to each creature. If a creature dealt damage this way would die this turn, exile it instead."), {"sweeper": ("core", "sweeper-coarse")}, ()),
    (("Own Side", "Sorcery", "Destroy all creatures you control."), {}, ("sweeper",)),
    (("Viscera Seer", "Creature — Vampire Wizard", "Sacrifice a creature: Scry 1."), {"sacrifice-outlet": ("core", "sacrifice-outlet")}, ()),
    (("Heroic Intervention", "Instant", "Permanents you control gain hexproof and indestructible until end of turn."), {"protection": ("core", "protection-grant")}, ()),
    (("Teferi's Protection", "Sorcery", "Until your next turn, your life total can't change and you gain protection from everything. All permanents you control phase out. (While they're phased out, they're treated as though they don't exist. They phase in before you untap during your untap step.)\nExile Teferi's Protection."),
     {"protection": ("core", "protection-grant")}, ()),
    (("Stone Wall", "Creature — Wall", "Indestructible"), {}, ("protection",)),  # a printed keyword is not a role
    (("Healing Salve", "Instant", "Choose one —\n• Target player gains 3 life.\n• Prevent the next 3 damage that would be dealt to any target this turn."), {"lifegain": ("core", "lifegain-gain")}, ()),
    (("Soul Warden", "Creature — Human Cleric", "Whenever another creature enters the battlefield, you gain 1 life."), {"lifegain": ("core", "lifegain-gain")}, ()),
    (("Vampire Nighthawk", "Creature — Vampire Shaman", "Flying, deathtouch, lifelink"), {"lifegain": ("incidental", "lifegain-lifelink")}, ()),
    (("Ajani's Pridemate", "Creature — Cat Soldier", "Whenever you gain life, put a +1/+1 counter on Ajani's Pridemate."), {}, ("lifegain",)),
    (("Levitation", "Enchantment", "Creatures you control have flying."), {"evasion": ("core", "evasion-grant")}, ()),
    (("Whispersilk Cloak", "Artifact — Equipment", "Equipped creature can't be blocked and has shroud.\nEquip {2}"), {"evasion": ("core", "evasion-unblockable")}, ("protection",)),
    (("Serra Angel", "Creature — Angel", "Flying, vigilance"), {}, ("evasion",)),  # a printed keyword is not a role
    (("Grounding Test", "Instant", "Target creature gets -1/-0 and loses flying until end of turn."), {}, ("evasion",)),
    (("Plain Bear", "Creature — Bear", "Trample"), {}, tuple(eq.ROLES)),
]


@pytest.mark.parametrize("spec,wanted,unwanted", CASES, ids=[c[0][0] for c in CASES])
def test_a_rule_gives_the_role_it_should_and_not_the_ones_it_should_not(spec, wanted, unwanted):
    got = roles(spec)
    for role, (strength, rule) in wanted.items():
        assert role in got, (spec[0], {s: h.rule for s, h in got.items()})
        assert (got[role].strength, got[role].rule) == (strength, rule), (spec[0], got[role])
    for role in unwanted:
        assert role not in got, (spec[0], got[role])
    assert list(got) == sorted(got, key=eq.ORDER.get)  # always in vocabulary order


def test_every_role_has_a_rule_and_every_rule_has_a_card_in_the_table_above_that_it_matches():
    matched = {rule for _, wanted, _ in CASES for _, rule in wanted.values()}
    assert {r.id for r in eq.RULES} - matched == set(), "add a card for the rule"
    assert {r.role for r in eq.RULES} == set(eq.ROLES) and len(eq.VOCABULARY) == 22


def test_the_vocabulary_is_the_one_the_owner_agreed():
    assert [r.slug for r in eq.VOCABULARY] == [
        "mana-rock", "mana-creature", "land-ramp", "treasure", "mana-multiplier", "draw-once", "draw-engine", "tutor", "recursion", "reanimate",
        "token-maker", "token-doubler", "counter-doubler", "counterspell", "free-counterspell", "spot-removal", "bounce", "sweeper",
        "sacrifice-outlet", "protection", "lifegain", "evasion"]
    assert all(r.means and r.name and r.needles for r in eq.VOCABULARY)


def test_whether_a_treasure_or_a_token_repeats_is_read_from_the_line_that_makes_it():
    assert roles(TITHE)["treasure"].repeatable is True and roles(DOCKSIDE)["treasure"].repeatable is False
    assert roles(RHYS)["token-maker"].repeatable is True
    spell = roles(("Raise the Alarm", "Instant", "Create two 1/1 white Soldier creature tokens."))["token-maker"]
    assert spell.repeatable is False
    assert roles(DIVINATION)["draw-once"].repeatable is None  # only the two modified roles carry the flag


def test_a_side_effect_is_incidental_when_the_card_has_another_job_and_core_when_it_is_the_job():
    lonely = roles(("Raise the Alarm", "Instant", "Create two 1/1 white Soldier creature tokens."))
    assert lonely["token-maker"].strength == "core"
    rider = roles(("Dockside Rock", "Artifact", "{T}: Add {C}.\nWhen Dockside Rock enters the battlefield, create a Treasure token."))
    assert rider["mana-rock"].strength == "core" and rider["treasure"].strength == "incidental"
    engine = roles(("Treasure Engine", "Artifact", "{T}: Add {C}.\nAt the beginning of your upkeep, create a Treasure token."))
    assert engine["treasure"].strength == "core"  # a repeating Treasure maker is a job of its own


def test_the_back_face_of_a_double_faced_card_is_read_and_its_own_name_does_not_hide_a_rule():
    transform = card("Front // Back", "Creature — Human // Creature — Horror", None,
                     faces=[{"name": "Front", "oracle_text": "Flying"}, {"name": "Back", "oracle_text": "Draw three cards."}])
    assert "draw-once" in eq.roles_of(transform)
    assert "draw-once" in eq.roles_of(card("Gonti, Lord of Luxury", "Legendary Creature", "When Gonti enters, draw two cards."))


def test_the_database_words_find_every_card_with_a_role_in_the_table():
    """The owned cards are narrowed by words in their text (the database does it); a role missing from its own words would hide a card."""
    for spec, wanted, _ in CASES:
        text = (spec[2] or "").lower()
        for role in wanted:
            assert any(n in text for n in eq.ROLES[role].needles), (spec[0], role, eq.ROLES[role].needles)
        got = roles(spec)
        for family_needles in [eq.needles_for(got)] if got else []:
            assert all(any(n in text for n in eq.ROLES[s].needles) for s in got), (spec[0], family_needles)


# -- matching: the owner's examples -----------------------------------------------------------------------------------------------

def m(target, candidate):
    return eq.match(roles(target), roles(candidate))


def test_doubling_season_has_two_jobs_and_anointed_procession_does_the_first_of_them():
    found = m(DOUBLING_SEASON, ANOINTED)
    assert found.tier == eq.SAME and found.shared == ["token-doubler"] and found.lacks == ["counter-doubler"] and found.extra == []
    assert m(DOUBLING_SEASON, PARALLEL_LIVES).tier == eq.SAME  # creature tokens only: the rules do not tell it apart, the Oracle text beside it does
    assert m(DOUBLING_SEASON, MONDRAK).tier == eq.SAME
    vorinclex = m(DOUBLING_SEASON, VORINCLEX)
    assert vorinclex.tier == eq.SIMILAR and vorinclex.shared == ["counter-doubler"] and vorinclex.lacks == ["token-doubler"]


def test_parallel_lives_is_matched_by_the_other_token_doublers_and_not_by_a_counter_doubler():
    assert m(PARALLEL_LIVES, DOUBLING_SEASON).tier == eq.SAME and m(PARALLEL_LIVES, DOUBLING_SEASON).extra == ["counter-doubler"]
    assert m(PARALLEL_LIVES, ANOINTED).tier == eq.SAME
    assert m(PARALLEL_LIVES, VORINCLEX) is None  # a counter doubler is a different job
    assert m(PARALLEL_LIVES, RHYS) is None  # making tokens is not doubling them


def test_rhystic_study_is_matched_by_other_draw_engines_and_a_one_shot_draw_is_only_similar():
    assert m(RHYSTIC, ARENA).tier == eq.SAME and m(RHYSTIC, REMORA).tier == eq.SAME
    once = m(RHYSTIC, DIVINATION)
    assert once.tier == eq.SIMILAR and once.different == [{"kind": "neighbour", "target": "draw-engine", "candidate": "draw-once"}]
    assert m(DIVINATION, RHYSTIC).tier == eq.SIMILAR  # and the other way round
    assert m(RHYSTIC, COUNTERSPELL) is None and m(RHYSTIC, SOL_RING) is None


def test_fierce_guardianship_needs_a_free_counterspell_for_the_same_job():
    assert m(GUARDIANSHIP, FORCE).tier == eq.SAME and m(GUARDIANSHIP, DAZE).tier == eq.SAME
    plain = m(GUARDIANSHIP, COUNTERSPELL)
    assert plain.tier == eq.SIMILAR and plain.lacks == ["free-counterspell"]
    assert plain.different == [{"kind": "lacks_refinement", "target": "free-counterspell", "candidate": "counterspell"}]
    better = m(COUNTERSPELL, FORCE)  # a free counterspell does a plain counterspell's job and more
    assert better.tier == eq.SAME and better.extra == ["free-counterspell"]


def test_cyclonic_rift_is_matched_by_bounce_and_a_board_wipe_is_only_similar():
    assert m(RIFT, EVACUATION).tier == eq.SAME and m(RIFT, EVACUATION).shared == ["bounce", "sweeper"]
    assert m(RIFT, UNSUMMON).tier == eq.SAME and m(RIFT, UNSUMMON).lacks == ["sweeper"]
    wipe = m(RIFT, WRATH)
    assert wipe.tier == eq.SIMILAR and wipe.shared == ["sweeper"] and wipe.lacks == ["bounce"]
    assert m(RIFT, GUARDIANSHIP) is None  # a counterspell is not removal


def test_a_treasure_that_happens_once_is_not_the_same_job_as_one_that_repeats():
    once = m(TITHE, DOCKSIDE)
    assert once.tier == eq.SIMILAR and once.different[0]["kind"] == "repeats" and once.different[0]["candidate_repeats"] is False
    again = ("Revel in Riches", "Enchantment", "Whenever a creature an opponent controls dies, create a Treasure token.\nAt the beginning of your upkeep, if you control ten or more Treasures, you win the game.")
    assert m(TITHE, again).tier == eq.SAME


def test_ramp_neighbours_are_similar_and_a_rock_with_a_side_draw_is_still_a_rock():
    assert m(SOL_RING, MIND_STONE).tier == eq.SAME and m(SOL_RING, MIND_STONE).extra == []  # its draw is a side effect: not a job
    assert m(SOL_RING, ELVES).tier == eq.SIMILAR and m(SOL_RING, CULTIVATE).tier == eq.SIMILAR
    assert m(SOL_RING, RHYSTIC) is None


def test_a_card_with_no_core_role_matches_nothing_and_is_not_offered():
    assert m(CURIO, SOL_RING) is None and m(("Plain Bear", "Creature — Bear", "Trample"), RHYSTIC) is None
    assert eq.primary(roles(CURIO)) is None


def test_the_sentence_under_a_candidate_names_only_roles_the_data_holds():
    pairs = [(DOUBLING_SEASON, ANOINTED), (RHYSTIC, DIVINATION), (GUARDIANSHIP, COUNTERSPELL), (RIFT, WRATH), (TITHE, DOCKSIDE), (SOL_RING, ELVES), (RIFT, EVACUATION)]
    for target, candidate in pairs:
        t, c = roles(target), roles(candidate)
        found = eq.match(t, c)
        sentence = eq.why(found, t, c, target[0]).lower()
        allowed = {eq.ROLES[s].name.lower() for s in list(t) + list(c)}
        mentioned = {r.name.lower() for r in eq.VOCABULARY if r.name.lower() in sentence}
        assert mentioned <= allowed, (target[0], candidate[0], sentence, mentioned - allowed)
        assert sentence.startswith("same job: " if found.tier == eq.SAME else "similar, with a difference: ")
        if found.tier == eq.SAME:
            assert all(c[s].rule in sentence for s in found.shared)  # the rule behind each role is named


def test_mana_value_is_ranked_and_said_not_used_as_a_filter():
    assert eq.mana_value_words(5, 4, "Doubling Season") == "costs 1 less than Doubling Season"
    assert eq.mana_value_words(3, 4.0, "Cultivate") == "costs 1 more than Cultivate"
    assert eq.mana_value_words(2, 2.0, "X") == "costs the same as X" and eq.mana_value_words(None, 2, "X") is None


def test_the_main_type_tells_a_spell_from_a_permanent():
    assert eq.main_type("Artifact Creature — Construct") == "Creature" and eq.main_type("Legendary Enchantment") == "Enchantment"
    assert eq.main_type("Instant // Instant") == "Instant" and eq.main_type("Basic Land — Forest") == "Land" and eq.main_type(None) is None


# -- the doc ----------------------------------------------------------------------------------------------------------------------

def test_every_role_and_rule_is_written_down_in_the_design_doc_with_what_it_gets_wrong():
    for role in eq.VOCABULARY:
        assert f"`{role.slug}`" in DOC, f"document the role {role.slug} in docs/functional-equivalents.md"
    for rule in eq.RULES:
        assert f"`{rule.id}`" in DOC, f"document {rule.id} in docs/functional-equivalents.md"
        assert rule.what and rule.wrong
    assert eq.RULES_VERSION in DOC


def test_no_outside_data_is_read():
    """The matcher is the Vault's own text rules (the owner's decision, and the #62 terms record is not made): it imports no tag
    table, no catalog source and no network client."""
    source = (Path(__file__).resolve().parent.parent / "vault" / "equivalents.py").read_text(encoding="utf-8")
    for forbidden in ("OracleTag", "oracle_tags", "ROLE_TAGS", "httpx", "requests", "urllib", "scryfall_tagger"):
        assert forbidden not in source.replace("Scryfall's community tags", ""), forbidden
