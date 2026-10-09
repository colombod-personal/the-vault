"""Functional equivalents (#166, docs/functional-equivalents.md): what a card does in the Vault's own 22-role vocabulary, and
which cards do the same job as another.

**What this is.** A small vocabulary of jobs a player recognises (a mana rock, a token doubler, a free counterspell, bounce ...), a
written rule for each over the card's Oracle text and type line, and one matcher that says whether a candidate card does the
same job as a target card. Everything here is the Vault's own deterministic work: the inputs are the Oracle text and the type line
the catalog holds, and every role it gives is shown as ``basis: "computed"`` with the id of the rule that found it. Nothing here
reads Scryfall's community tags (the Tagger): they stay off until the terms check is recorded on #62, and no tag, popularity
figure or other outside data is used.

**What it is not.** It reads words, not context. A role is a hint with a reason, never "this is the same card". A rule that
misses shows as "no role known" (which the answer says is not the same as "you own nothing like it"); a rule that overreaches
shows its rule id, and ``RULES`` below says, for each one, what it matches and what it is known to get wrong. Change a rule
here and in ``tests/test_equivalents.py`` together, and in the table of ``docs/functional-equivalents.md`` (a test fails when
the three disagree).

**Jobs and tiers.** Each role has a *family* (the draw roles are one family, the counter roles another). Of a target card's
``core`` roles (the card exists to do it; ``incidental`` is a side effect), the first in vocabulary order is its *primary* job.

- **Same job:** the candidate has the target's primary job as a core role, and where the target's job has a modifier the
  candidate agrees (a repeating Treasure maker for a repeating one, a free counterspell for a free one).
- **Similar, with a difference:** the candidate shares another core role with the target, or has a role of the same family
  (a card that draws once, offered for one that draws every turn), or the primary job differs only in a modifier.
- Anything else is not offered. The two tiers are never merged, and each candidate says which roles of the target it lacks.

Text is read like ``role_rules``: reminder text removed, the card's own name as ``~``, lower case, every face of a double-faced
card. Whether a draw or a Treasure repeats is read from the line that makes it: a ``whenever``, ``at the beginning of`` or an
activated ability on a permanent repeats; a spell, an ``enters`` trigger, or a cost that sacrifices the card itself does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from . import role_rules as rr

RULES_VERSION = "2026-10-09.1"  # bump with any change to a rule, the vocabulary or the matcher: the answers carry it
BASIS = "computed"


@dataclass(frozen=True)
class Role:
    slug: str
    name: str
    means: str       # in the Vault's own words
    family: str
    needles: tuple[str, ...]  # a card with this role has at least one of these words in its text: lets the database narrow the search


# The order is the priority: a card's primary job is its first core role in this order. Slugs are stable, names may be reworded.
VOCABULARY: tuple[Role, ...] = (
    Role("mana-rock", "Mana rock", "A non-creature permanent that taps for mana", "ramp", ("add ",)),
    Role("mana-creature", "Mana creature", "A creature that taps for mana", "ramp", ("add ",)),
    Role("land-ramp", "Land ramp", "Puts lands onto the battlefield or allows extra land drops", "ramp", ("land", "plains", "island", "swamp", "mountain", "forest")),
    Role("treasure", "Treasure", "Makes Treasure tokens, once or again and again", "ramp", ("treasure",)),
    Role("mana-multiplier", "Mana doubler", "Makes lands or other sources produce more mana", "ramp", ("for mana",)),
    Role("draw-once", "Card draw (once)", "A spell or effect that draws a fixed amount, one time", "draw", ("draw",)),
    Role("draw-engine", "Card draw (every turn or every trigger)", "Draws again and again while it stays", "draw", ("draw",)),
    Role("tutor", "Tutor", "Searches the library for a card that is not just a land", "tutor", ("search your library",)),
    Role("recursion", "Get cards back", "Returns cards from the graveyard to hand", "graveyard", ("graveyard",)),
    Role("reanimate", "Reanimate", "Returns creatures from a graveyard to the battlefield", "graveyard", ("graveyard",)),
    Role("token-maker", "Token maker", "Creates creature or other tokens, once or again and again", "tokens", ("token",)),
    Role("token-doubler", "Token doubler", "Doubles the tokens you create", "token-doubling", ("token",)),
    Role("counter-doubler", "Counter doubler", "Doubles the counters put on your permanents", "counter-doubling", ("counter",)),
    Role("counterspell", "Counterspell", "Counters a spell or ability", "counter", ("counter",)),
    Role("free-counterspell", "Free counterspell", "A counterspell that can be cast without paying its mana cost", "counter", ("counter",)),
    Role("spot-removal", "Spot removal", "Removes or neutralises one target permanent", "removal",
         ("destroy", "exile", "damage", "gets -", "fight", "can't attack or block", "sacrifices")),
    Role("bounce", "Bounce", "Returns permanents to their owners' hands (one or all)", "removal", ("return",)),
    Role("sweeper", "Board wipe", "Removes many permanents at once", "removal", ("destroy", "exile", "damage", "-x/-x", "-1/-1", "sacrifices", "overload", "return")),
    Role("sacrifice-outlet", "Sacrifice outlet", "Lets you sacrifice permanents again and again", "sacrifice", ("sacrifice",)),
    Role("protection", "Protect my stuff", "Keeps your permanents from removal or counters", "protection", ("hexproof", "indestructible", "protection from", "shroud", "phase", "regenerate")),
    Role("lifegain", "Lifegain", "Gains life", "lifegain", ("gain", "lifelink")),
    Role("evasion", "Evasion", "Makes creatures hard to block", "evasion", ("flying", "menace", "shadow", "fear", "intimidate", "horsemanship", "blocked")),
)
ROLES = {r.slug: r for r in VOCABULARY}
ORDER = {r.slug: i for i, r in enumerate(VOCABULARY)}
# A refinement is a role that only exists on top of another: a free counterspell is a counterspell that is also free.
REFINES = {"free-counterspell": "counterspell"}
# Roles that are usually a side effect: they are incidental on a card that has another core job, unless they repeat.
RIDERS = ("treasure", "token-maker", "draw-once", "protection", "lifegain", "evasion")
# Roles whose Treasure or tokens can repeat or happen once: a different modifier is a different (similar) job.
MODIFIED = ("treasure", "token-maker")


# -- reading a card ---------------------------------------------------------------------------------------------------------------

@dataclass
class Read:
    """A card as the rules read it."""
    text: str
    types: str            # the whole type line, lower case
    front: str            # the front face's types (the one that is played)
    lines: list[str]      # the text, one ability per line
    stripped: list[str]   # the same lines without trigger conditions that only watch an effect ("whenever you draw a card,")

    @property
    def permanent(self) -> bool:
        return any(t in self.front for t in ("creature", "artifact", "enchantment", "planeswalker", "land", "battle"))

    @property
    def creature(self) -> bool:
        return "creature" in self.front

    @property
    def land(self) -> bool:
        return "land" in self.front


_CONDITION = re.compile(r"\b(?:whenever|each time|when|if|as long as)\b[^,.:;\n]*?\b(?:draws?|drawn|creates?|created|would draw|would create)\b[^,.:;\n]*,")


def read(card) -> Read:
    text, types = rr.clean(card)
    lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip() != "//"]
    return Read(text, types, types.split("//")[0], lines, [_CONDITION.sub("", l) for l in lines])


def _repeats(line: str, permanent: bool) -> bool:
    """Whether the effect on this line happens again and again: a trigger or an activated ability of a permanent."""
    if not permanent:
        return False
    cost = line.split(":")[0] if ":" in line else ""
    if cost and re.search(r"\b(?:sacrifice|discard|exile) (?:~|this )", cost):
        return False  # a cost that spends the card itself: one time
    if re.match(r"(?:when|whenever) ~ (?:enters|dies|leaves the battlefield|is put into a graveyard)\b", line) and not re.search(r"\b(?:at the beginning of|each time)\b", line):
        return False
    if re.search(r"\bwhenever\b|\bat the beginning of\b|\beach time\b|\bevery time\b", line):
        return True
    return ":" in line  # an activated ability (a cost, a colon, an effect) or a loyalty ability


# -- the rules --------------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    id: str
    role: str
    what: str                                  # one line: what it matches
    wrong: str                                 # one line: what it is known to get wrong
    test: Callable[[Read], bool]               # matches
    repeat: Callable[[Read], bool] | None = None  # for the roles with a modifier: does it repeat
    strength: str | None = None                # None: core unless a rider (see RIDERS); "incidental": always a side effect


def _lines(pattern: str, stripped: bool = True) -> Callable[[Read], list[str]]:
    """The lines (as written, not stripped) in which the pattern is found, looking at the stripped line or the written one."""
    compiled = re.compile(pattern)
    return lambda r: [l for l, s in zip(r.lines, r.stripped) if compiled.search(s if stripped else l)]


def _any(pattern: str, stripped: bool = True) -> Callable[[Read], bool]:
    found = _lines(pattern, stripped)
    return lambda r: bool(found(r))


def _grants(pattern: str) -> Callable[[Read], bool]:
    """A line that gives something (not one that takes it away: 'loses flying')."""
    compiled = re.compile(pattern)
    return lambda r: any(compiled.search(l) and not re.search(r"\blos(?:e|es)\b", l) for l in r.lines)


def _tap_mana(permanent_ok: Callable[[Read], bool]) -> Callable[[Read], bool]:
    found = _lines(r"\{t\}[^.\n]*: add ")
    return lambda r: not r.land and permanent_ok(r) and bool(found(r))


def _coarse(*ids: str) -> Callable[[Read], bool]:
    """The existing coarse rules of ``role_rules`` (one source for the patterns both use)."""
    return lambda r: any(rr.BY_ID[i].test(r.text, r.types) for i in ids)


DRAW = (r"\bdraws? (?:(?:a|an|one|two|three|four|five|six|seven|eight|x|that many|\d+|an additional|one additional|two additional) "
        r"(?:additional )?cards?|cards equal to|that many cards)\b")
_DRAW = re.compile(r"(?<!opponent )(?<!opponents )" + DRAW)


def _draw_lines(r: Read) -> list[str]:
    return [l for l, s in zip(r.lines, r.stripped) if _DRAW.search(s)]


def _draws(r: Read, repeating: bool) -> bool:
    return any(_repeats(l, r.permanent) == repeating for l in _draw_lines(r))


_TOKEN = re.compile(r"\bcreates? ((?!twice\b|three times\b)[^.\n]*?)\btokens?\b")
_TREASURE = re.compile(r"\bcreates? [a-z0-9{}/ ]*?treasure tokens?")


def _token_lines(r: Read) -> list[str]:
    return [l for l, s in zip(r.lines, r.stripped) if "would be created" not in l and any("treasure" not in m.group(1) for m in _TOKEN.finditer(s))]


def _treasure_lines(r: Read) -> list[str]:
    return [l for l, s in zip(r.lines, r.stripped) if _TREASURE.search(s)]


_BOUNCE = re.compile(r"\breturn (?:up to (?:one|two|three|x) )?(?:target|all|each|another target|that|those) (?:(?!graveyard)[a-z0-9'’,/+ -])*?"
                     r"(?:permanents?|creatures?|artifacts?|enchantments?|planeswalkers?|lands?|spells?|tokens?|battles?)\b"
                     r"(?:(?!graveyard)[^.\n])*? to (?:its|their) owner(?:['’]s|s['’])? hands?\b")


def _bounce(r: Read) -> bool:
    return any(m for l in r.lines for m in [_BOUNCE.search(l)] if m and "you control" not in m.group(0))


def _bounce_all(r: Read) -> bool:
    return any(m for l in r.lines for m in [_BOUNCE.search(l)] if m and re.match(r"return (?:all|each)\b", m.group(0)) and "you control" not in m.group(0))


_COUNTER = rr.BY_ID["counter-spell"]
_FREE = re.compile(r"without paying (?:its|this spell's) mana cost|rather than pay (?:this spell's|its) mana cost")


def _free_counter(r: Read) -> bool:
    return _COUNTER.test(r.text, r.types) and bool(_FREE.search(r.text))


def _overload_sweeper(r: Read) -> bool:
    return "overload {" in r.text and (_bounce(r) or _coarse("removal-destroy-exile", "removal-damage", "removal-shrink-fight")(r))


GAIN_AMOUNT = r"(?:\d+|x|one|two|three|four|five|six|seven|eight|nine|ten|twenty|that much)"
KEYWORD_EVASION = r"(?:flying|menace|shadow|fear|intimidate|horsemanship)"
OWN = r"(?:creatures you control|other creatures you control|each creature you control|all creatures you control|target creature(?: you control)?|enchanted creature|equipped creature)"

RULES: tuple[Rule, ...] = (
    Rule("mana-rock-taps", "mana-rock", "a non-creature artifact or enchantment with an activated '{T}: Add ...' ability",
         "a rock that adds mana only under a condition, or at a cost per use, is matched like Sol Ring; a Treasure-making card is not a rock",
         _tap_mana(lambda r: not r.creature and any(t in r.front for t in ("artifact", "enchantment")))),
    Rule("mana-creature-taps", "mana-creature", "a creature that is not a land with an activated '{T}: Add ...' ability",
         "a creature that adds mana only under a condition is matched; a land creature (Dryad Arbor) is deliberately a land, not a mana creature",
         _tap_mana(lambda r: r.creature)),
    Rule("land-ramp-fetch", "land-ramp", "a non-land card that searches the library for a land (or a basic type) and puts it onto the battlefield",
         "does not check that the land comes in untapped or that the spell is cheap; a land that fetches a land (Evolving Wilds) is deliberately not ramp",
         lambda r: not r.land and bool(re.search(r"search your library for [^.;\n]*\b(?:lands?|plains|islands?|swamps?|mountains?|forests?)\b[^.;\n]*\bonto the battlefield\b", r.text))),
    Rule("land-ramp-extra-drop", "land-ramp", "'you may play an additional land' (or two, or any number), 'play lands from the top of your library', or a land put onto the battlefield from hand",
         "a card that gives the extra land drop only to opponents is matched too",
         lambda r: not r.land and bool(re.search(r"play (?:an|one|two|three|any number of) additional lands?|play lands? from the top of your library|put (?:a|up to one) lands? cards? from your hand onto the battlefield", r.text))),
    Rule("treasure-create", "treasure", "creates one or more Treasure tokens; repeats when it is a trigger or an activated ability of a permanent",
         "a one-shot Treasure on a spell that is mostly something else is matched but marked incidental; a payoff that only watches Treasures being created is not",
         lambda r: bool(_treasure_lines(r)), lambda r: any(_repeats(l, r.permanent) for l in _treasure_lines(r))),
    Rule("mana-doubling", "mana-multiplier", "'if you tap a permanent for mana, it produces twice as much', 'whenever you tap a land for mana, add one mana of any type', 'adds an additional'",
         "matches symmetrical versions that help every player; an effect worded in a way not listed here is missed",
         _any(r"\b(?:tap|taps|tapped)\b[^.\n]*?\bfor mana\b[^.\n]*?(?:produces? (?:twice|three times|an additional|one additional)|adds? (?:an additional|one additional|one mana of any (?:type|color)|that much|an amount))")),
    Rule("draw-once", "draw-once", "'draw a card/two/X cards' in a spell, an 'enters' trigger or an ability that spends the card itself",
         "a symmetrical wheel or a draw that is only a drawback is matched like a plain draw spell; a payoff that only watches draws ('whenever you draw') is not",
         lambda r: _draws(r, False)),
    Rule("draw-engine", "draw-engine", "a draw in a 'whenever', 'at the beginning of' or activated ability of a permanent (Phyrexian Arena, Rhystic Study)",
         "a conditional trigger that rarely happens is matched like an upkeep draw; so is a creature that draws when it deals damage",
         lambda r: _draws(r, True)),
    Rule("tutor-library", "tutor", "'search your library for' something that is not only a land", "a card that fetches a named, narrow card is matched like Demonic Tutor",
         _coarse("tutor-library")),
    Rule("recursion-to-hand", "recursion", "'return ... card(s) from a graveyard to your/its owner's hand'",
         "a card that returns only itself ('return ~ from your graveyard') is not matched; one that returns cards of an opponent's graveyard is",
         _any(r"\breturn [^.\n]*?\bcards? [^.\n]*?from [^.\n]*?graveyards? to (?:your|its owner(?:'s)?|their|that player(?:'s)?) hands?\b", False)),
    Rule("reanimate-to-battlefield", "reanimate", "'return/put ... card from a graveyard onto the battlefield', and 'return enchanted creature card to the battlefield' (Animate Dead)",
         "a reanimation of only the card itself is not matched; a card that puts any card, not only creatures, is matched",
         _any(r"\b(?:return|put) [^.\n]*?\bcards? [^.\n]*?from [^.\n]*?graveyards? (?:to|onto) the battlefield\b|\breturn enchanted creature card to the battlefield\b", False)),
    Rule("token-create", "token-maker", "'create ... token(s)' that are not only Treasure; repeats when it is a trigger or an activated ability of a permanent",
         "a one-shot token on a spell or creature is core unless the card has another job; 'create a token that's a copy' is matched like any other token",
         lambda r: bool(_token_lines(r)), lambda r: any(_repeats(l, r.permanent) for l in _token_lines(r))),
    Rule("token-doubling", "token-doubler", "'if an effect would create one or more tokens under your control, it creates twice that many' (and the 'would be created' wording)",
         "a doubler that applies only to some tokens (creature tokens: Parallel Lives) is matched like one for all tokens",
         lambda r: any("token" in l and re.search(r"would (?:create|be created)", l) and re.search(r"twice that many|three times that many", l)
                       and re.search(r"under your control|you would", l) for l in r.lines)),
    Rule("counter-doubling", "counter-doubler", "'if you would put one or more counters ... put/it puts twice that many'",
         "an effect that adds one more ('that many plus one': Hardened Scales) is deliberately not a doubler; a doubler for only some counters is matched like one for all",
         lambda r: any(re.search(r"would put one or more [^.\n]*counters?", l) and re.search(r"twice that many|three times that many", l)
                       and re.search(r"you control|you would", l) for l in r.lines)),
    Rule("counter-spell", "counterspell", "'counter target spell' or 'counter target ... ability', 'counter that spell'",
         "a counter with a heavy condition (only creature spells, only noncreature) is matched like an unconditional one",
         _coarse("counter-spell")),
    Rule("counter-free", "free-counterspell", "a counterspell whose text also lets it be cast 'without paying its mana cost' or 'rather than pay this spell's mana cost'",
         "a counterspell with a free mode that has a steep cost (exiling a card, returning a land) is matched like one that is free in a plain situation",
         _free_counter),
    Rule("removal-target", "spot-removal", "'destroy' or 'exile' target creature/artifact/enchantment/planeswalker/permanent, damage to a target, '-N/-N', fights, 'can't attack or block' Auras",
         "an optional 'may' or a conditional clause is not read; 'exile target creature card from a graveyard' is correctly not matched",
         _coarse("removal-destroy-exile", "removal-damage", "removal-shrink-fight", "removal-pacify")),
    Rule("removal-edict", "spot-removal", "'target player/opponent sacrifices a creature' (an edict)", "an edict that the opponent can dodge by having a worse creature is matched like a precise removal spell",
         _any(r"\b(?:target (?:player|opponent)|each opponent|defending player) sacrifices (?:a|an|another|one)\b", False)),
    Rule("bounce-owner-hand", "bounce", "'return target/all/each ... permanent, creature, artifact, enchantment, planeswalker, land or spell to its owner's hand' (not your own permanents, not cards from a graveyard)",
         "a bounce that only rescues your own card ('target creature you control') is deliberately left out; a bounce with a restriction ('attacking creature') is matched like an unrestricted one",
         _bounce),
    Rule("sweeper-coarse", "sweeper", "'destroy/exile all creatures/permanents/...' (not 'you control'), damage to each creature, 'all creatures get -X/-X', 'each player sacrifices all creatures'",
         "a sweeper that spares a type or has an exception in a later sentence is still matched",
         _coarse("sweeper-destroy-all", "sweeper-damage-each", "sweeper-shrink-all", "sweeper-each-sacrifices")),
    Rule("sweeper-bounce-all", "sweeper", "'return all/each creatures/permanents ... to their owners' hands'",
         "a mass bounce that hits only attackers or one colour is matched like a full wipe", _bounce_all),
    Rule("sweeper-overload", "sweeper", "a spell with Overload that is also a bounce or removal spell (Overload turns 'target' into 'each')",
         "the overload cost is often very high; it is read as the card's identity, not as a common mode", _overload_sweeper),
    Rule("sacrifice-outlet", "sacrifice-outlet", "a cost of 'Sacrifice a/another/an/two creature(s), permanent ...:' before a colon (a repeatable outlet)",
         "an outlet limited to one type of permanent (a Treasure, a land) is matched like a creature outlet", _coarse("sacrifice-outlet")),
    Rule("protection-grant", "protection", "permanents you control (or a target one) gain/have hexproof, indestructible, shroud or protection from something; 'phase out'",
         "a keyword the card has printed on itself is not matched; a protection that only helps one named permanent is matched like a group effect",
         lambda r: any("you control" in l and re.search(r"\b(?:gains?|have|has)\b", l) and re.search(r"\b(?:hexproof|indestructible|shroud|protection from)\b", l) for l in r.lines)
         or any("you control" in l and re.search(r"\bphases? out\b", l) for l in r.lines)),
    Rule("lifegain-gain", "lifegain", "'you gain N life', 'you gain life equal to', 'target/each player gains N life'",
         "an opponent's gain that is a drawback for them ('its controller gains life equal to its power') is not matched; a payoff that only watches life gain is not",
         _any(r"\byou(?: may)? gain " + GAIN_AMOUNT + r" life\b|\b(?:you|target player|each player)(?: may)? gains? life equal to\b|\b(?:target player|each player|that player) gains? " + GAIN_AMOUNT + r" life\b", False)),
    Rule("lifegain-lifelink", "lifegain", "lifelink (a keyword, or granted to creatures)", "always a side effect of a creature, so always marked incidental",
         _any(r"(?:^|, )lifelink\b|\bgains? lifelink\b|\bhave lifelink\b|\bhas lifelink\b", False), strength="incidental"),
    Rule("evasion-grant", "evasion", "creatures you control, a target creature, or an Aura/Equipment's creature gain/have flying, menace, shadow, fear, intimidate or horsemanship",
         "a keyword the creature has printed on itself is not matched; trample is not counted as evasion",
         _grants(OWN + r"[^.\n]*?\b(?:gains?|have|has)\b[^.\n]*?\b" + KEYWORD_EVASION + r"\b")),
    Rule("evasion-unblockable", "evasion", "creatures you control or a target creature 'can't be blocked'", "'can't be blocked by' a narrow kind of creature is matched like a plain unblockable",
         _grants(OWN + r"[^.\n]*?can't be blocked")),
)
BY_ID = {r.id: r for r in RULES}
assert {r.role for r in RULES} == set(ROLES), "every role has a rule"


# -- roles of a card --------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    role: str
    strength: str            # "core" | "incidental"
    rule: str                # the id of the rule that found it
    repeatable: bool | None  # for the roles with a modifier (treasure, token-maker); else None

    @property
    def name(self) -> str:
        return ROLES[self.role].name


_CACHE: dict[tuple, dict[str, Hit]] = {}
_CACHE_MAX = 30000


def roles_of(card) -> dict[str, Hit]:
    """``{slug: Hit}`` for a catalog card, in vocabulary order. Cached per (oracle id, content hash) in this process."""
    key = (getattr(card, "oracle_id", None), getattr(card, "content_hash", None))
    if key[0] and key[1] and key in _CACHE:
        return _CACHE[key]
    found = derive(read(card))
    if key[0] and key[1]:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = found
    return found


def derive(r: Read) -> dict[str, Hit]:
    raw: dict[str, tuple[Rule, bool | None]] = {}
    for rule in RULES:
        if rule.role not in raw and r.text and rule.test(r):
            raw[rule.role] = (rule, rule.repeat(r) if rule.repeat else None)
    main = [s for s, (rule, rep) in raw.items() if s not in RIDERS or (s in MODIFIED and rep)]
    out: dict[str, Hit] = {}
    for slug in sorted(raw, key=ORDER.get):
        rule, rep = raw[slug]
        strength = rule.strength or ("incidental" if slug in RIDERS and not (slug in MODIFIED and rep) and main else "core")
        out[slug] = Hit(slug, strength, rule.id, rep if slug in MODIFIED else None)
    return out


def core(roles: dict[str, Hit]) -> dict[str, Hit]:
    return {s: h for s, h in roles.items() if h.strength == "core"}


def needles_for(roles: dict[str, Hit]) -> list[str]:
    """Words of which at least one is in the text of every card that could match a target with these core roles: the database
    narrows the owned cards with them, and the rules then run on that handful. Every role of a family is included."""
    families = {ROLES[s].family for s in roles}
    return sorted({n for r in VOCABULARY if r.family in families for n in r.needles})


def main_type(types: str) -> str | None:
    """The one type a card is mainly (``Creature`` before ``Artifact``), from its type line."""
    front = (types or "").split("//")[0].split("—")[0]
    return next((t for t in ("Creature", "Planeswalker", "Land", "Artifact", "Enchantment", "Instant", "Sorcery", "Battle") if t in front), None)


# -- matching ---------------------------------------------------------------------------------------------------------------------

SAME, SIMILAR = "same_job", "similar"


@dataclass
class Match:
    tier: str
    shared: list[str]          # core roles both cards have, in vocabulary order
    lacks: list[str]           # core roles of the target the candidate does not have
    extra: list[str]           # core roles of the candidate the target does not have
    different: list[dict]      # why a similar card is only similar: [{kind, target, candidate}]


def primary(roles: dict[str, Hit]) -> str | None:
    return next(iter(core(roles)), None)  # roles are in vocabulary order


def match(target: dict[str, Hit], candidate: dict[str, Hit]) -> Match | None:
    """Whether ``candidate`` does the same job as ``target`` (tier ``same_job``), a similar one (``similar``), or neither (``None``).
    Both are ``roles_of`` results. Only core roles count on either side."""
    t, c = core(target), core(candidate)
    head = primary(target)
    if head is None:
        return None
    shared = [s for s in t if s in c]
    lacks = [s for s in t if s not in c]
    extra = [s for s in c if s not in t]
    different: list[dict] = []
    if head in c:
        for slug in t:  # the target's refinement of its job (free) must be the candidate's too
            if REFINES.get(slug) == head and slug not in c:
                different.append({"kind": "lacks_refinement", "target": slug, "candidate": head})
        if head in MODIFIED and t[head].repeatable is not None and c[head].repeatable is not None and t[head].repeatable != c[head].repeatable:
            different.append({"kind": "repeats", "target": head, "candidate": head,
                              "target_repeats": t[head].repeatable, "candidate_repeats": c[head].repeatable})
        return Match(SAME if not different else SIMILAR, shared, lacks, extra, different)
    for slug in lacks:  # a neighbour of the same family: a one-shot draw for a draw engine, a bounce for a board wipe
        for other in extra:
            if ROLES[slug].family == ROLES[other].family:
                different.append({"kind": "neighbour", "target": slug, "candidate": other})
    if shared or different:
        return Match(SIMILAR, shared, lacks, extra, different)
    return None


def _lower(name: str) -> str:
    return name[0].lower() + name[1:]


def role_view(hit: Hit) -> dict:
    return {"role": hit.role, "name": hit.name, "means": ROLES[hit.role].means, "strength": hit.strength, "basis": BASIS,
            "rule": hit.rule, "repeatable": hit.repeatable}


def mana_value_words(target_mv: float | None, mv: float | None, target_name: str) -> str | None:
    if target_mv is None or mv is None:
        return None
    diff = round(mv - target_mv, 2)
    if diff == 0:
        return f"costs the same as {target_name}"
    n = f"{abs(diff):g}"
    return f"costs {n} {'more' if diff > 0 else 'less'} than {target_name}"


def why(found: Match, target: dict[str, Hit], candidate: dict[str, Hit], target_name: str) -> str:
    """The sentence under a candidate, built only from the roles both cards hold (no free text about the cards)."""
    c = core(candidate)
    if found.tier == SAME:
        parts = ", ".join(f"{_lower(ROLES[s].name)} (core, Vault rule {c[s].rule})" for s in found.shared if s in c)
        return f"same job: {parts}"
    bits = []
    for d in found.different:
        if d["kind"] == "neighbour":
            bits.append(f"{_lower(ROLES[d['candidate']].name)}, where {target_name} is {_lower(ROLES[d['target']].name)}")
        elif d["kind"] == "lacks_refinement":
            bits.append(f"{_lower(ROLES[d['candidate']].name)} but not {_lower(ROLES[d['target']].name)}")
        elif d["kind"] == "repeats":
            bits.append(f"{_lower(ROLES[d['candidate']].name)} that {'repeats' if d['candidate_repeats'] else 'happens once'}, where {target_name} "
                        f"{'repeats' if d['target_repeats'] else 'happens once'}")
    if not bits and found.shared:
        bits.append(f"shares {', '.join(_lower(ROLES[s].name) for s in found.shared)} with {target_name}")
    return "similar, with a difference: " + "; ".join(bits)
