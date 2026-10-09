"""Reading a decklist's text: ``mtg_toolkits.decklist.parse_text`` plus Moxfield's ``*CMDR*`` marker (#332).

Moxfield marks the commander on its own line (``1 Atraxa, Praetors' Voice (CMM) 1068 *CMDR*``) instead of giving it a section.
The parser takes the marker as part of the name, so the card was never found (no price, no owner count) and the set code and
number stayed in the name. Here a marked line is moved into a Commander section, without its marker, before the parser reads it.

It is also the one place that bounds what the library's regular expressions see (#349): its header pattern backtracks cubically
on a long run of spaces (1,600 spaces took 14.7 seconds; 40,000, which fits the 50,000-character limit, never returns, and the
process holds the GIL meanwhile). Runs of whitespace are collapsed and a line is cut at :data:`MAX_LINE` characters before
anything is matched. Only the copy being read is changed: a saved deck keeps its text.
"""

from __future__ import annotations

import re

from mtg_toolkits import decklist

CMDR = re.compile(r"\s*\*CMDR\*\s*$", re.IGNORECASE)
SPACES = re.compile(r"\s{2,}")
MAX_LINE = 300  # a card line with its set, number and categories is well under this


def bounded(text: str) -> list[str]:
    """The lines of ``text`` as the library's patterns may safely see them: whitespace runs collapsed, each line cut."""
    return [SPACES.sub(" ", line)[:MAX_LINE] for line in text.splitlines()]


def parse_text(text: str) -> decklist.Decklist:
    """``decklist.parse_text`` with every ``*CMDR*`` line read as a Commander-section line, on bounded input."""
    lines = bounded(text)
    marked = [CMDR.sub("", line) for line in lines if CMDR.search(line)]
    if not marked:
        return decklist.parse_text("\n".join(lines))
    rest = [line for line in lines if not CMDR.search(line)]
    return decklist.parse_text("\n".join(["Commander", *marked, "", "Deck", *rest]))


SECTION_HEADERS = {"commander": "Commander", "companion": "Companion", "main": "Deck", "sideboard": "Sideboard", "maybeboard": "Maybeboard"}


def render_line(line: decklist.DeckLine) -> str:
    """One card as a decklist line the parser reads back to the same line: ``1 Sol Ring (C21) 263 *F* [Ramp, Mana rock]``."""
    out = f"{line.quantity} {line.name}"
    if line.set_code:
        out += f" ({line.set_code.upper()})" + (f" {line.collector_number}" if line.collector_number else "")
    out += {"foil": " *F*", "etched": " *E*"}.get(getattr(line.finish, "value", line.finish), "")
    if line.categories:
        out += " [" + ", ".join(line.categories) + "]"
    return out


def render(lines: list[decklist.DeckLine]) -> str:
    """The cards as a decklist text, a section at a time (Commander, Companion, Deck, Sideboard, Maybeboard); a list that is
    all main-deck cards has no header. ``parse_text(render(lines))`` gives the same cards in the same sections (#163)."""
    groups: dict[str, list[str]] = {}
    for line in lines:
        groups.setdefault(line.section or decklist.MAIN, []).append(render_line(line))
    if set(groups) <= {decklist.MAIN}:
        return "\n".join(groups.get(decklist.MAIN, []))
    order = [s for s in SECTION_HEADERS if s in groups] + [s for s in groups if s not in SECTION_HEADERS]
    return ("\n" * 2).join(SECTION_HEADERS.get(s, s.title()) + "\n" + "\n".join(groups[s]) for s in order)
