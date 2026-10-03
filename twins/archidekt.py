"""Twin of Archidekt's (unofficial) read API: ``/api/decks/{id}/`` and ``/api/decks/v3/``.

Archidekt is a Django REST Framework site, so errors are ``{"detail": "..."}`` and lists are
``{"count", "next", "previous", "results"}`` with absolute ``next`` links. Private decks
answer 404, like a missing deck. The deck shape is what ``mtg_toolkits.archidekt`` reads,
plus the fields around it.
"""

from __future__ import annotations

import itertools
import zlib
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from .base import Request, Twin, json_response

PAGE = 50
MODIFIERS = {"nonfoil": "Normal", "foil": "Foil", "etched": "Etched"}
DEFAULT_CATEGORIES = [
    {"name": "Commander", "isPremier": True, "includedInDeck": True, "includedInPrice": True},
    {"name": "Maybeboard", "isPremier": False, "includedInDeck": False, "includedInPrice": False},
    {"name": "Sideboard", "isPremier": False, "includedInDeck": False, "includedInPrice": False},
]


def _num(*parts) -> int:
    return zlib.crc32(repr(parts).encode()) % 10**6


class ArchidektTwin(Twin):
    name = "archidekt"
    hosts = ("archidekt.com",)

    def __init__(self, scryfall=None):
        super().__init__()
        self.scryfall = scryfall  # to fill card details from the Scryfall twin's cards
        self.decks: dict[int, dict] = {}
        self._ids = itertools.count(1_000_000)
        self.route("GET", "archidekt.com", "/api/decks/v3/", self._search)
        self.route("GET", "archidekt.com", "/api/decks/{id}/", self._deck)

    def error(self, status, code, details):
        return json_response(status, {"detail": details})

    def add_deck(self, name: str, owner: str, cards: list[tuple], *, deck_id: int | None = None,
                 private: bool = False, unlisted: bool = False, deck_format: int = 3, description: str = "") -> dict:
        """``cards``: ``(quantity, name, set_code, collector_number, category, finish)`` tuples;
        everything after ``name`` is optional."""
        deck_id = deck_id or next(self._ids)
        now = datetime.now(timezone.utc).isoformat()
        entries = []
        for i, spec in enumerate(cards):
            qty, cname, set_code, number, category, finish = (list(spec) + [None] * 6)[:6]
            sf = self.scryfall.find(cname, set_code, number) if self.scryfall else None
            set_code = set_code or (sf and sf["set"]) or ""
            number = number or (sf and sf["collector_number"]) or ""
            entries.append({
                "id": deck_id * 100 + i, "categories": [category] if category else [], "companion": False,
                "flippedDefault": False, "label": ",#656565", "modifier": MODIFIERS[finish or "nonfoil"],
                "quantity": qty, "customCmc": None, "removedCategories": None, "createdAt": now, "updatedAt": now,
                "deletedAt": None, "notes": None,
                "card": {
                    "id": _num((cname, set_code, number)), "artist": sf and sf.get("artist"),
                    "uid": sf and sf["id"], "displayName": None, "releasedAt": sf and sf.get("released_at"),
                    "edition": {"editioncode": set_code, "editionname": (sf and sf.get("set_name")) or set_code.upper(),
                                "editiondate": sf and sf.get("released_at"), "editiontype": "expansion", "mtgoCode": None},
                    "flavor": None, "games": ["paper"], "options": ["Normal", "Foil"], "scryfallImageHash": None,
                    "oracleCard": {"id": _num(cname), "name": cname, "cmc": sf and sf.get("cmc"),
                                   "colorIdentity": [], "colors": [], "layout": "normal", "manaCost": sf and sf.get("mana_cost"),
                                   "text": sf and sf.get("oracle_text"), "types": [], "subTypes": [], "superTypes": [],
                                   "faces": [], "legalities": {}, "uid": sf and sf.get("oracle_id")},
                    "owned": 0, "pinnedStatus": 0, "rarity": sf and sf.get("rarity"), "globalCategories": [],
                    "collectorNumber": number, "prices": {"tcg": float((sf and sf["prices"].get("usd")) or 0), "ck": 0},
                },
            })
        deck = {
            "id": deck_id, "name": name, "createdAt": now, "updatedAt": now, "deckFormat": deck_format,
            "edhBracket": None, "game": None, "description": description, "viewCount": 0, "featured": "",
            "customFeatured": "", "private": private, "unlisted": unlisted, "theorycrafted": False, "points": 0,
            "userInput": 0, "ownerId": _num(owner),
            "owner": {"id": _num(owner), "username": owner, "avatar": "", "frame": None,
                      "ckAffiliate": "", "tcgAffiliate": "", "referrerEnum": None},
            "commentRoot": deck_id, "editors": [], "parentFolder": None, "bookmarked": False, "tags": [],
            "playgroupDeckUrl": None, "cardPackage": None,
            "categories": [{"id": deck_id * 10 + i, **c} for i, c in enumerate(DEFAULT_CATEGORIES)],
            "cards": entries,
        }
        self.decks[deck_id] = deck
        return deck

    def _deck(self, req: Request) -> httpx.Response:
        try:
            deck = self.decks.get(int(req.params["id"]))
        except ValueError:
            deck = None
        if deck is None or deck["private"]:
            return self.error(404, "not_found", "Not found.")
        return json_response(200, deck)

    def _search(self, req: Request) -> httpx.Response:
        q = req.query
        decks = [d for d in self.decks.values() if not d["private"] and not d["unlisted"]]
        if q.get("ownerUsername"):
            decks = [d for d in decks if d["owner"]["username"].lower() == q["ownerUsername"].lower()]
        if q.get("name"):
            decks = [d for d in decks if q["name"].lower() in d["name"].lower()]
        if q.get("cardName"):
            decks = [d for d in decks if any(q["cardName"].lower() == c["card"]["oracleCard"]["name"].lower() for c in d["cards"])]
        if q.get("deckFormat"):
            decks = [d for d in decks if str(d["deckFormat"]) == q["deckFormat"]]
        decks.sort(key=lambda d: d["updatedAt"], reverse=True)
        page = int(q.get("page", "1"))
        chunk = decks[(page - 1) * PAGE: page * PAGE]

        def page_url(n):
            return "https://archidekt.com/api/decks/v3/?" + urlencode({**q, "page": n})

        return json_response(200, {
            "count": len(decks),
            "next": page_url(page + 1) if page * PAGE < len(decks) else None,
            "previous": page_url(page - 1) if page > 1 else None,
            "results": [{
                "id": d["id"], "name": d["name"], "size": sum(c["quantity"] for c in d["cards"]),
                "updatedAt": d["updatedAt"], "createdAt": d["createdAt"], "deckFormat": d["deckFormat"],
                "edhBracket": d["edhBracket"], "featured": d["featured"], "customFeatured": d["customFeatured"],
                "viewCount": d["viewCount"], "private": d["private"], "unlisted": d["unlisted"],
                "theorycrafted": d["theorycrafted"], "game": d["game"], "hasDescription": bool(d["description"]),
                "tags": d["tags"], "parentFolderId": None, "owner": d["owner"], "colors": {}, "cardPackage": None,
            } for d in chunk],
        })
