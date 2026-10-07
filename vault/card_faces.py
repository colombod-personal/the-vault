"""Reading a catalog card whose text lives on its faces (#197).

Scryfall documents that for a card with ``card_faces`` (transform, modal double-faced, split, adventure, flip) the overall
``mana_cost``, ``oracle_text``, ``power`` and ``toughness`` may be absent, and live on the faces. The catalog keeps them as
Scryfall gives them (``oracle_cards.oracle_text`` is null for a transform card), with the faces' own values in ``faces``. A
reader that wants a card's text must therefore ask here, never read ``card.oracle_text`` alone:

- :func:`all_text` joins every face's text (for rules that look for a phrase anywhere on the card);
- :func:`front_text` is the front face's text (for what a card does when it is played as itself);
- :func:`front_mana_cost` is the front face's mana cost.

The audit of every reader of colours and text is in ``docs/catalog-design.md`` ("Multi-faced cards: who reads what").
"""

from __future__ import annotations

FACE_SEPARATOR = "\n//\n"


def _faces(card) -> list[dict]:
    return [f for f in (getattr(card, "faces", None) or []) if isinstance(f, dict)]


def all_text(card) -> str:
    """Every face's Oracle text, joined; the card's own text when it has no faces or Scryfall gave one at the top level."""
    top = getattr(card, "oracle_text", None)
    if top:
        return top
    return FACE_SEPARATOR.join(f["oracle_text"] for f in _faces(card) if f.get("oracle_text"))


def front_text(card) -> str:
    """The front face's Oracle text (the top-level text for a single-faced card, or when Scryfall gave one)."""
    top = getattr(card, "oracle_text", None)
    if top:
        return top
    faces = _faces(card)
    return (faces[0].get("oracle_text") or "") if faces else ""


def front_mana_cost(card) -> str | None:
    top = getattr(card, "mana_cost", None)
    if top:
        return top
    faces = _faces(card)
    return faces[0].get("mana_cost") if faces else None
