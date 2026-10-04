"""Combos from Commander Spellbook, asked for on demand.

The Vault keeps no copy of their data (docs/data-sources.md): when a person asks about a deck, one call
goes to Commander Spellbook's public API and the answer is shortened to what an agent needs. Combo
descriptions are theirs (written by their community) and are shown as theirs, with a link to each
combo's page. A slow or failing upstream becomes a clear error; it never slows anything else.
"""

from __future__ import annotations

import httpx

URL = "https://backend.commanderspellbook.com/find-my-combos"
PAGE_URL = "https://commanderspellbook.com/combo/"
USER_AGENT = "the-vault/0.1 (+https://github.com/colombod-personal/the-vault)"
TIMEOUT = 15.0
MAX_PER_GROUP = 10
MAX_CARDS = 600


class ComboServiceError(RuntimeError):
    """Commander Spellbook could not answer (down, slow, or it refused the request)."""


def ask(cards: list[tuple[str, int]], commanders: list[str], transport: httpx.BaseTransport | None = None) -> dict:
    body = {"main": [{"card": name, "quantity": qty} for name, qty in cards[:MAX_CARDS]],
            "commanders": [{"card": name, "quantity": 1} for name in commanders[:12]]}
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
            res = client.post(URL, params={"limit": 1}, json=body)
    except httpx.HTTPError as exc:
        raise ComboServiceError("Commander Spellbook did not answer in time.") from exc
    if res.status_code != 200:
        raise ComboServiceError(f"Commander Spellbook answered {res.status_code}.")
    try:
        return res.json()["results"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ComboServiceError("Commander Spellbook's answer was not in the expected shape.") from exc


def _variant(v: dict, have: set[str]) -> dict:
    cards = [u["card"]["name"] for u in v.get("uses", []) if u.get("card")]
    return {
        "id": v["id"], "url": PAGE_URL + str(v["id"]), "cards": cards,
        "missing": [c for c in cards if c.lower() not in have],
        "produces": [p["feature"]["name"] for p in v.get("produces", []) if p.get("feature")],
        "description": v.get("description") or "", "mana_needed": v.get("manaNeeded") or None,
        "prerequisites": " ".join(x for x in (v.get("easyPrerequisites"), v.get("notablePrerequisites")) if x) or None,
        "color_identity": v.get("identity"), "popularity": v.get("popularity"), "bracket_tag": v.get("bracketTag"),
    }


def summarize(results: dict, deck_names: set[str]) -> dict:
    """The combos in the deck, and the ones a card short, shortened and capped."""
    have = {n.lower() for n in deck_names}
    groups = {"included": results.get("included") or [], "almost_included": results.get("almostIncluded") or []}
    out = {name: [_variant(v, have) for v in found[:MAX_PER_GROUP]] for name, found in groups.items()}
    return {**out, "totals": {name: len(found) for name, found in groups.items()},
            "notes": ["Combos and their descriptions are Commander Spellbook's, written by its community; check each on its page before relying on it.",
                      "'almost_included' combos need the cards listed under 'missing'. Other groups (other commanders, more colors) are not shown."]}
