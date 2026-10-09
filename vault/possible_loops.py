"""A reading of a deck's card text for possible loops that Commander Spellbook does not list (#172, docs/possible-loops-design.md).

This is the Vault's own reading, never Commander Spellbook's, and it is deliberately small: one pattern, **token for mana** (P1),
checked by arithmetic on facts read from Oracle text with anchored patterns. Anything a pattern does not account for is left out
(a clause it cannot price is a blocker, never a guess), so the reading under-reports rather than claim a loop that is not there:
a false positive costs trust and is worse than a false negative.

Pattern P1: a repeatable ability that pays mana ``C`` for one creature token; something in the deck that lets that token tap for
``m`` mana (a lord that grants "{T}: Add ..." to the token's type); something that gives the token haste (a token cannot use a {T}
ability the turn it arrives without it). Net per pass is ``m - C``:

- ``closes``: net is zero or better by the text (every piece matched, no blocker), alone or with cards that fit a closer shape
  (a cost reduction with a floor of one mana, a doubling of mana) which are named;
- ``one_short``: net is -1 and nothing in the deck closes it: an **engine**, not a loop. It is reported so nobody rounds it up;
- anything lower, or without haste, is not reported.

The words of the note are fixed by the design (section 5) and enforced by tests/test_possible_loops.py: it says "possible loop",
"engine", "one mana short", never the words the design forbids, and names what it is not (Commander Spellbook's).

Pure functions over ``CardText``; nothing here touches the database or the network, so it cannot fail when Spellbook does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product
from typing import Iterable

from .card_faces import front_text

PATTERN = "token for mana"
SOURCE = "the Vault's reading of card text"
NOT_FROM = "Commander Spellbook"
NOTE = ("A reading of the text, not a proof, and not Commander Spellbook's. Finding none says only that these patterns found none.")
VERIFY = "Read each card with get_card_oracle; ask a judge before relying on it."
ASSUMES = [
    "every card is on the battlefield and can be used once there (the cost of casting them is not counted)",
    "no opponent acts",
    "the colour of mana is checked only against the cost named here",
    "state-based actions, the legend rule, replacement effects other than the ones named, and rulings are not modelled",
    "having these cards in a 99-card deck is not having them together: how often they meet is not measured",
]
MAX_LOOPS = 5
MAX_SKIPPED = 5


@dataclass(frozen=True)
class CardText:
    name: str
    type_line: str
    text: str


def card_texts(entries: Iterable) -> list[CardText]:
    """The Oracle text of the cards of a resolved deck (front face), once per card; cards the catalog does not know are left out."""
    seen, out = set(), []
    for e in entries:
        card = getattr(e, "card", None)
        if card is None or card.name in seen:
            continue
        seen.add(card.name)
        out.append(CardText(card.name, card.type_line or "", front_text(card)))
    return out


# -- reading a card's text into typed facts --------------------------------------------------------------------------------------

_NUMBER = {"one": 1, "two": 2, "three": 3}
_TYPE = r"[A-Z][a-z]+"
_SCOPE = (rf"(?:All (?P<a>{_TYPE})s|All (?P<b>{_TYPE}) creatures|(?P<c>{_TYPE}) creatures you control|"
          r"(?P<d>Creatures you control)|(?P<e>All creatures))")
_TAP_GRANT = re.compile(rf'^{_SCOPE} have "(?P<ability>\{{T\}}: Add [^"]+)"$')
_HASTE_GRANT = re.compile(rf"^{_SCOPE} have haste\.?$")
_TAP_ABILITY = re.compile(r"^\{T\}: Add (?:(?P<symbols>(?:\{[WUBRGC]\})+)|(?P<word>one|two|three) mana of any (?:one )?colou?r)\.?$")
_TOKEN_LOOSE = re.compile(r"^create\b.*\btokens?\b", re.IGNORECASE)
_TOKEN_STRICT = re.compile(
    rf"^Create (?:a|an|one) (?P<p>\d+)/(?P<t>\d+) (?:(?:colorless|white|blue|black|red|green)(?: and (?:white|blue|black|red|green))* )?"
    rf"(?P<types>{_TYPE}(?: {_TYPE})*) creature token\.$")
_REDUCER = re.compile(r"^Activated abilities of creatures (?:you control )?cost \{(?P<n>\d)\} less to activate\. "
                      r"This effect can't reduce the mana in that cost to less than one mana\.$")
_MULTIPLIER = re.compile(r"^If you tap a permanent for mana, it produces (?P<how>twice|three times) as much of that mana instead\.$")


@dataclass(frozen=True)
class Cost:
    generic: int
    coloured: tuple[str, ...]  # one letter per coloured (or colourless {C}) symbol

    @property
    def total(self) -> int:
        return self.generic + len(self.coloured)

    def text(self) -> str:
        return (("{%d}" % self.generic) if self.generic or not self.coloured else "") + "".join("{%s}" % c for c in self.coloured)


@dataclass(frozen=True)
class TokenMaker:
    card: str
    is_creature: bool
    cost: Cost
    types: frozenset[str]
    size: str


@dataclass(frozen=True)
class TapGrant:
    card: str
    scope: frozenset[str] | None  # None: every creature
    mana: int
    colours: frozenset[str]
    any_colour: bool


@dataclass(frozen=True)
class HasteGrant:
    card: str
    scope: frozenset[str] | None


@dataclass(frozen=True)
class Reducer:
    card: str
    by: int


@dataclass(frozen=True)
class Multiplier:
    card: str
    factor: int


def _clean(text: str) -> list[str]:
    """The ability lines of a card: curly quotes straightened, reminder text removed, blank lines dropped."""
    text = (text or "").replace("“", '"').replace("”", '"').replace("’", "'")
    text = re.sub(r"\([^)]*\)", "", text)
    return [" ".join(line.split()) for line in text.split("\n") if line.strip()]


def _scope(m: re.Match) -> frozenset[str] | None:
    for key in ("a", "b", "c"):
        if m.group(key):
            return frozenset({m.group(key)})
    return None  # "Creatures you control" or "All creatures"


def _cost(prefix: str) -> Cost | None:
    """A cost made only of generic and coloured mana symbols; {T}, {X}, hybrid, Phyrexian, sacrifice, discard, life: not priced here."""
    generic, coloured = 0, []
    for part in prefix.split(", "):
        if not re.fullmatch(r"(?:\{[^}]+\})+", part.strip()):
            return None
        for symbol in re.findall(r"\{([^}]+)\}", part):
            if symbol.isdigit():
                generic += int(symbol)
            elif symbol in ("W", "U", "B", "R", "G", "C"):
                coloured.append(symbol)
            else:
                return None
    return Cost(generic, tuple(coloured)) if generic or coloured else None


@dataclass
class Facts:
    makers: list[TokenMaker]
    taps: list[TapGrant]
    hastes: list[HasteGrant]
    reducers: list[Reducer]
    multipliers: list[Multiplier]
    skipped: list[tuple[str, str]]  # (card, why) for a token-making line the patterns could not price


def read_facts(cards: Iterable[CardText]) -> Facts:
    facts = Facts([], [], [], [], [], [])
    for card in cards:
        for line in _clean(card.text):
            _read_line(card, line, facts)
    return facts


def _read_line(card: CardText, line: str, facts: Facts) -> None:
    head, sep, body = line.partition(": ")
    if sep and '"' not in head and _TOKEN_LOOSE.match(body):
        cost, strict = _cost(head), _TOKEN_STRICT.match(body)
        if cost is None:
            facts.skipped.append((card.name, "its cost is not only mana, or has an X"))
        elif strict is None:
            facts.skipped.append((card.name, "its token ability says more than the reading can price (a condition, a count, a rider)"))
        else:
            facts.makers.append(TokenMaker(card.name, "Creature" in card.type_line, cost,
                                           frozenset(strict.group("types").split()), f"{strict.group('p')}/{strict.group('t')}"))
        return
    if m := _TAP_GRANT.match(line):
        ability = _TAP_ABILITY.match(m.group("ability"))
        if ability:
            if ability.group("symbols"):
                letters = re.findall(r"\{([WUBRGC])\}", ability.group("symbols"))
                facts.taps.append(TapGrant(card.name, _scope(m), len(letters), frozenset(letters), False))
            else:
                facts.taps.append(TapGrant(card.name, _scope(m), _NUMBER[ability.group("word")], frozenset("WUBRG"), True))
        return
    if m := _HASTE_GRANT.match(line):
        facts.hastes.append(HasteGrant(card.name, _scope(m)))
    elif m := _REDUCER.match(line):
        facts.reducers.append(Reducer(card.name, int(m.group("n"))))
    elif m := _MULTIPLIER.match(line):
        facts.multipliers.append(Multiplier(card.name, 2 if m.group("how") == "twice" else 3))


# -- the arithmetic ----------------------------------------------------------------------------------------------------------------

def _covers(scope: frozenset[str] | None, types: frozenset[str]) -> bool:
    return scope is None or bool(scope & types)


def _paid(maker: TokenMaker, reducer: Reducer | None) -> Cost:
    """The cost of the ability after a reduction that cannot take the mana in the cost below one."""
    cost = maker.cost
    if reducer is None or not maker.is_creature or cost.total < 1:
        return cost
    generic = max(cost.generic - reducer.by, 0)
    if generic + len(cost.coloured) < 1:
        generic = 1 - len(cost.coloured)
    return Cost(generic, cost.coloured)


def _signed(n: int) -> str:
    return f"{n:+d} mana" if n else "0 mana"


def _options(facts: Facts) -> list[tuple[Reducer | None, Multiplier | None]]:
    """What can close a gap: at most one cost reduction and one doubling at a time (they are never added to each other)."""
    reducers = [None, *sorted(facts.reducers, key=lambda r: r.card)]
    multipliers = [None, *sorted(facts.multipliers, key=lambda m: m.card)]
    return [o for o in product(reducers, multipliers) if o != (None, None)]


def _closers(maker: TokenMaker, tap: TapGrant, facts: Facts) -> list[dict]:
    """The smallest sets of cards in the deck that bring the net to zero or better, each alone sufficient (alternatives)."""
    found = []
    for reducer, multiplier in _options(facts):
        paid = _paid(maker, reducer)
        mana = tap.mana * (multiplier.factor if multiplier else 1)
        if mana - paid.total >= 0:
            how = []
            if reducer:
                how.append(f"{reducer.card} lowers {maker.cost.text()} to {paid.text()}")
            if multiplier:
                how.append(f"{multiplier.card} makes the token's tap produce {mana} mana instead of {tap.mana}")
            found.append({"cards": [x.card for x in (reducer, multiplier) if x], "how": "; ".join(how),
                          "net": f"{_signed(mana - paid.total)} per pass"})
    if not found:
        return []
    smallest = min(len(f["cards"]) for f in found)
    return sorted((f for f in found if len(f["cards"]) == smallest), key=lambda f: f["cards"])


def _loop_for(maker: TokenMaker, facts: Facts) -> dict | None:
    needed = set(maker.cost.coloured)
    taps = [g for g in facts.taps if _covers(g.scope, maker.types)]
    taps = [g for g in taps if not needed or (g.any_colour and "C" not in needed) or needed <= g.colours]
    hastes = sorted((h for h in facts.hastes if _covers(h.scope, maker.types)), key=lambda h: h.card)
    if not taps or not hastes:
        return None  # nobody makes the token tap for the mana the ability needs, or without haste it cannot tap this turn
    tap = sorted(taps, key=lambda g: (-g.mana, g.card))[0]  # a creature taps once: the largest grant, never the sum
    haste = hastes[0]
    kind = " ".join(sorted(maker.types))
    base = tap.mana - maker.cost.total
    closers = _closers(maker, tap, facts) if base < 0 else []
    if base >= 0 or closers:
        confidence = "closes"
    elif base == -1:
        confidence = "one_short"
    else:
        return None
    what = f"{maker.card} turns {maker.cost.text()} into a {maker.size} {kind} token, and that token can tap for mana at once"
    if confidence == "one_short":
        reading = f"These cards make an engine that is one mana short of a loop: {what}."
    elif closers:
        also = " or ".join(" and ".join(c["cards"]) for c in closers)
        reading = f"The card text suggests a possible loop once {also} is also on the battlefield: {what}."
    else:
        reading = f"The card text suggests a possible loop: {what}."
    steps = [f"Pay {maker.cost.text()} ({maker.card}): a {maker.size} {kind} token.",
             f"It has haste ({haste.card}) and taps for {tap.mana} mana ({tap.card}).",
             f"Net: {_signed(base)} per token."]
    steps += [f"{c['how']}. Net: {c['net']}." for c in closers]
    cards = [maker.card, tap.card, haste.card] + [n for c in closers for n in c["cards"]]
    return {
        "confidence": confidence, "pattern": PATTERN, "cards": list(dict.fromkeys(cards)), "reading": reading, "steps": steps,
        "net": f"{_signed(base)} per pass" + (" from these cards alone" if closers else ""),
        "needs": (f"something that makes a tapped {kind} produce one more mana, or lowers {maker.cost.text()} by one"
                  if confidence == "one_short" else None),
        "closed_by": closers, "assumes": list(ASSUMES), "verify": VERIFY,
    }


def _summary(loops: list[dict]) -> str:
    if not loops:
        return f"The Vault's reading found none with the pattern it checks ({PATTERN}). That says nothing about other shapes."
    closes = sum(l["confidence"] == "closes" for l in loops)
    return (f"The Vault's reading of the card text found {closes} possible loop(s) and {len(loops) - closes} engine(s) one mana short "
            f"of a loop (pattern: {PATTERN}).")


def read(cards: Iterable[CardText], listed_by_spellbook: Iterable[Iterable[str]] = ()) -> dict:
    """The ``possible_loops`` block for a deck's cards. ``listed_by_spellbook`` are the card names of each combo Commander
    Spellbook lists as in the deck: a reading whose cards all sit in one of them is left to Spellbook (design section 5, rule 7)."""
    facts = read_facts(list(cards))
    listed = [{n.lower() for n in names} for names in listed_by_spellbook]
    loops, duplicates = [], 0
    for maker in sorted(facts.makers, key=lambda m: m.card):
        loop = _loop_for(maker, facts)
        if loop is None:
            continue
        names = {n.lower() for n in loop["cards"]}
        if any(names <= combo for combo in listed):
            duplicates += 1
            continue
        loops.append(loop)
    loops.sort(key=lambda l: (l["confidence"] != "closes", l["cards"][0]))
    shown = loops[:MAX_LOOPS]
    out = {
        "source": SOURCE, "not_from": NOT_FROM, "covers": [PATTERN],
        "summary": _summary(loops),
        "loops": shown, "total": len(loops),
        "note": NOTE,
    }
    if duplicates:
        out["also_listed_by_spellbook"] = duplicates
    if facts.skipped:
        out["not_read"] = [{"card": c, "why": why} for c, why in sorted(set(facts.skipped))[:MAX_SKIPPED]]
    return out
