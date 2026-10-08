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
