"""Roles worked out from Oracle text (vault.role_rules, #18): every rule matches the cards it should, misses the ones it
should not, and is written down in docs/card-roles-design.md. The card texts are the short Oracle texts of well-known cards."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from vault import role_rules as rr

DOCS = (Path(__file__).resolve().parent.parent / "docs" / "card-roles-design.md").read_text(encoding="utf-8")


def card(name, type_line, text, faces=None):
    return SimpleNamespace(name=name, type_line=type_line, oracle_text=text, faces=faces)


# (name, type line, text, {role: rule id} it must get, roles it must NOT get)
CASES = [
    ("Sol Ring", "Artifact", "{T}: Add {C}{C}.", {"ramp": "ramp-add-mana"}, ()),
    ("Llanowar Elves", "Creature — Elf Druid", "{T}: Add {G}.", {"ramp": "ramp-add-mana"}, ()),
    ("Command Tower", "Land", "{T}: Add one mana of any color in your commander's color identity.", {}, ("ramp",)),  # a land is not ramp
    ("Dark Ritual", "Instant", "Add {B}{B}{B}.", {}, ("ramp",)),  # a ritual is not ramp
    ("Cultivate", "Sorcery", "Search your library for up to two basic land cards, reveal those cards, put one onto the battlefield tapped and the other into your hand, then shuffle.",
     {"ramp": "ramp-fetch-land"}, ("tutor",)),
    ("Exploration", "Enchantment", "You may play an additional land on each of your turns.", {"ramp": "ramp-extra-land"}, ()),
    ("Smothering Tithe", "Enchantment", "Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token.",
     {"ramp": "ramp-treasure"}, ("draw",)),  # the opponent's draw is not yours
    ("Divination", "Sorcery", "Draw two cards.", {"draw": "draw-cards"}, ()),
    ("Rhystic Study", "Enchantment", "Whenever an opponent casts a spell, you may pay {1}. If that player doesn't, you draw a card.", {"draw": "draw-cards"}, ()),
    ("Cycler", "Creature — Wall", "Defender\nCycling {2} ({2}, Discard this card: Draw a card.)", {}, ("draw",)),  # reminder text is not the card's effect
    ("Swords to Plowshares", "Instant", "Exile target creature. Its controller gains life equal to its power.", {"removal": "removal-destroy-exile"}, ("sweeper",)),
    ("Doom Blade", "Instant", "Destroy target nonblack creature.", {"removal": "removal-destroy-exile"}, ()),
    ("Anguished Unmaking", "Instant", "Exile target nonland permanent. You lose 3 life.", {"removal": "removal-destroy-exile"}, ()),
    ("Cleanse", "Sorcery", "Exile target card from a graveyard.", {}, ("removal",)),  # a graveyard card is not a permanent
    ("Lightning Bolt", "Instant", "Lightning Bolt deals 3 damage to any target.", {"removal": "removal-damage"}, ()),
    ("Disfigure", "Instant", "Target creature gets -2/-2 until end of turn.", {"removal": "removal-shrink-fight"}, ()),
    ("Pacifism", "Enchantment — Aura", "Enchant creature\nEnchanted creature can't attack or block.", {"removal": "removal-pacify"}, ()),
    ("Wrath of God", "Sorcery", "Destroy all creatures. They can't be regenerated.", {"sweeper": "sweeper-destroy-all"}, ("removal",)),
    ("Anger of the Gods", "Sorcery", "Anger of the Gods deals 3 damage to each creature. If a creature dealt damage this way would die this turn, exile it instead.",
     {"sweeper": "sweeper-damage-each"}, ()),
    ("Toxic Deluge", "Sorcery", "As an additional cost to cast this spell, pay X life.\nAll creatures get -X/-X until end of turn.", {"sweeper": "sweeper-shrink-all"}, ()),
    ("Players Sweep", "Sorcery", "Each player sacrifices all creatures they control.", {"sweeper": "sweeper-each-sacrifices"}, ()),
    ("Your Own", "Sorcery", "Destroy all creatures you control.", {}, ("sweeper",)),  # a sweeper of your own side only
    ("Counterspell", "Instant", "Counter target spell.", {"counterspell": "counter-spell"}, ()),
    ("Stifle", "Instant", "Counter target activated or triggered ability.", {"counterspell": "counter-spell"}, ()),
    ("Demonic Tutor", "Sorcery", "Search your library for a card, put that card into your hand, then shuffle.", {"tutor": "tutor-library"}, ()),
    ("Evolving Wilds", "Land", "{T}, Sacrifice Evolving Wilds: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.", {}, ("tutor", "ramp")),
    ("Regrowth", "Sorcery", "Return target card from your graveyard to your hand.", {"recursion": "recursion-to-hand"}, ()),
    ("Reanimate", "Sorcery", "Put target creature card from a graveyard onto the battlefield under your control. You lose life equal to its mana value.",
     {"recursion": "recursion-reanimate"}, ()),
    ("Viscera Seer", "Creature — Vampire Wizard", "Sacrifice a creature: Scry 1.", {"sacrifice_outlet": "sacrifice-outlet"}, ()),
    ("Ashnod's Altar", "Artifact", "Sacrifice a creature: Add {C}{C}.", {"sacrifice_outlet": "sacrifice-outlet"}, ("ramp",)),
    ("Treasure-ish", "Artifact — Treasure", "{T}, Sacrifice this artifact: Add one mana of any color.", {"ramp": "ramp-add-mana"}, ("sacrifice_outlet",)),
    ("Plain Bear", "Creature — Bear", "Trample", {}, ("ramp", "draw", "removal", "sweeper", "counterspell", "tutor", "recursion", "sacrifice_outlet")),
]


@pytest.mark.parametrize("name,types,text,wanted,unwanted", CASES, ids=[c[0] for c in CASES])
def test_a_rule_gives_the_role_it_should_and_not_the_ones_it_should_not(name, types, text, wanted, unwanted):
    got = rr.derive(card(name, types, text))
    for role, rule in wanted.items():
        assert got.get(role) == rule, (name, got)
    for role in unwanted:
        assert role not in got, (name, got)


def test_every_role_rule_has_a_card_that_matches_it_in_the_table_above():
    matched = {rule for *_, wanted, _ in CASES for rule in wanted.values()}
    assert {r.id for r in rr.RULES} - matched == set()


def test_every_rule_is_written_down_in_the_roles_design_doc_with_what_it_gets_wrong():
    for rule in rr.RULES + rr.SIGNALS:
        assert f"`{rule.id}`" in DOCS, f"document {rule.id} in docs/card-roles-design.md"
        assert rule.what and rule.wrong and rr.describe(rule.id)["known_to_get_wrong"] == rule.wrong


def test_a_faced_cards_text_on_its_back_face_is_read():
    transform = card("Front // Back", "Creature — Human // Creature — Horror", None,
                     faces=[{"name": "Front", "oracle_text": "Flying"}, {"name": "Back", "oracle_text": "{T}: Destroy target creature."}])
    assert rr.derive(transform).get("removal") == "removal-destroy-exile"


def test_the_cards_own_name_in_its_text_does_not_hide_a_rule():
    assert rr.derive(card("Fire // Ice", "Instant // Instant", "Fire deals 2 damage divided as you choose among one or two targets.",
                          faces=[{"name": "Fire", "oracle_text": "Fire deals 2 damage divided as you choose among one or two targets."}]))["removal"] == "removal-damage"
    assert rr.derive(card("Gonti, Lord of Luxury", "Legendary Creature", "When Gonti enters, target opponent... Draw two cards."))["draw"] == "draw-cards"


SIGNAL_CASES = [
    ("Time Warp", "Instant", "Target player takes an extra turn after this one.", {"extra_turn": "extra-turn"}),
    ("Nexus of Fate", "Instant", "Take an extra turn after this one. If Nexus of Fate would be put into a graveyard, shuffle it into its owner's library instead.", {"extra_turn": "extra-turn"}),
    ("Armageddon", "Sorcery", "Destroy all lands.", {"mass_land_denial": "mass-land-denial"}),
    ("Ruination", "Sorcery", "Destroy all nonbasic lands.", {"mass_land_denial": "mass-land-denial"}),
    ("Sunder", "Sorcery", "Return all lands to their owners' hands.", {"mass_land_denial": "mass-land-denial"}),
    ("Winter Orb", "Artifact", "As long as Winter Orb is untapped, players can't untap more than one land during their untap steps.", {"mass_land_denial": "mass-land-denial"}),
    ("Blood Moon", "Enchantment", "Nonbasic lands are Mountains.", {"mass_land_denial": "mass-land-denial"}),
    ("Back to Basics", "Enchantment", "Nonbasic lands don't untap during their controllers' untap steps.", {"mass_land_denial": "mass-land-denial"}),
    ("Stone Rain", "Sorcery", "Destroy target land.", {}),  # one land is not mass denial
    ("Own Lands", "Sorcery", "Sacrifice all lands you control.", {}),
    ("Plain Bear", "Creature — Bear", "Trample", {}),
]


@pytest.mark.parametrize("name,types,text,wanted", SIGNAL_CASES, ids=[c[0] for c in SIGNAL_CASES])
def test_the_bracket_signals_follow_wizards_description(name, types, text, wanted):
    assert rr.signals(card(name, types, text)) == wanted
