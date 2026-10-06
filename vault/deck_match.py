"""Find a saved deck from how a person names it ("my sliver swarm deck"), without an id (issue #96).

Words are compared without case, accents or punctuation; filler words ("my", "the", "deck") are ignored; a deck matches
when every remaining word of the query starts a word of its name (so "sliver" finds "Sliver Swarm tuned with rage" and
"drag" finds "The dragon in the night"). Matches are ranked by how much of the name the query covers. When nothing
matches, the closest names are offered, never a guess.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

FILLER = {"my", "the", "a", "an", "deck", "decks", "of", "for", "your", "our", "this", "that", "list", "decklist"}


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", folded)


def query_words(q: str) -> list[str]:
    kept = [w for w in words(q) if w not in FILLER]
    return kept or words(q)


def search(names: dict[int, str], q: str, limit: int = 25) -> tuple[list[int], list[str]]:
    """(ids of matching decks, best first; closest names when none match)."""
    wanted = query_words(q)
    if not wanted:
        return [], []
    scored: list[tuple[float, str, int]] = []
    for deck_id, name in names.items():
        parts = words(name)
        if all(any(p.startswith(w) for p in parts) for w in wanted):
            covered = sum(len(w) for w in wanted) / max(1, sum(len(p) for p in parts))
            scored.append((-covered, name.lower(), deck_id))
    if scored:
        return [i for _, _, i in sorted(scored)[:limit]], []
    return [], _closest(names, wanted)


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _closest(names: dict[int, str], wanted: list[str], n: int = 3) -> list[str]:
    """Names whose words are each a near-miss of a query word (a typo), best first; nothing for an unrelated query."""
    scored = []
    for name in names.values():
        parts = words(name)
        best = [max((_similar(w, p) for p in parts), default=0.0) for w in wanted]
        if min(best) >= 0.7 and sum(best) / len(best) >= 0.8:
            scored.append((-sum(best) / len(best), name))
    return [name for _, name in sorted(scored)[:n]]
