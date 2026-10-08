"""Reading a decklist's text: ``mtg_toolkits.decklist.parse_text`` plus Moxfield's ``*CMDR*`` marker (#332).

Moxfield marks the commander on its own line (``1 Atraxa, Praetors' Voice (CMM) 1068 *CMDR*``) instead of giving it a section.
The parser takes the marker as part of the name, so the card was never found (no price, no owner count) and the set code and
number stayed in the name. Here a marked line is moved into a Commander section, without its marker, before the parser reads it.
"""

from __future__ import annotations

import re

from mtg_toolkits import decklist

CMDR = re.compile(r"\s*\*CMDR\*\s*$", re.IGNORECASE)


def parse_text(text: str) -> decklist.Decklist:
    """``decklist.parse_text`` with every ``*CMDR*`` line read as a Commander-section line."""
    lines = text.splitlines()
    marked = [CMDR.sub("", line) for line in lines if CMDR.search(line)]
    if not marked:
        return decklist.parse_text(text)
    rest = [line for line in lines if not CMDR.search(line)]
    return decklist.parse_text("\n".join(["Commander", *marked, "", "Deck", *rest]))
