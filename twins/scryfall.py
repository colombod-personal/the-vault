"""Twin of Scryfall: the REST API (api.scryfall.com), bulk files (data.scryfall.io) and card
images (cards.scryfall.io).

It enforces Scryfall's published rules:
- ``User-Agent`` and ``Accept`` headers are required.
- Rate limits: 500 ms between calls to /cards/search, /cards/named, /cards/random and
  /cards/collection, and 100 ms for everything else. Going faster gets a 429 and a 30-second lockout.
- At most 75 identifiers per /cards/collection request.

Errors use Scryfall's ``{"object": "error", ...}`` shape. It is seeded with a few real cards
(``twins/data/scryfall_cards.json``); add more with :meth:`add_card`, and move prices with
:meth:`set_price`.
"""

from __future__ import annotations

import gzip
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from .base import Request, Twin, json_response

DATA = Path(__file__).parent / "data"
SLOW = ("/cards/search", "/cards/named", "/cards/random", "/cards/collection")
PAGE = 175
CORS = {"access-control-allow-origin": "*"}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower())


class ScryfallTwin(Twin):
    name = "scryfall"
    hosts = ("api.scryfall.com", "data.scryfall.io", "cards.scryfall.io", "svgs.scryfall.io")

    def __init__(self, seed: bool = True, *, enforce_rate_limits: bool = False, clock=time.monotonic):
        super().__init__()
        self.cards: dict[str, dict] = {}
        self.rulings: list[dict] = []
        self.oracle_tags: list[dict] = []
        self.enforce_rate_limits = enforce_rate_limits
        self.require_headers = True
        self.clock = clock
        self._last: dict[str, float] = {}
        self._locked_until = 0.0
        self.bulk_updated_at = datetime.now(timezone.utc).replace(microsecond=0)
        if seed:
            for card in json.loads((DATA / "scryfall_cards.json").read_text(encoding="utf-8")):
                self.cards[card["id"]] = card
        a = "api.scryfall.com"
        self.route("GET", a, "/cards/named", self._named)
        self.route("GET", a, "/cards/search", self._search)
        self.route("GET", a, "/cards/autocomplete", self._autocomplete)
        self.route("POST", a, "/cards/collection", self._collection)
        self.route("GET", a, "/cards/{set}/{number}/{lang}", self._by_number)
        self.route("GET", a, "/cards/{set}/{number}", self._by_number)
        self.route("GET", a, "/cards/{id}", self._by_id)
        self.route("GET", a, "/sets", self._sets)
        self.route("GET", a, "/sets/{code}", self._set)
        self.route("GET", a, "/bulk-data", self._bulk_list)
        self.route("GET", a, "/bulk-data/{type}", self._bulk_entry)
        self.route("GET", "data.scryfall.io", "/{type}/{file}", self._bulk_file)
        self.route("GET", "cards.scryfall.io", "/{path:path}", self._image)
        self.route("GET", "svgs.scryfall.io", "/sets/{file}", self._set_icon)

    # -- state ---------------------------------------------------------------------------
    def add_card(self, name: str, set_code: str, collector_number: str, *, finishes=("nonfoil",),
                 prices: dict | None = None, **fields) -> dict:
        """Add a card with a realistic shape. ``prices`` like ``{"usd": 1.5, "usd_foil": 3}``."""
        cid = fields.pop("id", None) or str(uuid.uuid5(uuid.NAMESPACE_URL, f"{set_code}/{collector_number}/{name}"))
        img = f"https://cards.scryfall.io/{{size}}/front/{cid[0]}/{cid[1]}/{cid}.jpg"
        card = {
            "object": "card", "id": cid, "oracle_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
            "multiverse_ids": [], "lang": "en", "released_at": "2021-04-23",
            "uri": f"https://api.scryfall.com/cards/{cid}",
            "scryfall_uri": f"https://scryfall.com/card/{set_code}/{collector_number}/{quote(name.lower().replace(' ', '-'))}",
            "layout": "normal", "highres_image": True, "image_status": "highres_scan",
            "image_uris": {s: img.format(size=s) for s in ("small", "normal", "large", "png", "art_crop", "border_crop")},
            "mana_cost": "", "cmc": 0.0, "type_line": "Artifact", "oracle_text": "", "colors": [], "color_identity": [],
            "keywords": [], "legalities": {"commander": "legal", "vintage": "legal"}, "games": ["paper"],
            "reserved": False, "foil": "foil" in finishes, "nonfoil": "nonfoil" in finishes, "finishes": list(finishes),
            "oversized": False, "promo": False, "reprint": True, "variation": False, "set_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, set_code)),
            "set": set_code, "set_name": fields.pop("set_name", set_code.upper()), "set_type": "expansion",
            "collector_number": collector_number, "digital": False, "rarity": "common",
            "artist": "Twin Artist", "border_color": "black", "frame": "2015", "full_art": False, "textless": False,
            "booster": True, "story_spotlight": False,
            "prices": {k: None for k in ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "tix")},
            "related_uris": {}, "purchase_uris": {},
        }
        card.update(fields)
        card["name"] = name
        for k, v in (prices or {}).items():
            card["prices"][k] = None if v is None else f"{float(v):.2f}"
        self.cards[cid] = card
        return card

    def add_ruling(self, oracle_id: str, comment: str, *, source: str = "wotc", published_at: str = "2020-01-01") -> dict:
        ruling = {"object": "ruling", "oracle_id": oracle_id, "source": source, "published_at": published_at, "comment": comment}
        self.rulings.append(ruling)
        return ruling

    def add_tag(self, slug: str, *, cards: dict[str, str] | None = None, children: list[str] | None = None,
                parents: list[str] | None = None, label: str | None = None) -> dict:
        """Add a Tagger tag. `cards` maps an Oracle id to a weight; `children`/`parents` are tag ids."""
        tag = {"object": "tag", "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "tag:" + slug)), "label": label or slug, "slug": slug,
               "type": "oracle", "uri": f"https://tagger.scryfall.com/tags/card/{slug}", "description": "", "aliases": [],
               "parent_ids": list(parents or []), "child_ids": list(children or []),
               "taggings": [{"oracle_id": o, "weight": w} for o, w in (cards or {}).items()]}
        self.oracle_tags.append(tag)
        return tag

    def set_price(self, card_id: str, **prices: float | None) -> None:
        for k, v in prices.items():
            self.cards[card_id]["prices"][k] = None if v is None else f"{float(v):.2f}"
        self.bulk_updated_at = datetime.now(timezone.utc).replace(microsecond=0)

    def find(self, name: str | None = None, set_code: str | None = None, number: str | None = None) -> dict | None:
        for c in self.cards.values():
            names = {_norm(c["name"])} | {_norm(f["name"]) for f in c.get("card_faces", [])}
            if name is not None and _norm(name) not in names:
                continue
            if set_code is not None and c["set"] != set_code.lower():
                continue
            if number is not None and c["collector_number"] != number:
                continue
            return c
        return None

    def reset(self) -> None:
        super().reset()
        self._last.clear()
        self._locked_until = 0.0
        self.rulings.clear()
        self.oracle_tags.clear()

    def state(self) -> dict:
        return super().state() | {"cards": len(self.cards), "rulings": len(self.rulings), "oracle_tags": len(self.oracle_tags), "enforce_rate_limits": self.enforce_rate_limits}

    # -- rules ---------------------------------------------------------------------------
    def error(self, status, code, details):
        return json_response(status, {"object": "error", "code": code, "status": status, "details": details}, CORS)

    def _dispatch(self, req, call):
        if req.url.host == "api.scryfall.com":
            if self.require_headers and not (req.headers.get("user-agent") and req.headers.get("accept")):
                return self.error(400, "bad_request", "API requests must include a User-Agent and an Accept header.")
            if self.enforce_rate_limits:
                now = self.clock()
                if now < self._locked_until:
                    return self.error(429, "rate_limited", "You are being rate limited. Wait 30 seconds.")
                bucket = "slow" if req.url.path.startswith(SLOW) else "fast"
                gap = 0.5 if bucket == "slow" else 0.1
                if now - self._last.get(bucket, -1e9) < gap:
                    self._locked_until = now + 30
                    return self.error(429, "rate_limited", "Too many requests. Slow down and wait 30 seconds.")
                self._last[bucket] = now
        return super()._dispatch(req, call)

    def _ok(self, body) -> httpx.Response:
        return json_response(200, body, CORS)

    def _list(self, cards: list[dict], req: Request | None = None, page: int = 1) -> dict:
        chunk = cards[(page - 1) * PAGE: page * PAGE]
        body = {"object": "list", "total_cards": len(cards), "has_more": page * PAGE < len(cards), "data": chunk}
        if body["has_more"] and req is not None:
            q = dict(req.query, page=str(page + 1))
            body["next_page"] = "https://api.scryfall.com/cards/search?" + "&".join(f"{k}={quote(v)}" for k, v in q.items())
        return body

    # -- endpoints -----------------------------------------------------------------------
    def _by_id(self, req: Request) -> httpx.Response:
        card = self.cards.get(req.params["id"])
        return self._ok(card) if card else self.error(404, "not_found", "No card found with the given ID.")

    def _by_number(self, req: Request) -> httpx.Response:
        card = self.find(None, req.params["set"], req.params["number"])
        return self._ok(card) if card else self.error(404, "not_found", "No card found with the given set and collector number.")

    def _named(self, req: Request) -> httpx.Response:
        exact, fuzzy, set_code = req.query.get("exact"), req.query.get("fuzzy"), req.query.get("set")
        if exact:
            card = self.find(exact, set_code)
        elif fuzzy:
            matches = [c for c in self.cards.values() if _norm(fuzzy) in _norm(c["name"])
                       and (not set_code or c["set"] == set_code.lower())]
            names = {c["name"] for c in matches}
            if len(names) > 1:
                return self.error(404, "not_found", "Too many cards match ambiguous name “%s”. Add more words to refine your search." % fuzzy)
            card = matches[0] if matches else None
        else:
            return self.error(400, "bad_request", "You must provide an exact or fuzzy parameter.")
        return self._ok(card) if card else self.error(404, "not_found", "No cards found matching “%s”" % (exact or fuzzy))

    def _search(self, req: Request) -> httpx.Response:
        q = req.query.get("q", "")
        if not q.strip():
            return self.error(400, "bad_request", "You didn’t enter anything to search for.")
        cards = [c for c in self.cards.values() if self._matches(c, q)]
        if req.query.get("unique", "cards") == "cards":
            seen, unique = set(), []
            for c in cards:
                if c["oracle_id"] not in seen:
                    seen.add(c["oracle_id"])
                    unique.append(c)
            cards = unique
        if req.query.get("order") == "released":
            cards.sort(key=lambda c: (c.get("released_at", ""), c["collector_number"]), reverse=True)
        else:
            cards.sort(key=lambda c: c["name"])
        if not cards:
            return self.error(404, "not_found", "Your query didn’t match any cards. Adjust your search terms or refer to the syntax guide at https://scryfall.com/docs/reference")
        return self._ok(self._list(cards, req, int(req.query.get("page", "1"))))

    @staticmethod
    def _matches(card: dict, q: str) -> bool:
        for term in re.findall(r'!"[^"]+"|\S+:"[^"]+"|\S+', q):
            if term.startswith('!"'):
                if _norm(card["name"]) != _norm(term[2:-1]):
                    return False
            elif ":" in term:
                key, value = term.split(":", 1)
                value = value.strip('"').lower()
                if key in ("s", "e", "set", "edition") and card["set"] != value:
                    return False
                if key in ("t", "type") and value not in (card.get("type_line") or "").lower():
                    return False
                if key in ("a", "artist") and value not in (card.get("artist") or "").lower():
                    return False
                if key in ("oracleid", "oid") and card["oracle_id"] != value:
                    return False
            elif _norm(term) not in _norm(card["name"]):
                return False
        return True

    def _autocomplete(self, req: Request) -> httpx.Response:
        q = _norm(req.query.get("q", ""))
        names = sorted({c["name"] for c in self.cards.values() if q and _norm(c["name"]).startswith(q)})[:20]
        return self._ok({"object": "catalog", "total_values": len(names), "data": names})

    def _collection(self, req: Request) -> httpx.Response:
        try:
            idents = (req.json() or {}).get("identifiers")
        except ValueError:
            idents = None
        if not isinstance(idents, list):
            return self.error(400, "bad_request", "Your request must include a JSON body with an identifiers array.")
        if len(idents) > 75:
            return self.error(422, "too_many_identifiers", "Too many identifiers. You may only request up to 75 cards at once.")
        found, missing = [], []
        for ident in idents:
            card = None
            if "id" in ident:
                card = self.cards.get(ident["id"])
            elif "set" in ident and "collector_number" in ident:
                card = self.find(None, ident["set"], ident["collector_number"])
            elif "name" in ident:
                card = self.find(ident["name"], ident.get("set"))
            elif "oracle_id" in ident:
                card = next((c for c in self.cards.values() if c["oracle_id"] == ident["oracle_id"]), None)
            (found if card else missing).append(card or ident)
        return self._ok({"object": "list", "not_found": missing, "data": found})

    def _sets(self, req: Request) -> httpx.Response:
        return self._ok({"object": "list", "has_more": False, "data": [self._set_obj(c) for c in self._set_codes()]})

    def _set(self, req: Request) -> httpx.Response:
        code = req.params["code"].lower()
        if code not in self._set_codes():
            return self.error(404, "not_found", "No Magic set found for the given code.")
        return self._ok(self._set_obj(code))

    def _set_codes(self) -> list[str]:
        return sorted({c["set"] for c in self.cards.values()})

    def _set_obj(self, code: str) -> dict:
        sample = next(c for c in self.cards.values() if c["set"] == code)
        return {"object": "set", "id": sample.get("set_id"), "code": code, "name": sample.get("set_name"),
                "set_type": sample.get("set_type", "expansion"), "released_at": sample.get("released_at"),
                "card_count": sum(c["set"] == code for c in self.cards.values()), "digital": False,
                "icon_svg_uri": f"https://svgs.scryfall.io/sets/{code}.svg",
                "uri": f"https://api.scryfall.com/sets/{code}", "scryfall_uri": f"https://scryfall.com/sets/{code}"}

    # -- bulk data -----------------------------------------------------------------------
    BULK_TYPES = ("oracle_cards", "unique_artwork", "default_cards", "all_cards", "rulings", "art_tags", "oracle_tags")

    def _bulk_objects(self, kind: str) -> list[dict]:
        """What each bulk file holds: one card per Oracle id for ``oracle_cards``, every printing
        for the others, and the rulings and tags the twin was given."""
        if kind == "rulings":
            return list(self.rulings)
        if kind == "oracle_tags":
            return list(self.oracle_tags)
        if kind == "art_tags":
            return []
        if kind == "oracle_cards":
            return list({c["oracle_id"]: c for c in reversed(list(self.cards.values()))}.values())
        return list(self.cards.values())

    def _bulk_gz(self, kind: str) -> bytes:
        return gzip.compress("".join(json.dumps(o) + "\n" for o in self._bulk_objects(kind)).encode())

    def _bulk_entry_obj(self, kind: str) -> dict:
        # The shape Scryfall serves today: only the .jsonl.gz link and its compressed size.
        stamp = self.bulk_updated_at.strftime("%Y%m%d%H%M%S")
        slug = kind.replace("_", "-")
        return {
            "object": "bulk_data", "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, kind)), "type": kind,
            "updated_at": self.bulk_updated_at.isoformat(), "uri": f"https://api.scryfall.com/bulk-data/{slug}",
            "name": kind.replace("_", " ").title(), "description": "A twin bulk file.",
            "jsonl_download_uri": f"https://data.scryfall.io/{slug}/{slug}-{stamp}.jsonl.gz",
            "compressed_size": len(self._bulk_gz(kind)),
        }

    def _bulk_list(self, req: Request) -> httpx.Response:
        return self._ok({"object": "list", "has_more": False, "data": [self._bulk_entry_obj(k) for k in self.BULK_TYPES]})

    def _bulk_entry(self, req: Request) -> httpx.Response:
        kind = req.params["type"].replace("-", "_")
        if kind not in self.BULK_TYPES:
            return self.error(404, "not_found", "No bulk data found.")
        return self._ok(self._bulk_entry_obj(kind))

    def _bulk_file(self, req: Request) -> httpx.Response:
        kind = req.params["type"].replace("-", "_")
        if kind in self.BULK_TYPES and req.params["file"].endswith(".jsonl.gz"):
            return httpx.Response(200, content=self._bulk_gz(kind), headers={"content-type": "application/gzip"})
        return httpx.Response(404, content=b"Not Found")

    # -- images --------------------------------------------------------------------------
    def _set_icon(self, req: Request) -> httpx.Response:
        code = req.params["file"].split(".")[0][:4].upper()
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="15" fill="#888"/>
<text x="16" y="20" text-anchor="middle" font-family="sans-serif" font-size="9" fill="#fff">{code}</text></svg>"""
        return httpx.Response(200, content=svg.encode(), headers={"content-type": "image/svg+xml", **CORS})

    def _image(self, req: Request) -> httpx.Response:
        cid = req.params["path"].rsplit("/", 1)[-1].split(".")[0]
        card = self.cards.get(cid)
        if card is None:
            return httpx.Response(404, content=b"Not Found")
        name = card["name"].replace("&", "&amp;").replace("<", "&lt;")
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="488" height="680" viewBox="0 0 488 680">
<rect width="488" height="680" rx="24" fill="#222"/><rect x="24" y="24" width="440" height="632" rx="12" fill="#6b5a3a"/>
<text x="44" y="70" font-family="serif" font-size="28" fill="#fff">{name}</text>
<text x="44" y="640" font-family="sans-serif" font-size="16" fill="#ddd">Illus. {card.get('artist', '')} · twin image</text></svg>"""
        return httpx.Response(200, content=svg.encode(), headers={"content-type": "image/svg+xml", **CORS})

