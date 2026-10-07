"""How a deck's mana curve plays: sample opening turns and the odds behind them (issue #137).

A small goldfish simulator: no opponent, a simple player. Each game: London mulligan with a stated keep rule, a draw
each turn (none on turn 1 for the player on the play in two-player games, CR 103.8a; in multiplayer everyone draws,
CR 103.8c), one land a turn, then cast what the available mana allows (ramp first in the early turns, then the
biggest spells), and discard to seven at the end of the turn unless something gives no maximum hand size.

What it does not model is listed in ``ASSUMPTIONS`` and returned with every answer. Pure functions: the card facts
come in as ``SimCard`` (built from the catalog by ``from_oracle``), so tests need no database.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from .card_faces import front_text

HAND_SIZE = 7
MAX_TURNS, MAX_GAMES, MAX_SAMPLES = 10, 5000, 10
ASSUMPTIONS = [
    "Colours are not checked: any land pays for any spell (a deck with colour problems will do worse than shown).",
    "Only the draw step draws cards; card draw, tutors and cantrips are not played out.",
    "Mana from rocks and creatures is read from the card text (a creature's mana starts the turn after it is cast); "
    "spells that put lands onto the battlefield count as one more land.",
    "X spells count their mana value with X = 0.",
    "No opponent: no interaction, no combat, nothing is destroyed.",
    "Keep rule: keep 7 cards with 2 to 5 lands; otherwise mulligan (London) up to twice, putting back the most "
    "expensive spells or extra lands.",
]
_ADD = re.compile(r"\{T\}[^.:]*:\s*Add ([^.]*)", re.IGNORECASE)
DISCARD_PLAN = (("no maximum hand size", "no maximum hand size"), ("madness", "madness"),
                ("whenever you discard", "pays off discarding"), ("discards their hand", "wheels hands"),
                ("cards in your hand", "cares how many cards are in hand"))


@dataclass(frozen=True)
class SimCard:
    name: str
    cmc: int = 0
    land: bool = False
    tapped: bool = False          # a land that enters tapped
    rock: int = 0                 # mana an artifact adds from the turn it is cast
    dork: int = 0                 # mana a creature adds from the next turn
    ramp_lands: int = 0           # lands a spell puts onto the battlefield
    no_max_hand: bool = False
    plan: tuple[str, ...] = ()    # why discarding or a big hand may be the deck's plan


def _mana_added(text: str) -> int:
    m = _ADD.search(text or "")
    if not m:
        return 0
    clause = m.group(1)
    symbols = len(re.findall(r"\{[^}]+\}", clause.split(" or ")[0]))
    if symbols:
        return symbols
    words = {"one": 1, "two": 2, "three": 3}
    w = re.search(r"\b(one|two|three) mana\b", clause)
    return words[w.group(1)] if w else 1


def from_oracle(card) -> SimCard:
    """The simulation's view of a catalog card (``vault.models.OracleCard``)."""
    front_type = (card.type_line or "").split("//")[0]
    text = front_text(card)  # a transform or modal card keeps its text on its faces (vault.card_faces)
    lower = text.lower()
    land = "Land" in front_type
    rock = dork = ramp_lands = 0
    if not land:
        added = _mana_added(text)
        if added and "Artifact" in front_type and "Creature" not in front_type:
            rock = added
        elif added and "Creature" in front_type:
            dork = added
        if ("Sorcery" in front_type or "Instant" in front_type) and "search your library" in lower and \
                "land" in lower and "onto the battlefield" in lower:
            ramp_lands = 2 if re.search(r"up to two [^.]*lands? cards?[^.]*put them onto the battlefield", lower) else 1
    tapped = land and ("enters tapped" in lower or "enters the battlefield tapped" in lower) and "unless" not in lower
    plan = tuple(dict.fromkeys(why for needle, why in DISCARD_PLAN if needle in lower))
    return SimCard(card.name, int(card.cmc or 0), land, tapped, rock, dork, ramp_lands,
                   "no maximum hand size" in lower, plan)


@dataclass
class _Game:
    library: list[SimCard]
    hand: list[SimCard] = field(default_factory=list)
    lands: int = 0
    tapped_lands: int = 0         # entered tapped this turn: usable from next turn
    rocks: int = 0
    dorks_ready: int = 0
    dorks_new: int = 0
    no_max_hand: bool = False
    discarded: int = 0
    commander: SimCard | None = None


def _keepable(hand: list[SimCard]) -> bool:
    return 2 <= sum(c.land for c in hand) <= 5


def _bottom(hand: list[SimCard], n: int) -> list[SimCard]:
    """Put ``n`` cards back: extra lands if the hand is land-heavy, else the most expensive spells."""
    hand = list(hand)
    for _ in range(n):
        lands = [c for c in hand if c.land]
        spells = sorted((c for c in hand if not c.land), key=lambda c: -c.cmc)
        pick = lands[0] if len(lands) > 3 or not spells else spells[0]
        hand.remove(pick)
    return hand


def _opening(rng: random.Random, deck: list[SimCard]) -> tuple[list[SimCard], list[SimCard], int]:
    for mulligans in range(3):
        library = list(deck)
        rng.shuffle(library)
        hand, library = library[:HAND_SIZE], library[HAND_SIZE:]
        if _keepable(hand) or mulligans == 2:
            kept, back = _bottom(hand, mulligans), list(hand)
            for card in kept:
                back.remove(card)
            return kept, library + back, mulligans  # the cards put back go to the bottom
    raise AssertionError("unreachable")


def _play_turn(g: _Game, turn: int, draws: bool) -> dict:
    drew = None
    if draws and g.library:
        drew = g.library.pop(0)
        g.hand.append(drew)
    g.lands += g.tapped_lands
    g.tapped_lands = 0
    g.dorks_ready += g.dorks_new
    g.dorks_new = 0
    land = next((c for c in g.hand if c.land and not c.tapped), None) or next((c for c in g.hand if c.land), None)
    if land:
        g.hand.remove(land)
        if land.tapped:
            g.tapped_lands += 1
        else:
            g.lands += 1
        g.no_max_hand = g.no_max_hand or land.no_max_hand  # Reliquary Tower is a land: playing it is what lifts the limit (#137)
    mana = g.lands + g.rocks + g.dorks_ready
    available, cast = mana, []
    while True:
        options = [c for c in g.hand if not c.land and c.cmc <= mana]
        if g.commander and g.commander.cmc <= mana:
            options.append(g.commander)
        if not options:
            break
        ramp = [c for c in options if c.rock or c.dork or c.ramp_lands]
        pick = max(ramp, key=lambda c: c.cmc) if ramp and turn <= 4 else max(options, key=lambda c: c.cmc)
        mana -= pick.cmc
        if pick is g.commander:
            g.commander = None
        else:
            g.hand.remove(pick)
        cast.append(pick.name)
        g.rocks += pick.rock
        mana += pick.rock          # a rock can be tapped the turn it comes down
        g.dorks_new += pick.dork
        g.tapped_lands += pick.ramp_lands
        g.no_max_hand = g.no_max_hand or pick.no_max_hand
    spells_held = sum(not c.land for c in g.hand)
    discarded = []
    if not g.no_max_hand:
        while len(g.hand) > HAND_SIZE:
            spare = [c for c in g.hand if c.land] if sum(c.land for c in g.hand) > 2 else []
            worst = spare[0] if spare else max((c for c in g.hand if not c.land), key=lambda c: c.cmc)
            g.hand.remove(worst)
            discarded.append(worst.name)
    g.discarded += len(discarded)
    return {"turn": turn, "drew": drew.name if drew else None, "land": land.name if land else None,
            "mana": available, "spent": available - mana, "cast": cast, "hand": len(g.hand),
            "discarded": discarded,
            # every spell held cost more than the mana: a gap in the curve (normal on turn 1 with no one-drops)
            "nothing_affordable": available > 0 and not cast and spells_held > 0}


def _game(rng: random.Random, deck: list[SimCard], commander: SimCard | None, turns: int, skip_first_draw: bool):
    hand, library, mulligans = _opening(rng, deck)
    g = _Game(library=library, hand=list(hand), commander=commander)
    opening = [c.name for c in hand]
    log = [_play_turn(g, t, draws=not (t == 1 and skip_first_draw)) for t in range(1, turns + 1)]
    return {"mulligans": mulligans, "opening_hand": opening, "turns": log}


def simulate(deck: list[SimCard], *, commander: SimCard | None = None, multiplayer: bool = False,
             on_the_play: bool = True, turns: int = 6, games: int = 1000, samples: int = 5, seed: int = 0) -> dict:
    """Odds per turn over ``games`` games, and ``samples`` of them turn by turn. Same seed, same answer."""
    turns, games, samples = max(1, min(turns, MAX_TURNS)), max(1, min(games, MAX_GAMES)), max(0, min(samples, MAX_SAMPLES))
    if len(deck) < HAND_SIZE + turns:
        raise ValueError(f"The deck has {len(deck)} cards: too few to play {turns} turns.")
    rng = random.Random(seed)
    skip = on_the_play and not multiplayer
    results = [_game(rng, deck, commander, turns, skip) for _ in range(games)]
    per_turn = []
    for t in range(turns):
        rows = [r["turns"][t] for r in results]
        lands_by = [sum(1 for x in r["turns"][: t + 1] if x["land"]) for r in results]
        per_turn.append({
            "turn": t + 1,
            "land_drop": _pct(sum(1 for x in rows if x["land"]), games),
            "all_land_drops_so_far": _pct(sum(1 for n in lands_by if n == t + 1), games),
            "mana_available": round(sum(x["mana"] for x in rows) / games, 2),
            "mana_spent": round(sum(x["spent"] for x in rows) / games, 2),
            "cards_in_hand": round(sum(x["hand"] for x in rows) / games, 2),
            "discarded_by_now": _pct(sum(1 for r in results if any(x["discarded"] for x in r["turns"][: t + 1])), games),
            "every_spell_in_hand_cost_too_much": _pct(sum(1 for x in rows if x["nothing_affordable"]), games),
        })
    headline = {"mulligan_rate": _pct(sum(1 for r in results if r["mulligans"]), games)}
    if turns >= 5:
        headline["five_mana_by_turn_5"] = _pct(sum(1 for r in results if r["turns"][4]["mana"] >= 5), games)
        headline["discarded_by_turn_5"] = per_turn[4]["discarded_by_now"]
        headline["missed_a_land_drop_by_turn_4"] = _pct(sum(1 for r in results if not all(x["land"] for x in r["turns"][:4])), games)
    plan = sorted({f"{c.name}: {why}" for c in deck + ([commander] if commander else []) for why in c.plan})
    return {"games": games, "turns": turns, "on_the_play": on_the_play, "multiplayer": multiplayer, "seed": seed,
            "headline": headline, "per_turn": per_turn, "samples": results[:samples],
            "discard_may_be_the_plan": plan,
            "assumptions": ASSUMPTIONS}


def _pct(n: int, total: int) -> float:
    return round(100 * n / total, 1)
