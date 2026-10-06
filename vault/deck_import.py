"""A public Archidekt deck turned into the Vault's own decklist text, with its sections kept (issue #96, step 3).

The model should not have to convert Archidekt's JSON to text by hand: the server fetches the one deck (through the
cache, ``vault.archidekt_cache``) and writes it out with ``Commander``, ``Deck``, ``Sideboard`` and ``Maybeboard``
headers, which ``mtg_toolkits.decklist`` already reads. Only the deck's public list is kept, with its author's public
username for the credit; the deck is Archidekt's and stays labelled so.
"""

from __future__ import annotations

SECTIONS = (("Commander", "Commander"), ("Deck", "main"), ("Sideboard", "sideboard"), ("Maybeboard", "maybeboard"))


def canonical_url(deck_id: int | str) -> str:
    return f"https://archidekt.com/decks/{int(deck_id)}"


def section_of(categories: list[str]) -> str:
    lowered = {c.strip().lower() for c in categories or []}
    if "commander" in lowered:
        return "Commander"
    if "sideboard" in lowered:
        return "Sideboard"
    if lowered & {"maybeboard", "considering"}:
        return "Maybeboard"
    return "Deck"


# Archidekt's deckFormat numbers, only those checked against a real deck (3: Commander, "Sliver Swarm", 2026-10-06).
# Unknown numbers leave the format to be read from the list (vault.deck_overview) rather than guessed.
ARCHIDEKT_FORMATS = {3: "commander"}


def to_decklist(raw: dict) -> dict:
    """``{"name", "author", "text", "counts", "format"}`` from Archidekt's deck JSON; the list keeps its sections."""
    buckets: dict[str, dict[str, int]] = {name: {} for name, _ in SECTIONS}
    for entry in raw.get("cards") or []:
        card = (entry.get("card") or {})
        name = ((card.get("oracleCard") or {}).get("name") or card.get("name") or "").strip()
        if not name:
            continue
        quantity = int(entry.get("quantity") or 1)
        bucket = buckets[section_of(entry.get("categories") or [])]
        bucket[name] = bucket.get(name, 0) + max(1, quantity)  # two printings of one card are one line
    lines: list[str] = []
    for header, _ in SECTIONS:
        cards = buckets[header]
        if cards:
            lines.append(header)
            lines.extend(f"{q} {n}" for n, q in cards.items())
    return {"name": (raw.get("name") or "").strip(), "author": ((raw.get("owner") or {}).get("username") or "").strip() or None,
            "text": "\n".join(lines), "counts": {header: sum(cards.values()) for header, cards in buckets.items() if cards},
            "format": ARCHIDEKT_FORMATS.get(raw.get("deckFormat"))}
