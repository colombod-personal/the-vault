"""Twin of Commander Spellbook's backend (``backend.commanderspellbook.com``): ``POST /find-my-combos``.

Give it a deck (``{"main": [{"card", "quantity"}], "commanders": [...]}``) and it answers the way the
real service does: a paged envelope whose ``results`` hold ``included`` combos (every card is in the deck)
and ``almostIncluded`` ones (one card short). Combos are added with :meth:`add_combo`; the variant shape is
a subset of the real one (fields it has are real, nothing is invented). The real service needs no sign-in
for this call (checked 2026-10-04).
"""

from __future__ import annotations

import httpx

from .base import Request, Twin, json_response

HOST = "backend.commanderspellbook.com"
GROUPS = ("included", "includedByChangingCommanders", "almostIncluded", "almostIncludedByAddingColors",
          "almostIncludedByChangingCommanders", "almostIncludedByAddingColorsAndChangingCommanders")


class SpellbookTwin(Twin):
    name = "spellbook"
    hosts = (HOST,)

    def __init__(self, seed: bool = True):
        super().__init__()
        self.combos: list[dict] = []
        self.route("POST", HOST, "/find-my-combos", self._find)
        self.route("GET", HOST, "/find-my-combos", self._find)
        if seed:
            self.add_combo(["Thassa's Oracle", "Demonic Consultation"], ["Exile your library", "Win the game"],
                           "Cast Demonic Consultation naming a card not in your library, then cast Thassa's Oracle.", identity="UB",
                           mana_needed="{U}{U}{B}")

    def add_combo(self, cards: list[str], produces: list[str], description: str, *, identity: str = "C",
                  mana_needed: str = "", popularity: int = 100, bracket_tag: str = "R") -> dict:
        variant = {
            "id": f"{len(self.combos) + 1}-{len(cards)}", "status": "OK",
            "uses": [{"card": {"name": c}, "quantity": 1, "zoneLocations": ["H"], "mustBeCommander": False} for c in cards],
            "produces": [{"feature": {"name": p}, "quantity": 1} for p in produces],
            "identity": identity, "manaNeeded": mana_needed, "easyPrerequisites": "", "notablePrerequisites": "",
            "description": description, "popularity": popularity, "bracketTag": bracket_tag,
        }
        self.combos.append(variant)
        return variant

    def reset(self) -> None:
        super().reset()
        self.combos.clear()

    def error(self, status, code, details):
        return json_response(status, {"detail": details})

    def _find(self, req: Request) -> httpx.Response:
        if req.method == "GET":
            return json_response(200, {"count": None, "next": None, "previous": None, "results": {g: [] for g in GROUPS} | {"identity": "C"}})
        body = req.json() or {}
        have = {c["card"].lower() for c in (body.get("main") or []) + (body.get("commanders") or []) if c.get("card")}
        out: dict = {g: [] for g in GROUPS}
        for combo in self.combos:
            names = [u["card"]["name"].lower() for u in combo["uses"]]
            missing = [n for n in names if n not in have]
            if not missing:
                out["included"].append(combo)
            elif len(missing) == 1 and len(have & set(names)) >= 1:
                out["almostIncluded"].append(combo)
        return json_response(200, {"count": None, "next": None, "previous": None, "results": {"identity": "C", **out}})
