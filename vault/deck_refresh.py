"""Refresh a saved deck from its stored link (issue #217): what changed, per section and card, before anything is
replaced.

The Vault keeps each deck's source link. Refreshing reads the source's current list (Archidekt only, one public deck,
through the 10-minute cache: docs/compliance.md), compares it with the saved list, and returns the changes and a
fingerprint of the list it read. Replacing the saved list needs that fingerprint back, so what is applied is exactly
what the person saw: if the source changed in between, the refresh is refused and previewed again.
"""

from __future__ import annotations

import hashlib

from mtg_toolkits import decklist

from . import deck_text

SECTION_NAMES = {"commander": "Commander", "main": "Deck", "sideboard": "Sideboard", "maybeboard": "Maybeboard",
                 "companion": "Companion"}


def _cards(text: str) -> dict[tuple[str, str], tuple[str, int]]:
    """``{(section, lower-cased name): (name, quantity)}`` of a list's text."""
    out: dict[tuple[str, str], tuple[str, int]] = {}
    for line in deck_text.parse_text(text).lines:
        key = (line.section or "main", line.name.lower())
        name, qty = out.get(key, (line.name, 0))
        out[key] = (name, qty + line.quantity)
    return out


def diff(saved: str, current: str) -> list[dict]:
    """Each card whose count changed: ``{"section", "card", "before", "after"}``, in section then name order."""
    old, new = _cards(saved), _cards(current)
    order = list(SECTION_NAMES)
    changes = []
    for key in sorted(set(old) | set(new), key=lambda k: (order.index(k[0]) if k[0] in order else 99, k[1])):
        before, after = old.get(key, ("", 0))[1], new.get(key, ("", 0))[1]
        if before != after:
            changes.append({"section": SECTION_NAMES.get(key[0], key[0]), "card": (new.get(key) or old.get(key))[0],
                            "before": before, "after": after})
    return changes


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def summary(changes: list[dict]) -> dict:
    return {"added": sum(1 for c in changes if c["before"] == 0), "removed": sum(1 for c in changes if c["after"] == 0),
            "changed": sum(1 for c in changes if c["before"] and c["after"]),
            "copies_in": sum(max(0, c["after"] - c["before"]) for c in changes),
            "copies_out": sum(max(0, c["before"] - c["after"]) for c in changes)}
