"""Roles worked out from a card's Oracle text, the fallback where Scryfall's Tagger has no tag (#18, docs/card-roles-design.md).

**What this is.** A small set of written rules, each one a pattern over the card's Oracle text and type line, that says "this
card ramps / draws / removes / sweeps / counters / tutors / recurs / sacrifices". The Vault uses a rule's answer only for a
card that has **no Tagger tag for that role** (``deck_tools.roles_of``): Scryfall's community tags are the primary source and
win wherever they speak. A role from a rule is always shown as **computed by the Vault from the card's Oracle text**, with the
rule's id (``basis: "computed"``, ``rule: "ramp-fetch-land"``), never as Scryfall's tag and never as a fact about the card.

**What it is not.** It reads text; it cannot see context (a ramp spell that costs more than it gives, a sweeper that only
hits your own side). The rules below say what each one matches and what it is known to get wrong. They are deliberately
narrow (a miss shows as "no role", a false hit shows a role with its rule id) and every rule has tests in
``tests/test_role_rules.py``. Change a rule there first.

How text is read: reminder text in parentheses is removed (so "Cycling" never looks like drawing), the card's own name is
replaced by ``~``, everything is lower-cased, and every face of a double-faced card is read (``vault.card_faces.all_text``).
The same module finds the two bracket signals the Commander Brackets mention, extra turns and mass land denial (``SIGNALS``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .card_faces import all_text

LAND_WORDS = r"(?:basic |snow |nonbasic )?(?:land|plains|island|swamp|mountain|forest)s?"


@dataclass(frozen=True)
class Rule:
    id: str
    role: str
    what: str                     # one line: what it matches
    wrong: str                    # one line: what it is known to get wrong
    test: Callable[[str, str], bool]  # (text, type_line) -> matches; text is cleaned, lower-case, with the name as ~


def _re(pattern: str) -> Callable[[str, str], bool]:
    compiled = re.compile(pattern)
    return lambda text, types: bool(compiled.search(text))


def _not_land(pattern: str) -> Callable[[str, str], bool]:
    compiled = re.compile(pattern)
    return lambda text, types: "land" not in types.split("//")[0] and bool(compiled.search(text))


def _permanent_not_land(pattern: str) -> Callable[[str, str], bool]:
    compiled = re.compile(pattern)

    def test(text: str, types: str) -> bool:
        front = types.split("//")[0]
        return "land" not in front and any(t in front for t in ("artifact", "creature", "enchantment")) and bool(compiled.search(text))
    return test


def _tutor(text: str, types: str) -> bool:
    """A search of the library whose target is not only lands (``search your library for a basic land`` is ramp, not a tutor)."""
    return any(not re.search(rf"\b{LAND_WORDS}\b", clause) for clause in re.findall(r"search your library for ([^.;\n]*)", text))


def _sacrifice_outlet(text: str, types: str) -> bool:
    return bool(re.search(r"(?:^|[,\n:] ?|\} ?)sacrifice (?:a|an|another|two|three|x|any number of) [^:.\n]*:", text))


RULES: tuple[Rule, ...] = (
    Rule("ramp-add-mana", "ramp", "a non-land artifact, creature or enchantment with an activated 'add' mana ability ({T}: Add ...)",
         "a rock or creature that adds mana only under a condition is still matched; a mana ability that needs a cost per use is too",
         _permanent_not_land(r"\{t\}[^.\n]*: add ")),
    Rule("ramp-fetch-land", "ramp", "a non-land card with 'search your library for ... land ... put it onto the battlefield' (Cultivate-style)",
         "does not check that the land comes in untapped or that the spell is cheap; a land that fetches a land (Evolving Wilds) is deliberately not ramp",
         _not_land(r"search your library for [^.;\n]*\bland\b[^.;\n]*\bonto the battlefield\b")),
    Rule("ramp-extra-land", "ramp", "'you may play an additional land' (or two, or any number)",
         "a card that gives the extra land drop only to opponents is matched too",
         _re(r"play (?:an|one|two|three|any number of) additional lands?|play lands? from the top of your library")),
    Rule("ramp-treasure", "ramp", "creates one or more Treasure tokens",
         "treats every Treasure maker as ramp, including a one-shot reward on a spell that is mostly something else",
         _re(r"creates? [a-z0-9 ]*?treasure tokens?")),
    Rule("draw-cards", "draw", "'you draw', 'draw a card/two/X cards', 'target player draws', 'each player draws'",
         "counts a card that draws only as a drawback or a symmetrical wheel the same as a plain draw spell",
         _re(r"\byou (?:may )?draw\b|(?<!opponent )(?<!opponents )\bdraws? (?:a|an|one|two|three|four|five|seven|x|that many|\d+) cards?\b|\btarget player draws\b|\beach player draws\b")),
    Rule("removal-destroy-exile", "removal", "'destroy' or 'exile' (up to N) target creature, artifact, enchantment, planeswalker, battle or permanent",
         "an optional 'may' or a conditional clause is not read; 'exile target card from a graveyard' is correctly not matched",
         _re(r"\b(?:destroy|exile) (?:up to (?:one|two|three|x) )?(?:another )?target (?:[a-z-]+ )*?(?:creature|permanent|artifact|enchantment|planeswalker|battle)s?\b(?! cards?\b)")),
    Rule("removal-damage", "removal", "'deals N damage to any target / target creature / planeswalker / battle' (burn and fight spells)",
         "damage aimed only at players is not matched; damage shared out 'divided as you choose' is",
         _re(r"deals? (?:\d+|x) damage (?:divided as you choose among (?:one or two|one, two, or three) targets|(?:to )?(?:any target|up to [a-z]+ (?:other )?target|target (?:[a-z-]+ )*?(?:creature|planeswalker|battle)))")),
    Rule("removal-shrink-fight", "removal", "'target creature gets -N/-N', '-X/-X until end of turn', or 'fights target creature'",
         "a -N/-N that only shrinks a creature without killing it is matched; so is a one-sided fight of your own creature",
         _re(r"target creature gets -[0-9x]+/-[0-9x]+|\bfights? (?:up to one )?(?:another )?target creature")),
    Rule("removal-pacify", "removal", "an Aura that says the enchanted creature 'can't attack or block'",
         "the creature stays on the battlefield and can still use abilities", _re(r"enchanted creature can't attack or block")),
    Rule("sweeper-destroy-all", "sweeper", "'destroy' or 'exile' all creatures, permanents, artifacts, enchantments or planeswalkers (not 'you control')",
         "a sweeper that spares a type or has an exception in a later sentence is still matched",
         _re(r"\b(?:destroy|exile) all (?:[a-z-]+ )*?(?:creatures|permanents|artifacts|enchantments|planeswalkers)\b(?! you control)")),
    Rule("sweeper-damage-each", "sweeper", "'deals N damage to each creature' (without 'you control')",
         "damage that only hits some creatures ('each creature with flying') is matched",
         _re(r"damage to each (?:[a-z-]+ )*?creature\b(?! you control)")),
    Rule("sweeper-shrink-all", "sweeper", "'all creatures get -N/-N' or '-X/-X'", "a symmetrical shrink that does not kill a big creature still counts",
         _re(r"\ball (?:other )?creatures get -[0-9x]+/-[0-9x]+")),
    Rule("sweeper-each-sacrifices", "sweeper", "'each player sacrifices all/every creature'", "none known",
         _re(r"each player sacrifices (?:all|every) (?:[a-z-]+ )*?(?:creatures?|permanents?)")),
    Rule("counter-spell", "counterspell", "'counter target spell' or 'counter target ... ability', 'counter that spell'",
         "a counter with a heavy condition (only creature spells, only noncreature) is matched like an unconditional one",
         _re(r"\bcounter (?:target|that|up to one target|the) (?:[a-z-]+ )*?(?:spell|ability)")),
    Rule("tutor-library", "tutor", "'search your library for' something that is not only a land", "a card that fetches a named, narrow card is matched like Demonic Tutor",
         _tutor),
    Rule("recursion-to-hand", "recursion", "'return ... card(s) from your/a/target player's graveyard to ... hand or the battlefield'",
         "a card that returns only itself ('return ~ from your graveyard') is matched too",
         _re(r"\breturn (?:up to (?:one|two|three|x) |target |all |each )?(?:[a-z-]+ )*?cards? from (?:your|a|target player's|their|an opponent's) graveyard to (?:the battlefield|your hand|its owner's hand|their hand)")),
    Rule("recursion-reanimate", "recursion", "'put target ... card from a graveyard onto the battlefield'", "a reanimation of only the card itself is matched too",
         _re(r"\bput (?:up to (?:one|two|three|x) )?(?:target |another target )?(?:[a-z-]+ )*?cards? from (?:your|a|target player's|any|an opponent's) graveyard onto the battlefield")),
    Rule("sacrifice-outlet", "sacrifice_outlet", "a cost of 'Sacrifice a/another/an/two creature(s), permanent ...:' before a colon (a repeatable outlet)",
         "an outlet limited to one type of permanent (a Treasure, a land) is matched like a creature outlet", _sacrifice_outlet),
)

# Not roles, but things the Commander Brackets name (docs/card-roles-design.md): found the same way, shown the same way.
SIGNALS: tuple[Rule, ...] = (
    Rule("extra-turn", "extra_turn", "'take an extra turn', 'takes an extra turn after this one'", "a card that only stops extra turns is not matched, but a rare typo could be",
         _re(r"\btakes? (?:an|one|two|three|\w+) extra turns?\b|\bextra turn after this one\b")),
    Rule("mass-land-denial", "mass_land_denial",
         "Wizards' own description (Commander Brackets, Feb 2025): cards that destroy, exile or bounce several lands, keep lands tapped, or change what mana "
         "several lands make (Armageddon, Ruination, Sunder, Winter Orb, Blood Moon). Matched: destroy/exile/sacrifice/return all lands, lands that don't untap, "
         "'can't untap more than one land', 'nonbasic lands are <type>s'",
         "Wizards published examples and a description, not a list: this finds the clear wordings and misses odd ones; it also matches symmetric effects "
         "and effects limited to nonbasic lands that Wizards might not count",
         _re(r"\b(?:destroy|exile|sacrifice) (?:all|each|every) (?:[a-z-]+ )*?lands?\b(?! you control)|\beach player sacrifices (?:all|\w+) (?:[a-z-]+ )*?lands?\b"
             r"|\breturn all (?:[a-z-]+ )*?lands to their owners' hands|\blands? (?:you don't control )?(?:don't|doesn't) untap|can't untap more than one land"
             r"|\bnonbasic lands are [a-z]+s\b|\beach land is destroyed")),
)

ROLES = tuple(dict.fromkeys(r.role for r in RULES))
BY_ID = {r.id: r for r in RULES + SIGNALS}


def clean(card) -> tuple[str, str]:
    """(text, type line) as the rules read them: reminder text removed, the card's own name as ``~``, lower case."""
    text = all_text(card)
    names = [card.name] + [f.get("name") for f in (getattr(card, "faces", None) or []) if isinstance(f, dict)]
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        text = text.replace(name, "~")
        short = name.split(",")[0]
        if short != name and len(short) > 3:
            text = text.replace(short, "~")  # "Gonti" for "Gonti, Lord of Luxury"
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[ \t]+", " ", text).lower(), (getattr(card, "type_line", None) or "").lower()


def derive(card, roles: tuple[str, ...] | None = None) -> dict[str, str]:
    """The roles a card's text gives it: ``{role: rule id}`` (the first rule that matched for each role)."""
    text, types = clean(card)
    if not text:
        return {}
    out: dict[str, str] = {}
    for rule in RULES:
        if rule.role not in out and (roles is None or rule.role in roles) and rule.test(text, types):
            out[rule.role] = rule.id
    return out


def signals(card) -> dict[str, str]:
    """The bracket signals (``extra_turn``, ``mass_land_denial``) a card's text gives it: ``{signal: rule id}``."""
    text, types = clean(card)
    return {r.role: r.id for r in SIGNALS if text and r.test(text, types)}


def describe(rule_id: str) -> dict:
    r = BY_ID[rule_id]
    return {"rule": r.id, "role": r.role, "matches": r.what, "known_to_get_wrong": r.wrong}
