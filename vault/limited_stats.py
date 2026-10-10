"""Per-card Limited statistics from 17Lands' public data sets: the arithmetic, the sample-size rules, the exact wording, and
the two streaming counters that reduce a game file and a draft file to per-card counts (#178, docs/limited-data-design.md).

17Lands (https://www.17lands.com/public_datasets) publishes one row per game and one row per pick under CC BY 4.0. The Vault
never keeps those rows: ``GameCounter`` and ``PickCounter`` read them one at a time and hold only per-card counters (plus the state of
one draft at a time). Everything shown to a person is worked out here from the counters, labelled as computed by the Vault, and
always carries the attribution below and the sample size.

Nothing in this module touches the network or the database; ``jobs/sync_limited.py`` reads the files and
``vault/limited_data.py`` stores and reads the counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .provenance import GENERIC_17LANDS_NOTICE

FORMATS = ("PremierDraft", "TradDraft")  # Bo1 and Bo3 draft: the two with a draft and a game file for every recent set
PLATFORM = "MTG Arena"
SOURCE_URL = "https://www.17lands.com/public_datasets"
LICENCE_NAME = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

# -- sample size (design section 7): constants in one place, the same wording everywhere ------------------------------------
SHOW_FLOOR = 200  # under this many games in hand: too few to tell the card from an average one
RANK_FLOOR = 1000  # from this many: ok. Between the two: low
LEVELS = ("too_few", "low", "ok")

CAVEAT = ("17Lands data comes from Magic Arena players who use the 17Lands tracker, not from all players or from paper. "
          "A win rate in hand does not show that the card causes wins.")
FIRST_PICK_CAVEAT = ("17Lands notes that pick 1 data may be missing in some sets, because it is not in the Arena logs. In this file "
                     "many drafters have no pick 1, so the last-seen positions are slightly high for cards that rarely wheel.")
FIRST_PICK_SHARE = 0.01  # tell people about missing first picks when this share of pick-1 rows (or more) shows an empty pack

CHANGES = "Reduced to per-card counts; percentages, intervals and sample-size warnings computed by the Vault."
ORIGIN = "17Lands users' Magic Arena games and drafts (public data sets)"
COMPUTED_WHAT = "per-card win rates, positions, intervals and sample warnings, worked out from the 17Lands counts"

ATTRIBUTION = (
    "Data from 17Lands (https://www.17lands.com/public_datasets), licensed under CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/), which also states that it is provided without warranty (section 5). "
    "Magic Arena games and drafts, set {set}, format {format}; 17Lands files last updated {updated}, read by the Vault on {read}. "
    "Changed by the Vault: the per-game and per-pick rows were reduced to per-card counts, and the percentages, intervals and "
    "sample-size warnings were computed by the Vault, so they can differ from the figures on 17lands.com. "
    "Not produced or endorsed by 17Lands.")
GENERIC_ATTRIBUTION = GENERIC_17LANDS_NOTICE  # the same text without a set, format or date (whoami, the status of the data)

WARN_TOO_FEW = ("Only {n} games in hand for {card} in {set} {format}: too few to say whether it wins more or less than other cards. "
                "The win rate ({rate}%, 95% range {low}% to {high}%) is shown for completeness. It must not be used to rank or "
                "recommend this card.")
WARN_LOW = ("Small sample: {n} games in hand for {card} in {set} {format}. Its true win rate could be anywhere from {low}% to "
            "{high}% (95% range). Do not treat a difference of less than {width} points from another card as real.")
WARN_POSITIONS = ("Too few packs ({n}) to say where {card} is usually last seen or taken; the average is not shown.")
WARN_LEFT_OUT = "{k} cards were left out of this ranking because they have fewer than {floor} {what}."

# -- sanity checks before a file's rows replace the stored ones (design section 4) --------------------------------------------
MIN_RECORDS = 1000
MIN_CARDS = 100
BASELINE_RANGE = (0.40, 0.70)
MAX_CARDS_LOST = 0.20  # a new file that would drop more of the stored cards than this is refused unless --force
MAX_GAMES_LOST = 0.50


class FileError(Exception):
    """The file cannot be used (wrong columns, not contiguous, failed a check). Nothing is written for it."""


def level(n: int) -> str:
    return "too_few" if n < SHOW_FLOOR else "low" if n < RANK_FLOOR else "ok"


def wilson(wins: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """The 95% Wilson score interval of a win rate (a proportion): better than the normal approximation for small samples and
    for rates near 0 or 1."""
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(value: float) -> str:
    return f"{value * 100:.1f}"


def warning_for(card: str, set_code: str, fmt: str, wins_in_hand: int, games_in_hand: int) -> str | None:
    """The exact warning for a card's games in hand, or None at the ``ok`` level."""
    lvl = level(games_in_hand)
    if lvl == "ok":
        return None
    low, high = wilson(wins_in_hand, games_in_hand)
    if lvl == "too_few":
        rate = wins_in_hand / games_in_hand if games_in_hand else 0.0
        return WARN_TOO_FEW.format(n=games_in_hand, card=card, set=set_code, format=fmt, rate=pct(rate), low=pct(low), high=pct(high))
    return WARN_LOW.format(n=games_in_hand, card=card, set=set_code, format=fmt, low=pct(low), high=pct(high),
                           width=f"{(high - low) * 100:.1f}")


def positions_warning(card: str, seen: int, picked: int) -> str | None:
    """The warning when the position averages of a card are withheld (fewer than SHOW_FLOOR packs seen or picks)."""
    short = [n for n in (seen, picked) if n < SHOW_FLOOR]
    return WARN_POSITIONS.format(n=min(short), card=card) if short else None


def attribution(set_code: str, fmt: str, updated: str, read: str) -> str:
    return ATTRIBUTION.format(set=set_code, format=fmt, updated=updated, read=read)


# -- the counters: one row at a time, nothing but per-card sums kept ---------------------------------------------------------
ZERO = frozenset(("", "0"))
TRUE = frozenset(("True", "true", "TRUE", "1"))
FALSE = frozenset(("False", "false", "FALSE", "0"))


def _int(text: str, column: str) -> int:
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            raise FileError(f"column {column!r} holds {text[:30]!r}, which is not a number") from None


def _time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _columns(header: list[str], prefix: str) -> dict[str, int]:
    return {col[len(prefix):]: i for i, col in enumerate(header) if col.startswith(prefix) and len(col) > len(prefix)}


@dataclass
class FileResult:
    """What one file reduced to. ``cards`` maps a card name to its counters (see ``GAME_FIELDS`` and ``PICK_FIELDS``)."""

    kind: str
    cards: dict[str, list[int]]
    records: int  # games or picks used
    skipped: int = 0
    wins: int | None = None
    first_time: datetime | None = None
    last_time: datetime | None = None
    empty_first_picks: int | None = None
    first_picks: int | None = None
    notes: list[str] = field(default_factory=list)


GAME_FIELDS = ("games_played", "wins_played", "opening", "wins_opening", "drawn", "wins_drawn", "in_hand", "wins_in_hand")
PICK_FIELDS = ("seen", "last_seen_sum", "picked", "picked_sum")


class GameCounter:
    """Reads a game file row by row. Per card it keeps eight counts of GAMES, never of copies (17Lands' definitions: a game counts
    once for a card however many copies it has): games with the card in the deck (#GP), in the kept opening hand (#OH), drawn later
    (#GD), and in hand at least once, in the opener or drawn later (#GIH, the union: a game with a copy in each counts once), each
    also over won games. A game with inconsistent data (more copies in hand than in the deck and sideboard together) is left out of every
    count and counted as skipped, as 17Lands does. Tutored copies are not counted as drawn."""

    def __init__(self, header: list[str], expected_set: str):
        self.expected = expected_set
        index = {name: i for i, name in enumerate(header)}
        missing = [c for c in ("won", "expansion") if c not in index]
        deck, opening, drawn, side = (_columns(header, p) for p in ("deck_", "opening_hand_", "drawn_", "sideboard_"))
        if not deck:
            missing.append("deck_<card>")
        if not opening:
            missing.append("opening_hand_<card>")
        if not drawn:
            missing.append("drawn_<card>")
        if missing:
            raise FileError("the game file lacks the column(s) " + ", ".join(missing))
        lacking = sorted(name for name in deck.keys() | opening.keys() | drawn.keys() if name not in deck or name not in opening or name not in drawn)
        if lacking:
            raise FileError("the game file lacks some of deck_, opening_hand_ and drawn_ columns for: " + ", ".join(lacking[:5]))
        self.names = sorted(deck)
        self.columns = [(deck[n], opening[n], drawn[n], side.get(n, -1)) for n in self.names]
        self.i_won, self.i_expansion = index["won"], index["expansion"]
        self.i_time = index.get("game_time", index.get("draft_time", -1))
        self.width = len(header)
        self.counts = {n: [0] * len(GAME_FIELDS) for n in self.names}
        self.records = self.skipped = self.wins = 0
        self.expansions: set[str] = set()
        self.first = self.last = ""

    def feed(self, row: list[str]) -> None:
        if len(row) < self.width:
            self.skipped += 1
            return
        self.expansions.add(row[self.i_expansion])
        won_text = row[self.i_won]
        if won_text in TRUE:
            won = 1
        elif won_text in FALSE:
            won = 0
        else:
            self.skipped += 1
            return
        touched = []
        for name, (i_deck, i_open, i_drawn, i_side) in zip(self.names, self.columns):
            deck, opening, drawn = row[i_deck], row[i_open], row[i_drawn]
            if deck in ZERO and opening in ZERO and drawn in ZERO:
                continue
            d, o, r = _int(deck or "0", "deck_" + name), _int(opening or "0", "opening_hand_" + name), _int(drawn or "0", "drawn_" + name)
            side = row[i_side] if i_side >= 0 else ""
            s = 0 if side in ZERO else _int(side, "sideboard_" + name)
            if o + r > d + s:  # a card in hand that was in neither the deck nor the sideboard: the game's data is inconsistent
                self.skipped += 1
                return
            touched.append((name, d, o, r))
        self.records += 1
        self.wins += won
        for name, d, o, r in touched:
            c = self.counts[name]
            for i, present in ((0, d > 0), (2, o > 0), (4, r > 0), (6, o > 0 or r > 0)):
                if present:
                    c[i] += 1
                    c[i + 1] += won
        if self.i_time >= 0:
            when = row[self.i_time]
            if when:
                if not self.first or when < self.first:
                    self.first = when
                if when > self.last:
                    self.last = when

    def result(self) -> FileResult:
        if self.expansions != {self.expected}:
            raise FileError(f"the file is for expansion(s) {sorted(self.expansions)[:5]}, not {self.expected}")
        cards = {n: c for n, c in self.counts.items() if c[0] or c[6]}
        return FileResult("game", cards, self.records, self.skipped, self.wins, _time(self.first), _time(self.last))


class PickCounter:
    """Reads a draft file row by row (one row is one pick, rows of one drafter contiguous). Per card it keeps: the number of packs
    (per drafter and pack round) in which the card was seen, the sum of the pick numbers at which it was last seen there (a card
    seen at pick 1 and again at pick 9 counts once, with 9: 17Lands' last-seen position), and how often and where it was taken.
    Pick numbers are stored 1-based, whatever the file's base."""

    def __init__(self, header: list[str], expected_set: str):
        self.expected = expected_set
        index = {name: i for i, name in enumerate(header)}
        missing = [c for c in ("expansion", "draft_id", "pack_number", "pick_number", "pick") if c not in index]
        packs = _columns(header, "pack_card_")
        if not packs:
            missing.append("pack_card_<card>")
        if missing:
            raise FileError("the draft file lacks the column(s) " + ", ".join(missing))
        self.names = sorted(packs)
        self.indexes = [packs[n] for n in self.names]
        self.i_expansion, self.i_draft, self.i_pack = index["expansion"], index["draft_id"], index["pack_number"]
        self.i_pick_number, self.i_pick = index["pick_number"], index["pick"]
        self.i_time = index.get("draft_time", -1)
        self.width = len(header)
        self.seen: dict[str, int] = {}
        self.last_sum: dict[str, int] = {}
        self.picked: dict[str, int] = {}
        self.picked_sum: dict[str, int] = {}
        self.records = 0
        self.expansions: set[str] = set()
        self.draft: str | None = None
        self.pack = -1
        self.finished: set[int] = set()  # hashes of the drafts already read: a draft that comes back means rows are not contiguous
        self.last: dict[int, int] = {}  # packs-column position -> last pick number seen in the current pack round
        self.low = None  # the lowest pick number in the file (0 or 1)
        self.by_number: dict[int, list[int]] = {}  # for pick numbers 0 and 1: [rows, rows with an empty pack]
        self.first = self.last_time = ""

    def _flush(self) -> None:
        for position, pick_number in self.last.items():
            name = self.names[position]
            self.seen[name] = self.seen.get(name, 0) + 1
            self.last_sum[name] = self.last_sum.get(name, 0) + pick_number
        self.last = {}

    def feed(self, row: list[str]) -> None:
        if len(row) < self.width:
            return
        self.expansions.add(row[self.i_expansion])
        draft = row[self.i_draft]
        pack = _int(row[self.i_pack], "pack_number")
        number = _int(row[self.i_pick_number], "pick_number")
        if draft != self.draft or pack != self.pack:
            self._flush()
            if draft != self.draft:
                if self.draft is not None:
                    self.finished.add(hash(self.draft))
                if hash(draft) in self.finished:
                    raise FileError("the rows of one draft are not contiguous in this file (draft ids come back after other drafts); "
                                    "the last-seen positions cannot be worked out one draft at a time")
            elif pack < self.pack:
                raise FileError("the pack rounds of one draft are not in order in this file")
            self.draft, self.pack = draft, pack
        if self.low is None or number < self.low:
            self.low = number
        any_card = False
        for position, i in enumerate(self.indexes):
            if row[i] not in ZERO:
                self.last[position] = number
                any_card = True
        if number <= 1:  # the first pick of a pack round is 0 or 1; which one is known at the end
            bucket = self.by_number.setdefault(number, [0, 0])
            bucket[0] += 1
            bucket[1] += 0 if any_card else 1
        pick = row[self.i_pick]
        if pick:
            self.picked[pick] = self.picked.get(pick, 0) + 1
            self.picked_sum[pick] = self.picked_sum.get(pick, 0) + number
        self.records += 1
        if self.i_time >= 0:
            when = row[self.i_time]
            if when:
                if not self.first or when < self.first:
                    self.first = when
                if when > self.last_time:
                    self.last_time = when

    def result(self) -> FileResult:
        self._flush()
        if self.expansions != {self.expected}:
            raise FileError(f"the file is for expansion(s) {sorted(self.expansions)[:5]}, not {self.expected}")
        if self.low is None:
            raise FileError("the draft file has no rows")
        if self.low not in (0, 1):
            raise FileError(f"pick numbers start at {self.low} in this file; expected 0 or 1")
        offset = 1 - self.low  # stored 1-based
        cards: dict[str, list[int]] = {}
        for name in set(self.seen) | set(self.picked):
            seen, picked = self.seen.get(name, 0), self.picked.get(name, 0)
            cards[name] = [seen, self.last_sum.get(name, 0) + seen * offset, picked, self.picked_sum.get(name, 0) + picked * offset]
        first = self.by_number.get(self.low, [0, 0])
        return FileResult("draft", cards, self.records, 0, None, _time(self.first), _time(self.last_time),
                          empty_first_picks=first[1], first_picks=first[0],
                          notes=[f"pick_number starts at {self.low}", f"first pick-number rows: {first[0]}, with an empty pack: {first[1]}"])


def sanity_problems(result: FileResult, previous: dict | None = None) -> tuple[list[str], list[str]]:
    """What is wrong with a reduced file: ``(always, unless_forced)``. The first group is never overridden (too few games, too few
    cards, a baseline win rate that is not plausible); the second compares with the rows already stored (design section 4)."""
    always: list[str] = []
    unit = "games" if result.kind == "game" else "picks"
    if result.records < MIN_RECORDS:
        always.append(f"only {result.records} {unit} used; at least {MIN_RECORDS} are needed")
    if len(result.cards) < MIN_CARDS:
        always.append(f"only {len(result.cards)} cards found; at least {MIN_CARDS} are needed")
    if result.kind == "game" and result.records:
        baseline = (result.wins or 0) / result.records
        if not BASELINE_RANGE[0] <= baseline <= BASELINE_RANGE[1]:
            always.append(f"the overall win rate is {baseline:.1%}, outside {BASELINE_RANGE[0]:.0%} to {BASELINE_RANGE[1]:.0%}")
    unless: list[str] = []
    if previous:
        if previous["cards"] and len(result.cards) < previous["cards"] * (1 - MAX_CARDS_LOST):
            unless.append(f"it would drop the stored cards from {previous['cards']} to {len(result.cards)} (more than {MAX_CARDS_LOST:.0%})")
        if previous["records"] and result.records < previous["records"] * (1 - MAX_GAMES_LOST):
            unless.append(f"it would drop the stored {unit} from {previous['records']} to {result.records} (more than {MAX_GAMES_LOST:.0%})")
    return always, unless
