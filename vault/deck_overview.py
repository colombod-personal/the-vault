"""What a deck is at a glance (issue #216): its format, its commander(s), how many cards, the commanders' colour identity.

People ask about their decks by name and expect "Sliver Overlord, Commander, 100 cards" before any card line. The card
text stays the source of truth; this reads it:

- **commanders**: the Commander section (pasted lists and Archidekt imports keep one, including Archidekt's Commander
  category) or Moxfield's ``*CMDR*`` marker. Partners and backgrounds are simply several commanders.
- **format**: the one stored on the deck wins (set by the person or their assistant, or by an Archidekt import, whose
  ``deckFormat`` 3 is Commander: checked on a real deck, 2026-10-06). Otherwise it is read from the list's shape: a
  commander with about 100 cards is Commander, a commander with 60 is Brawl-sized, 40 to 45 cards with no commander is
  a Limited deck. Otherwise unknown: a 60-card constructed list fits several formats (deck_legality says which).
  ``format_from`` always says which, so nobody mistakes a reading for a fact.
- **color_identity**: the union of the commanders' colour identities, from the card catalog (Scryfall's Oracle data).
"""

from __future__ import annotations

import re

from mtg_toolkits import decklist
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import OracleCard

CMDR = re.compile(r"\s*\*CMDR\*\s*$", re.IGNORECASE)  # Moxfield's commander marker on an exported line
WUBRG = "WUBRG"
SIDE = {"sideboard": "sideboard", "maybeboard": "maybeboard", "companion": "companion"}


def read(text: str) -> dict:
    """Commanders and card counts from a decklist's text (no database)."""
    try:
        deck = decklist.parse_text(text)
    except (ValueError, OverflowError):
        return {"commanders": [], "cards": None, "readable": False}
    commanders: list[str] = []
    counts = {"main": 0, "sideboard": 0, "maybeboard": 0, "companion": 0}
    for line in deck.lines:
        name = CMDR.sub("", line.name).strip()
        if line.section == "commander" or CMDR.search(line.name):
            if name not in commanders:
                commanders.append(name)
            counts["main"] += line.quantity  # the commander is part of the 100
        else:
            counts[SIDE.get(line.section, "main")] += line.quantity
    return {"commanders": commanders, "cards": counts["main"], "readable": True,
            **{k: v for k, v in counts.items() if k != "main" and v}}


def identities(db: Session | None, names: list[str]) -> dict[str, list[str]]:
    """Colour identity per card name (lower-cased), for the names the catalog knows."""
    wanted = {n.lower() for n in names if n}
    if db is None or not wanted:
        return {}
    rows = db.execute(select(func.lower(OracleCard.name), OracleCard.color_identity)
                      .where(func.lower(OracleCard.name).in_(wanted))).all()
    return {name: list(colors or []) for name, colors in rows}


def overview(text: str, stored_format: str | None, known: dict[str, list[str]] | None = None) -> dict:
    """The at-a-glance block for one deck. ``known`` maps lower-cased card names to colour identities."""
    seen = read(text)
    cards = seen["cards"] or 0
    if stored_format:
        fmt, source = stored_format, "set on the deck"
    elif seen["commanders"] and 58 <= cards <= 60:  # not a half-built Commander deck
        fmt, source = "brawl", f"a commander with {cards} cards (Brawl's size; Standard or Historic Brawl)"
    elif seen["commanders"]:
        fmt, source = "commander", "the list names a commander"
    elif 40 <= cards <= 45:
        fmt, source = "limited", f"{cards} cards with no commander (a Limited deck's size)"
    else:
        fmt, source = None, None
    out = {"format": fmt, "format_from": source, "commanders": seen["commanders"], "cards": seen["cards"]}
    for extra in ("sideboard", "maybeboard", "companion"):
        if seen.get(extra):
            out[extra] = seen[extra]
    if seen["commanders"] and known is not None:
        colors = set()
        found = True
        for name in seen["commanders"]:
            identity = known.get(name.lower())
            if identity is None:
                found = False
                break
            colors.update(identity)
        out["color_identity"] = "".join(c for c in WUBRG if c in colors) or "colorless" if found else None
    if not seen["readable"]:
        out["note"] = "The saved list could not be read"
    return out


def archidekt_credit(d) -> dict | None:
    """The credit a saved deck from Archidekt carries in every answer about it: the source, the link, the author, when the
    Vault last took the list from that link (``fetched_at``), and the notice to repeat. None for any other deck."""
    from urllib.parse import urlsplit

    host = (urlsplit(d.source_url).hostname or "").lower() if d.source_url else ""
    if host != "archidekt.com" and not host.endswith(".archidekt.com"):
        return None
    fetched = d.source_fetched_at
    return {"source": "Archidekt", "url": d.source_url, "author": d.source_author,
            "fetched_at": fetched.isoformat() if fetched else None,
            "notice": "Deck list from Archidekt" + (f" by {d.source_author}" if d.source_author else "")
                      + ". The deck is theirs, not the Vault's."}
