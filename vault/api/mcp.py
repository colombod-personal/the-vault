"""The Vault as an MCP server, so people can point their own AI agents at their collection.

``POST /api/mcp`` speaks the Model Context Protocol over Streamable HTTP. It is stateless:
no server-side sessions and no server-sent events. Each JSON-RPC request gets one JSON answer, which
suits serverless hosting. (The one thing a client says at ``initialize`` that later answers depend on, whether it
supports MCP Apps, comes back in a signed ``Mcp-Session-Id``: see ``mcp_session.py``.) Authenticate with ``Authorization: Bearer <token>``: a personal
access token from Account → Agents & API (or ``POST /api/v1/me/tokens``).

Every tool is a thin wrapper around a ``/api/v1`` endpoint, called in-process with the
caller's credentials. Tenancy, sharing, scopes, paging and validation are therefore
enforced in one place. With a read-only token, write tools aren't listed, and calling one
fails with the API's 403.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable
from urllib.parse import parse_qs, parse_qsl, quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .. import experts, observability
from ..deck_tools import FORMATS
from ..models import User
from . import mcp_session, mcp_ui
from .mcp_catalog import GROUNDING, PROMPTS, catalog_tools, provenance_blocks, render_prompt
from .schemas import MAX_ID

log = logging.getLogger(__name__)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
V1 = "/api/v1"
PATH = "/api/mcp"
MAX_BATCH = 20  # calls in one JSON-RPC batch

INSTRUCTIONS = """\
The Vault holds one person's Magic: The Gathering collection (imported from Dragon Shield),
priced daily from Scryfall, plus their saved decks and what others have shared with them.
- Start with get_collection_summary. Use search_cards to find printings; it pages (pass
  next_cursor back as cursor), sorts with sort="-value" (most valuable first), and filters by
  query, set, name, finish and condition.
- check_decklist tells which cards of any pasted decklist the person owns, partly owns, or is missing,
  and what the missing copies would cost.
- Analytics are computed by the Vault: get_collection_summary has P&L (pnl, over copies with a
  known price paid only), get_collection_breakdowns splits the collection by colour, type, mana
  value and rarity, get_valuation gives value and cost by month, and list_card_names rolls the
  collection up by card name. refresh_prices (write) fetches today's prices; call it again with
  the cursor it returns until remaining is 0.
- Prices are USD market prices; "paid" is what the person paid (hidden on shared collections
  unless the owner allowed it).
- Collections shared with the person: list_shared_with_me, then pass share_id to the
  collection tools.
- Card data and images come from Scryfall. When you show a card, credit its artist and Scryfall.
"""


@dataclass
class Tool:
    name: str
    description: str
    properties: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    method: str | Callable[[dict], str] = "GET"  # a function for preview-then-confirm tools
    path: Callable[[dict], str] = lambda a: V1
    query: tuple[str, ...] = ()
    body: Callable[[dict], Any] | None = None
    write: bool = False
    destructive: bool = False  # deletes or revokes something: hosts should ask the person first
    title: str = ""
    # Where third-party data in the answer comes from: "scryfall" / "archidekt" (the server adds a provenance
    # block), "catalog" / "computed" (the API already includes one), or () for the person's own data only.
    provenance: tuple[str, ...] = ()
    ui: str = ""  # the MCP Apps view (vault/api/mcp_ui.py) a host may show next to this tool's result

    def schema(self, ui: bool = True) -> dict:
        out = {
            "name": self.name, "title": self.title or self.name.replace("_", " ").capitalize(),
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.properties, "required": self.required,
                            "additionalProperties": False},
            "annotations": {"readOnlyHint": not self.write, "destructiveHint": self.destructive, "openWorldHint": False},
        }
        if self.ui and ui:  # MCP Apps: hosts that support it show the view; others ignore this and show the text answer
            out["_meta"] = {"ui": {"resourceUri": mcp_ui.uri(self.ui), "visibility": ["model", "app"]}}
        return out


# How a re-import answers its three-way update (vault.merge): shared by import_collection_csv and confirm_staged_upload.
MERGE_ARGS = {
    "conflicts": {"type": "string", "enum": ["vault", "app"], "default": "vault",
                  "description": "For cards changed both in the person's app and in the Vault since the last import: keep "
                                 "the Vault's edit (the default) or take the app's value, for all of them"},
    "use_app_value": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 500,
                      "description": "Conflict ids from the preview to take the app's value for, whatever conflicts says"},
    "replace_everything": {"type": "boolean", "default": False,
                           "description": "Replace the whole collection with the file and discard edits made in the Vault "
                                          "(the preview says how many)"},
}
MERGE_QUERY = tuple(MERGE_ARGS)

JSON_TYPES = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,),
              "array": (list,), "object": (dict,), "null": (type(None),)}


def _invalid(schema: dict, value: Any, where: str) -> str | None:
    """Why ``value`` doesn't match ``schema``, or None. Tool schemas are otherwise only
    descriptive, and paths and API bodies are built from these values. Covers the keywords the
    tools use: type, enum, bounds, lengths, pattern, format (date), items, properties, required,
    additionalProperties, anyOf."""
    kind = schema.get("type")
    if kind in JSON_TYPES:
        if (isinstance(value, bool) and kind != "boolean") or not isinstance(value, JSON_TYPES[kind]):
            return f"{where} must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{where} must be one of {schema['enum']}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{where} must be at least {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{where} must be at most {schema['maximum']}"
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            return f"{where} has the wrong length"
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return f"{where} is not in the right form"
        if schema.get("format") == "date" and not _is_date(value):
            return f"{where} must be a date (YYYY-MM-DD)"
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            return f"{where} has the wrong number of items"
        for i, item in enumerate(value):
            if "items" in schema and (why := _invalid(schema["items"], item, f"{where}[{i}]")):
                return why
    if "anyOf" in schema and all(_invalid(option, value, where) for option in schema["anyOf"]):
        return f"{where} doesn't match any allowed form"
    if isinstance(value, dict) and ("properties" in schema or "required" in schema):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if value.get(name) in (None, ""):
                return f"{where}.{name} is required"
        for name, item in value.items():
            if name not in properties:
                if schema.get("additionalProperties", True) is False:
                    return f"{where}.{name} is not allowed"
            elif item is not None and (why := _invalid(properties[name], item, f"{where}.{name}")):
                return why
    return None


def _is_date(value: str) -> bool:
    try:
        return len(value) == 10 and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _base(args: dict) -> str:
    # An explicit share_id always means the shared collection (never "your own" by accident).
    shared = args.get("share_id")
    return f"{V1}/shared/{int(shared)}/collection" if shared is not None else f"{V1}/collection"


ID = {"type": "integer", "minimum": 1, "maximum": MAX_ID}
SHARE = {"share_id": {**ID, "description": "Read a collection someone shared with you (from list_shared_with_me) instead of your own"}}
DECKLIST = {"type": "string", "maxLength": 50_000}  # as the API's TextIn and DeckIn
CONFIRM = {"type": "boolean", "description": "true only after the person agreed to this exact change"}
OWNED_LINES = {"type": "array", "minItems": 1, "maxItems": 50, "items": {
    "type": "object", "required": ["action", "name", "quantity"], "additionalProperties": False, "properties": {
        "action": {"type": "string", "enum": ["add", "remove", "set"], "description": "add or remove copies, or set how many"},
        "name": {"type": "string", "minLength": 1, "maxLength": 300, "description": "The card's name"},
        "quantity": {"type": "integer", "minimum": 0, "maximum": 999},
        "set": {"type": "string", "maxLength": 20, "description": "The printing's set code (with number)"},
        "number": {"type": "string", "maxLength": 30, "description": "The printing's collector number (with set)"},
        "finish": {"type": "string", "enum": ["nonfoil", "foil", "etched"]},
        "printing_unknown": {"type": "boolean", "description": "Only when the person says they do not know (adds only)"}}}}
# null clears them on update_deck (as on the API), so it is advertised as allowed.
SOURCE_URL = {"anyOf": [{"type": "string", "maxLength": 500}, {"type": "null"}],
              "description": "Where the deck came from (an http or https link); null clears it"}
SOURCE_AUTHOR = {"anyOf": [{"type": "string", "maxLength": 200}, {"type": "null"}],
                 "description": "Who made the deck at its source (e.g. the Archidekt author), kept for the credit; "
                                "null clears it"}
DECK_FORMAT = {"anyOf": [{"type": "string", "enum": list(FORMATS)}, {"type": "null"}],
               "description": "The deck's format; null clears it (it is then read from the list)"}
SET_SORTS = ["-value", "value", "-quantity", "quantity", "-unique", "unique", "name", "code", "release", "-release"]
PAGING = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25, "description": "Items per page"},
    "cursor": {"type": "string", "description": "next_cursor from the previous page"},
}

TOOLS = [
    Tool("get_collection_summary", "Totals for the collection: copies, printings, sets, market value, amount paid, "
         "prices date, and breakdowns by condition and printing.", dict(SHARE), path=_base),
    Tool("search_cards", "Find printings in the collection. Each item has name, set, collector number, finish, "
         "condition, language, quantity, price (market/low/mid), value, paid, acquisition dates and Scryfall card data "
         "(type, colours, mana cost, rarity, text, image with artist credit). Paged.",
         {"query": {"type": "string", "description": "Text in the card or set name"},
          "set": {"type": "string", "description": "Set code, e.g. 'mh3'"},
          "name": {"type": "string", "description": "Exact card name (front face for double-faced cards)"},
          "finish": {"type": "string", "enum": ["nonfoil", "foil", "etched"]},
          "condition": {"type": "string", "enum": ["mint", "near_mint", "excellent", "good", "light_played", "played", "poor"]},
          "printing": {"type": "string", "maxLength": 40, "description": "Printing label, e.g. 'Foil', 'Normal', 'Etched'"},
          "sort": {"type": "string", "enum": ["name", "-name", "-value", "value", "-quantity", "set", "-acquired", "acquired"],
                   "default": "name",
                   "description": "'-value' = most valuable first, '-acquired' = most recently bought first, "
                                  "'acquired' = first bought first"},
          **PAGING, **SHARE},
         path=lambda a: _base(a) + "/cards",
         query=("q", "set", "name", "finish", "condition", "printing", "sort", "limit", "cursor")),
    Tool("get_card", "One printing in detail: every copy (condition, language, folder, price paid, date), "
         "Scryfall card data (type, text, image with artist credit) and 90 days of prices.",
         {"card_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$", "description": "The id from search_cards"},
          **SHARE}, ["card_id"], path=lambda a: _base(a) + f"/cards/{quote(a['card_id'], safe='')}"),
    Tool("list_sets", "Market value, copies, printings and colour mix (copies by colour identity) per set. Paged.",
         {"query": {"type": "string", "maxLength": 200, "description": "Text in the set's code or name"},
          "sort": {"type": "string", "enum": SET_SORTS, "default": "-value",
                   "description": "'-value' = most valuable first; 'unique' = distinct printings; "
                                  "'release' = by release date"},
          **PAGING, **SHARE},
         path=lambda a: _base(a) + "/sets", query=("q", "sort", "limit", "cursor")),
    Tool("get_collection_stats", "Highlights: most valuable printings, biggest price gains and losses, and the "
         "cards owned in the most copies (with how many printings of each).",
         {"limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8, "description": "Items per list"},
          **SHARE},
         path=lambda a: _base(a) + "/stats", query=("limit",)),
    Tool("get_collection_breakdowns", "Copies, printings and market value by colour identity (W, U, B, R, G, "
         "multicolour, colourless), main card type, mana value (0-7, 8+), rarity, and colour x type. Printings "
         "without card data yet count as 'unknown'.", dict(SHARE), path=lambda a: _base(a) + "/breakdowns"),
    Tool("get_valuation", "Month by month (by purchase date): copies bought, their market value today, what was "
         "paid, running totals and gain; the month that added the most value; the last 12 months' change.",
         dict(SHARE), path=lambda a: _base(a) + "/valuation"),
    Tool("list_card_names", "The collection rolled up by card name: copies, market value, unit price, printings, "
         "sets, colour, main type, mana value, rarity and an image (credit its artist). Paged; the first page "
         "sorted by -value is the top N.",
         {"sort": {"type": "string", "enum": ["-value", "value", "-quantity", "quantity", "name", "-name"],
                   "default": "-value"},
          "colors": {"type": "string", "pattern": "^[WUBRGMCwubrgmc](,[WUBRGMCwubrgmc])*$",
                     "description": "Comma-separated W,U,B,R,G,M,C; a multicolour card matches any of its colours"},
          "type": {"type": "string", "maxLength": 20, "description": "Main type, e.g. 'Creature'"},
          "min_value": {"type": "number", "minimum": 0, "description": "Only names worth at least this (USD)"},
          **PAGING, **SHARE},
         path=lambda a: _base(a) + "/names", query=("sort", "colors", "type", "min_value", "limit", "cursor")),
    Tool("refresh_prices", "Fetch fresh card data and today's prices from Scryfall for the person's own "
         "printings, up to 300 per call, and recompute today's collection value. Each call returns a cursor and "
         "how many remain; passing the cursor continues until remaining is 0.",
         {"cursor": {"type": "string", "maxLength": 36, "description": "cursor from the previous call"},
          "force": {"type": "boolean", "default": False,
                    "description": "Also refresh printings that already have today's price"}},
         method="POST", path=lambda a: f"{V1}/collection/refresh",
         body=lambda a: {"cursor": a.get("cursor"), "force": bool(a.get("force", False))}, write=True),
    Tool("get_value_history", "The collection's market value (and cost) day by day. Paged.",
         {"since": {"type": "string", "format": "date", "description": "YYYY-MM-DD"}, **PAGING, **SHARE},
         path=lambda a: _base(a) + "/history", query=("since", "limit", "cursor")),
    Tool("get_acquisition_timeline", "How many copies were bought each month.", dict(SHARE),
         path=lambda a: _base(a) + "/timeline"),
    Tool("check_decklist", "Which cards of a decklist the person owns, partly owns or is missing. Accepts "
         "Archidekt, Moxfield, Arena and MTGO text formats.",
         {"text": {**DECKLIST, "description": "The decklist, one card per line, e.g. '1 Sol Ring'"}}, ["text"],
         method="POST", path=lambda a: f"{V1}/decks/coverage", body=lambda a: {"text": a["text"]}),
    Tool("parse_decklist", "Parse a decklist into cards with quantity, set, collector number, finish and section.",
         {"text": DECKLIST}, ["text"], method="POST", path=lambda a: f"{V1}/decks/parse",
         body=lambda a: {"text": a["text"]}),
    Tool("lookup_cards", "Card data (type, text, colours, artist, image links) and current prices for up to 75 "
         "printings, by Scryfall id, set + collector number, or name. Works for any card, owned or not.",
         {"identifiers": {"type": "array", "minItems": 1, "maxItems": 75, "items": {"type": "object", "properties": {
             # the API's CardIdentifier limits
             "id": {"type": "string", "maxLength": 36}, "set": {"type": "string", "maxLength": 20},
             "collector_number": {"type": "string", "maxLength": 30}, "name": {"type": "string", "maxLength": 300}},
             "additionalProperties": False,
             # one of: a Scryfall id, a name (optionally with a set), or a set and collector number
             "anyOf": [{"required": ["id"]}, {"required": ["name"]}, {"required": ["set", "collector_number"]}]}}},
         ["identifiers"],
         method="POST", path=lambda a: f"{V1}/cards/lookup", body=lambda a: {"identifiers": a["identifiers"]}),
    Tool("list_decks", "The person's saved decks at a glance: each deck's name, `overview` (format, commander(s), card count, the "
         "commanders' colour identity) and where it came from (archidekt, moxfield, link, pasted); no card lines (get_deck "
         "has them). Present each deck as its name, format and commander(s), e.g. 'Sliver Swarm: Commander, led by Sliver "
         "Overlord, 100 cards'. Say when the format is read from the list rather than set (`format_from`). Pass `query` "
         "with words from the deck's name ('sliver swarm') to find it: best match first, and when nothing matches `closest` "
         "lists near names. People name their decks; use this before asking for a link or an id.",
         {**PAGING, "query": {"type": "string", "maxLength": 200, "description": "Words from the deck's name"}},
         path=lambda a: f"{V1}/decks?brief=true", query=("limit", "cursor", "q")),  # no card text: get_deck has it
    Tool("get_deck_overlap", "Cards that are in more than one of the person's saved decks, how many copies building "
         "every deck at once needs, how many they own, and how many they are short. Basic lands are left out.",
         path=lambda a: f"{V1}/decks/overlap"),
    Tool("get_deck", "A saved deck: its name, `overview` (format, commander(s), card count, colour identity), a `summary` "
         "of how much of it the person owns (copies needed, owned, missing, cost to finish), the cards not fully owned "
         "(the dearest 40, each with its Scryfall unit price and the `price_date` that price is from), and the decklist. For a "
         "deck from Archidekt, `credit` gives its link, author and `fetched_at` (when the list was last taken from the link). "
         "all_cards adds every card's ownership with the printings owned (about 80 KB for 100 cards).",
         {"deck_id": ID, "all_cards": {"type": "boolean", "default": False,
                                       "description": "Every card's ownership and owned printings (large)"}}, ["deck_id"],
         path=lambda a: f"{V1}/decks/{int(a['deck_id'])}?detail={'cards' if a.get('all_cards') else 'summary'}"),
    Tool("save_deck", "Save a decklist to the person's decks.",
         {"name": {"type": "string"}, "text": DECKLIST, "source_url": SOURCE_URL, "source_author": SOURCE_AUTHOR,
          "format": DECK_FORMAT},
         ["name", "text"], method="POST", path=lambda a: f"{V1}/decks",
         body=lambda a: {"name": a["name"], "text": a["text"], "source_url": a.get("source_url"),
                         "source_author": a.get("source_author"), "format": a.get("format")}, write=True),
    Tool("update_deck", "Replace a saved deck's name and text (and its source link, author and format, if given). To set only "
         "the format, send the deck's current name and text from get_deck with the new format.",
         {"deck_id": ID, "name": {"type": "string"}, "text": DECKLIST, "source_url": SOURCE_URL,
          "source_author": SOURCE_AUTHOR, "format": DECK_FORMAT},
         ["deck_id", "name", "text"], method="PUT", path=lambda a: f"{V1}/decks/{int(a['deck_id'])}",
         body=lambda a: {"name": a["name"], "text": a["text"],
                         **{k: a[k] for k in ("source_url", "source_author", "format") if k in a}}, write=True),
    Tool("import_deck_from_link", "Saves a public Archidekt deck to the person's decks from its link. The server reads the deck, "
         "keeps its sections (commander, main, sideboard, maybeboard), its format and its author's credit. A link already saved "
         "is left unchanged unless update is true. Archidekt links only; the deck remains Archidekt's, with its credit and link.",
         {"url": {"type": "string", "minLength": 8, "maxLength": 500, "description": "An Archidekt deck link"},
          "name": {"type": "string", "maxLength": 200, "description": "Name to save it under (default: its name on Archidekt)"},
          "update": {"type": "boolean", "default": False, "description": "For a deck already saved: compare it with Archidekt's current list"},
          "confirm": {"type": "boolean", "description": "With update: replace the saved list with the previewed one"},
          "fingerprint": {"type": "string", "maxLength": 64, "description": "From the update preview"}},
         ["url"], method="POST", path=lambda a: f"{V1}/decks/import-link",
         body=lambda a: {k: a[k] for k in ("url", "name", "update", "confirm", "fingerprint") if a.get(k) is not None},
         write=True, provenance=("archidekt",)),
    Tool("refresh_deck", "Compares a saved deck with its stored Archidekt link. With confirm false or absent it returns the "
         "changes per section and card (added, removed, counts), the source's name and author, and a fingerprint, and "
         "changes nothing. With confirm true and that fingerprint it replaces the saved list with the source's; a "
         "different list on Archidekt returns an error. Decks from Moxfield or other sites, or with no link, return an "
         "error that says to paste a fresh export instead.",
         {"deck_id": ID, "confirm": CONFIRM, "fingerprint": {"type": "string", "maxLength": 64, "description": "From the preview"}},
         ["deck_id"], method="POST", path=lambda a: f"{V1}/decks/{int(a['deck_id'])}/refresh",
         body=lambda a: {k: a[k] for k in ("confirm", "fingerprint") if a.get(k) is not None},
         write=True, destructive=True, provenance=("archidekt",)),
    Tool("get_archidekt_deck", "A public deck from Archidekt by its id (the number in archidekt.com/decks/<id>). "
         "One deck per request, only the one the person gave you. Check list_decks first: the deck may be saved. "
         "Returns the deck's name, author, format and commander(s) (`overview`), card counts, the list with its "
         "sections and Archidekt's own bracket tag; not Archidekt's per-card shop prices. "
         "The deck is Archidekt's: credit Archidekt and link the deck when you use it. Read-only: nothing can "
         "change Archidekt, so the person applies any changes there themselves.",
         {"deck_id": ID}, ["deck_id"], path=lambda a: f"{V1}/archidekt/decks/{int(a['deck_id'])}"),
    Tool("list_imports", "Past collection imports, newest first, with what changed each time.", dict(PAGING),
         path=lambda a: f"{V1}/imports", query=("limit", "cursor")),
    Tool("import_collection_csv", "Imports a collection file and records what changed: it applies what changed in the "
         "person's app since their last import and keeps edits made in the Vault (for example through update_owned_cards). "
         "Dragon Shield, Moxfield and generic CSV exports are detected automatically. With confirm false or absent it "
         "returns what would change, what comes from the app, which Vault edits are kept and the conflicts (cards changed "
         "on both sides; each keeps the Vault's edit unless answered otherwise), and changes nothing; with confirm true it "
         "imports the file. replace_everything makes the file replace the whole collection instead.",
         {"csv": {"type": "string", "description": "The CSV file's content"},
          "filename": {"type": "string", "default": "agent-import.csv"}, "confirm": CONFIRM, **MERGE_ARGS}, ["csv"],
         method="POST", path=lambda a: f"{V1}/imports" if a.get("confirm") is True else f"{V1}/imports/preview",
         query=MERGE_QUERY, write=True, destructive=True),
    Tool("start_collection_upload", "For a collection file too big to paste: a one-time link (one hour) for the "
         "person to upload the file. Nothing is imported: the file waits until they confirm. Give them the link, then "
         "call get_staged_upload when they say it is uploaded.", method="POST", path=lambda a: f"{V1}/uploads", write=True),
    Tool("get_staged_upload", "A file the person uploaded through start_collection_upload: still waiting, or what "
         "importing it would change (what comes from their app, which Vault edits are kept, the conflicts) and which rows "
         "match no known printing (fix those in the file and upload again).",
         {"upload_id": ID, **MERGE_ARGS}, ["upload_id"], path=lambda a: f"{V1}/uploads/{int(a['upload_id'])}",
         query=MERGE_QUERY),
    Tool("confirm_staged_upload", "Imports a file uploaded through start_collection_upload: applies what changed in the "
         "person's app since their last import and keeps edits made in the Vault. With confirm false or absent it returns "
         "the preview (including the conflicts) and changes nothing; with confirm true it imports it. "
         "replace_everything makes the file replace the whole collection instead.",
         {"upload_id": ID, "confirm": CONFIRM, **MERGE_ARGS}, ["upload_id"],
         method=lambda a: "POST" if a.get("confirm") is True else "GET",
         path=lambda a: f"{V1}/uploads/{int(a['upload_id'])}" + ("/apply" if a.get("confirm") is True else ""),
         query=MERGE_QUERY, write=True, destructive=True),
    Tool("show_owned_printings", "Pictures of the printings of one card the person owns (set, number, finish, copies, "
         "Scryfall image with artist credit), most copies first. Use it when they ask to see which ones they have, or "
         "to help them match a card in their hand. Hosts with MCP Apps show the pictures; otherwise give the list.",
         {"name": {"type": "string", "minLength": 1, "maxLength": 300, "description": "The card's name"}}, ["name"],
         path=lambda a: f"{V1}/collection/printings", query=("name",), ui="printings"),
    Tool("update_owned_cards", "Previews small edits to the cards the person owns (bought, sold, traded, found): add "
         "or remove copies, or set how many are owned. Changes nothing. Returns each card, its printing, copies before "
         "and after, the value change and, when every line is resolved, a confirmation for confirm_owned_cards_update. "
         "A line whose printing is ambiguous returns status choose_printing with the candidate printings (pictures in "
         "the view); an add may be sent with printing_unknown. Limits: 50 lines, 25 copies removed (or 10% of the "
         "collection) per change; larger changes are imports.",
         {"lines": OWNED_LINES}, ["lines"], method="POST", path=lambda a: f"{V1}/collection/changes/preview",
         body=lambda a: {"lines": a["lines"]}, write=True, ui="printings"),
    Tool("confirm_owned_cards_update", "Applies the change previewed by update_owned_cards, given the same lines and "
         "that preview's confirmation. Returns an error when the lines differ from the preview, the confirmation is "
         "older than 15 minutes, or the collection changed since. The change is recorded in the import history under "
         "this app's name; undo_owned_cards_update reverts it.",
         {"lines": OWNED_LINES, "confirmation": {"type": "string", "minLength": 8, "maxLength": 400,
                                                 "description": "From the preview the person agreed to"}},
         ["lines", "confirmation"], method="POST", path=lambda a: f"{V1}/collection/changes/apply",
         body=lambda a: {"lines": a["lines"], "confirmation": a["confirmation"]}, write=True, destructive=True),
    Tool("undo_owned_cards_update", "Reverts the most recent change made through an assistant, if nothing else has "
         "changed the collection since. Without a confirmation it returns what the undo would change, and a "
         "confirmation; with that confirmation it applies the undo.",
         {"confirmation": {"type": "string", "minLength": 8, "maxLength": 400,
                           "description": "From this tool's preview, once the person agreed"}},
         method="POST", path=lambda a: f"{V1}/collection/changes/undo",
         body=lambda a: {"confirmation": a.get("confirmation")}, write=True, destructive=True),
    Tool("council_brief", "The expert council for a deck review, a rules dispute or a synergy question: the panel the "
         "council's rules seat for the format and goal (only on-topic format experts; Commander adds the casual table; "
         "always a rules judge and a devil's advocate), each member's full brief, and the chair's procedure. With a "
         "deck_id and no format, the format is read from the saved deck.",
         {"format": {"type": "string", "maxLength": 40, "description": "commander, limited, pauper, standard, pioneer, two-headed-giant, ..."},
          "goal": {"type": "string", "maxLength": 300, "description": "What the person wants: tune, check, explain, synergies, budget"},
          "deck_id": ID,
          "team_format": {"type": "string", "maxLength": 40, "description": "For Two-Headed Giant: the format the team plays"},
          "budget": {"type": "boolean", "description": "The person has a budget (seats the collection and budget analyst)"}},
         path=lambda a: f"{V1}/council", query=("format", "goal", "deck_id", "team_format", "budget")),
    Tool("expert_brief", "One council expert's brief, to answer a question as that expert (for example the rules judge or "
         "the Commander expert). The ids are listed in council_brief's panel and in the enum here.",
         {"expert": {"type": "string", "enum": sorted(experts.DATA["experts"])}}, ["expert"],
         path=lambda a: f"{V1}/experts/{a['expert']}"),
    Tool("list_export_formats", "Formats the collection can be exported in to move it to another app (Dragon "
         "Shield, Moxfield, Archidekt, generic CSV, text list), each with a download link. The files can be "
         "large; give the person the link rather than reading the whole file.",
         path=lambda a: f"{V1}/collection/exports"),
    Tool("list_shared_with_me", "Collections and decks other people have shared with this person.",
         path=lambda a: f"{V1}/shared"),
    Tool("get_shared_deck", "A deck someone shared, checked against this person's collection.",
         {"share_id": ID}, ["share_id"], path=lambda a: f"{V1}/shared/{int(a['share_id'])}/deck"),
    Tool("get_import", "One collection import: when it ran, the file, and what it added, removed and changed.",
         {"import_id": ID}, ["import_id"], path=lambda a: f"{V1}/imports/{int(a['import_id'])}"),
    Tool("delete_deck", "Deletes one of the person's saved decks. With confirm false or absent it returns the deck "
         "that would be deleted and changes nothing; with confirm true it deletes it.",
         {"deck_id": ID, "confirm": CONFIRM}, ["deck_id"],
         method=lambda a: "DELETE" if a.get("confirm") is True else "GET",
         path=lambda a: f"{V1}/decks/{int(a['deck_id'])}", write=True, destructive=True),
    Tool("list_my_shares", "What this person has shared (their collection or a deck), with whom, and whether the "
         "invite was accepted. Creating a share is done by the person in the Vault, not by an assistant.",
         dict(PAGING), path=lambda a: f"{V1}/shares", query=("limit", "cursor")),
    Tool("accept_share", "Accept an invite link someone sent this person (the token after ?invite= in the link), so "
         "their collection or deck appears under list_shared_with_me.",
         {"invite_token": {"type": "string", "minLength": 8, "maxLength": 200, "description": "The invite token"}}, ["invite_token"],
         method="POST", path=lambda a: f"{V1}/shares/accept", body=lambda a: {"token": a["invite_token"]}, write=True),
    Tool("stop_sharing", "Ends a share: the owner revokes it, the recipient leaves it. With confirm false or absent "
         "it returns the person's shares and changes nothing; with confirm true it ends the given share.",
         {"share_id": ID, "confirm": CONFIRM}, ["share_id"],
         method=lambda a: "DELETE" if a.get("confirm") is True else "GET",
         path=lambda a: f"{V1}/shares/{int(a['share_id'])}" if a.get("confirm") is True else f"{V1}/shares",
         write=True, destructive=True),
]
TOOLS.extend(catalog_tools(Tool, ID, PAGING))
INSTRUCTIONS += "\n" + GROUNDING

# Every tool is classified: either its answer carries Scryfall or Archidekt data (so it gets provenance),
# or it holds only the person's own data (tests/test_agents.py fails for a tool that is in neither group).
SCRYFALL_DATA = {"list_decks",  # the commanders' colour identity is Scryfall's Oracle data
                 "get_collection_summary", "search_cards", "get_card", "list_sets", "get_collection_stats",
                 "get_collection_breakdowns", "get_valuation", "get_value_history", "list_card_names", "refresh_prices",
                 "check_decklist", "lookup_cards", "get_deck", "get_shared_deck",
                 "update_owned_cards", "show_owned_printings"}  # these carry Scryfall's card images
OWN_DATA_ONLY = {"get_acquisition_timeline", "parse_decklist", "save_deck", "update_deck", "list_imports",
                 "import_collection_csv", "list_export_formats", "list_shared_with_me", "get_import", "delete_deck",
                 "list_my_shares", "accept_share", "stop_sharing", "start_collection_upload",
                 "get_staged_upload", "confirm_staged_upload", "get_deck_overlap", "council_brief", "expert_brief",
                 "confirm_owned_cards_update", "undo_owned_cards_update"}
DECK_ANALYSIS = {"deck_stats", "simulate_draws", "deck_legality", "find_upgrades", "validate_deck_changes", "find_combos",
                 "shopping_list"}
for _tool in TOOLS:
    if _tool.name in DECK_ANALYSIS:  # #216: the answer says which deck it is about, before any card
        _tool.description += (" The answer's `deck` block names the deck (its name when saved, format, commander(s), card "
                              "count, colour identity): say which deck this is first.")
for _tool in TOOLS:
    if not _tool.provenance:
        _tool.provenance = ("scryfall",) if _tool.name in SCRYFALL_DATA else ("archidekt",) if _tool.name == "get_archidekt_deck" else ()
BY_NAME = {t.name: t for t in TOOLS}


def _rpc_error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _with_cursor(body: Any) -> Any:
    """Pages get a plain next_cursor, easier for an agent than parsing the next link."""
    if isinstance(body, dict) and isinstance(body.get("_links"), dict) and "next" in body["_links"]:
        cursor = parse_qs(urlsplit(body["_links"]["next"]["href"]).query).get("cursor", [None])[0]
        body = {**body, "next_cursor": cursor}
    return body


def _with_retry_hint(status: int, headers, body: Any, request_id: str) -> Any:
    """What an agent needs to carry on after a failed tool call: for a 429 or a 503 when to come back
    (``retry_after_seconds``, from the answer's Retry-After); for any other 5xx that trying again is reasonable, and the
    request id to quote. Other answers are unchanged."""
    if status != 429 and status < 500:
        return body
    if not isinstance(body, dict):
        body = {"detail": str(body)[:300]}
    try:
        after = math.ceil(float(headers.get("retry-after")))
    except (TypeError, ValueError):
        after = body.get("retry_after_seconds") or 5
    out = {**body, "status": status, "retry_after_seconds": max(1, int(after))}
    detail = str(out.get("detail") or out.get("title") or "The Vault could not complete this request.")
    if status >= 500:
        out["request_id"] = request_id
        if "Try again" not in detail:
            detail += f" Try again in {out['retry_after_seconds']} seconds."
        if request_id and request_id not in detail:
            detail += f" (request {request_id})"
    out["detail"] = detail
    return out


def _marked_as_mcp(app):
    """The app, for the in-process calls a tool makes: they are marked, in the request's own scope
    (which no client can write to), as coming from the MCP server. OAuth access tokens are accepted
    only on such calls and on /api/mcp itself."""
    async def marked(scope, receive, send):
        scope.setdefault("state", {})["via_mcp"] = True
        await app(scope, receive, send)

    return marked


def build_router(optional_user, resource_metadata: str = "") -> APIRouter:
    router = APIRouter()

    async def call_api(request: Request, tool: Tool, args: dict, part: int | None) -> tuple[int, Any]:
        forward = ("authorization", "cookie", "idempotency-key")  # a retried tool call must not create twice
        headers = {k.lower(): v for k, v in request.headers.items() if k.lower() in forward}
        if part is not None and "idempotency-key" in headers:  # a batch: one key per call in it
            headers["idempotency-key"] = f"{headers['idempotency-key']}#{part}"
        kwargs: dict[str, Any] = {"headers": headers}
        params = {"q" if k == "query" else k: v for k, v in args.items() if v is not None}
        kwargs["params"] = {k: v for k, v in params.items() if k in tool.query}
        if tool.name in ("search_cards",) or "cursor" in tool.query:  # paged: a small page by default
            kwargs["params"].setdefault("limit", 25)
        if tool.name == "import_collection_csv":
            kwargs["files"] = {"file": (args.get("filename") or "agent-import.csv", args["csv"].encode(), "text/csv")}
        elif tool.body is not None:
            kwargs["json"] = tool.body(args)
        rid = request.scope.get("state", {}).get("request_id", "")
        headers["x-request-id"] = rid  # the in-process call logs under the same id
        # raise_app_exceptions=False: an exception the app turned into an answer (the 500 with a request id, the 503 for a
        # database that cannot be reached) is that answer here too, with its hint, instead of a bare exception.
        transport = httpx.ASGITransport(app=_marked_as_mcp(request.app), raise_app_exceptions=False)
        started = time.perf_counter()
        failure = None
        try:
            async with httpx.AsyncClient(transport=transport, base_url=str(request.base_url).rstrip("/")) as client:
                method = tool.method(args) if callable(tool.method) else tool.method
                path = tool.path(args)
                if "?" in path:  # a fixed query in the path (list_decks' brief=true): kept next to the caller's params
                    path, fixed = path.split("?", 1)
                    kwargs["params"] = {**dict(parse_qsl(fixed)), **kwargs["params"]}
                res = await client.request(method, path, **kwargs)
        except Exception as exc:  # the API failed: this tool call failed, not the whole MCP request (or its batch)
            failure = exc
        took = round((time.perf_counter() - started) * 1000)
        engine = request.app.state.db.engine
        if failure is not None:
            observability.event(logging.ERROR, "mcp_tool_failed", request_id=rid, tool=tool.name, duration_ms=took,
                                pool=observability.pool_state(engine), **observability.describe(failure))
            status, body = 500, {"type": "about:blank", "title": "Internal server error", "status": 500}
            headers_back: Any = {}
        else:
            status, headers_back = res.status_code, res.headers
            try:
                body = res.json()
            except ValueError:
                body = {"text": res.text}
        body = _with_retry_hint(status, headers_back, body, rid)
        observability.event(logging.WARNING if status >= 500 else logging.INFO, "mcp_tool", request_id=rid, tool=tool.name,
                            status=status, duration_ms=took, pool=observability.pool_state(engine))
        return status, body

    @router.post("/api/mcp", tags=["agents"], summary="MCP server (Streamable HTTP, JSON-RPC 2.0) for AI agents")
    async def mcp(request: Request, user: User | None = Depends(optional_user)):
        if user is None:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message":
                 "Authentication required: send Authorization: Bearer <personal access token> "
                 "(create one in the Vault: Account → Agents & API), or connect with OAuth: the server's "
                 "metadata is linked in the WWW-Authenticate header."}},
                status_code=401, headers={"WWW-Authenticate": 'Bearer realm="the-vault"' + (
                    ', error="invalid_token"' if request.headers.get("authorization") else "") + (
                    f', resource_metadata="{resource_metadata}"' if resource_metadata else "")})
        try:
            message = await request.json()
        except ValueError:
            return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
        if isinstance(message, list):  # JSON-RPC batch (older protocol versions)
            if not message or len(message) > MAX_BATCH:
                why = "an empty batch" if not message else f"a batch holds at most {MAX_BATCH} calls"
                return JSONResponse(_rpc_error(None, -32600, f"Invalid request: {why}"), status_code=400)
            answers = [a for a in [await guarded(request, m, i) for i, m in enumerate(message)] if a is not None]
            return JSONResponse(answers, headers=session_headers(request)) if answers else Response(status_code=202)
        answer = await guarded(request, message)
        return JSONResponse(answer, headers=session_headers(request)) if answer is not None else Response(status_code=202)

    async def guarded(request: Request, msg: Any, part: int | None = None) -> dict | None:
        """``handle``, with an exception it did not expect turned into a JSON-RPC internal error that says when to retry and
        which request to quote (and is logged under that id), not a bare 500 the client reports as a dead server."""
        try:
            return await handle(request, msg, part)
        except Exception as exc:
            rid = observability.log_unhandled(request, exc)
            return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None, "error": {
                "code": -32603, "message": f"Internal error. Try again in a few seconds; if it keeps happening, quote request {rid}.",
                "data": {"retryAfterSeconds": 5, "requestId": rid}}}

    def session_headers(request: Request) -> dict[str, str]:
        issued = getattr(request.state, "new_session", None)
        return {mcp_session.HEADER: issued} if issued else {}

    @router.get("/api/mcp", include_in_schema=False)
    @router.delete("/api/mcp", include_in_schema=False)
    def mcp_no_stream() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    async def handle(request: Request, msg: Any, part: int | None = None) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
        id_, method, params = msg.get("id"), msg["method"], msg.get("params")
        if "id" not in msg:  # a notification (e.g. notifications/initialized): nothing to answer
            return None
        if params is None:
            params = {}
        elif not isinstance(params, dict):
            return _rpc_error(id_, -32602, "Invalid params: params must be an object")
        scopes = request.state.scopes
        if method == "initialize":
            asked = params.get("protocolVersion")
            ui = mcp_session.advertises_ui(params.get("capabilities"))
            info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
            # Which hosts say they can show MCP Apps is what the next tools/list depends on; the log shows it per host
            # (name and version only, no person, no token). Control characters are dropped, the length is bounded.
            who = re.sub(r"[^ -~]", "", f"{info.get('name')}/{info.get('version')}")[:60]
            log.info("MCP initialize from %s: MCP Apps extension %s", who, "advertised" if ui else "not advertised")
            request.state.new_session = mcp_session.issue(request.app.state.settings.session_secret, ui)
            return _result(id_, {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False},
                                 "resources": {"listChanged": False, "subscribe": False}},
                # icons and websiteUrl (MCP 2025-11-25): hosts show the Vault's own gold V instead of a placeholder
                "serverInfo": {"name": "the-vault", "title": "The Vault", "version": "1",
                               "websiteUrl": request.app.state.settings.base_url,
                               "icons": [{"src": f"{request.app.state.settings.base_url}/apple-touch-icon.png",
                                          "mimeType": "image/png", "sizes": ["180x180"]},
                                         {"src": f"{request.app.state.settings.base_url}/favicon.svg",
                                          "mimeType": "image/svg+xml", "sizes": ["any"]}]},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return _result(id_, {})
        if method == "tools/list":
            # A client that connected here and said it cannot show MCP Apps gets no view links; one that did, or one we
            # have no session for (an older connection, a client that ignores session ids), gets them, as before.
            settings = request.app.state.settings
            views = (not settings.mcp_apps_require_capability
                     or mcp_session.read(settings.session_secret, request.headers.get(mcp_session.HEADER)) is not False)
            return _result(id_, {"tools": [t.schema(ui=views) for t in TOOLS if not t.write or "write" in scopes]})
        if method == "resources/list":  # the MCP Apps views (ui:// pages); there is nothing else to read
            return _result(id_, {"resources": mcp_ui.resources()})
        if method == "resources/templates/list":
            return _result(id_, {"resourceTemplates": []})
        if method == "resources/read":
            found = mcp_ui.read(params.get("uri")) if isinstance(params.get("uri"), str) else None
            return _result(id_, found) if found else _rpc_error(id_, -32002, f"Resource not found: {params.get('uri')}")
        if method == "prompts/list":
            return _result(id_, {"prompts": [{k: p[k] for k in ("name", "title", "description", "arguments")} for p in PROMPTS]})
        if method == "prompts/get":
            prompt = next((p for p in PROMPTS if p["name"] == params.get("name")), None)
            if prompt is None:
                return _rpc_error(id_, -32602, f"Unknown prompt: {params.get('name')}")
            given = params.get("arguments") or {}
            if not isinstance(given, dict):
                return _rpc_error(id_, -32602, "Invalid arguments: must be an object")
            missing = [a["name"] for a in prompt["arguments"] if a["required"] and not given.get(a["name"])]
            if missing:
                return _rpc_error(id_, -32602, f"Missing argument(s): {', '.join(missing)}")
            return _result(id_, {"description": prompt["description"], "messages": [
                {"role": "user", "content": {"type": "text", "text": render_prompt(prompt, given)}}]})
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                return _rpc_error(id_, -32602, "Invalid params: name must be the tool's name")
            tool = BY_NAME.get(name)
            args = params.get("arguments")
            if args is None:  # omitted: no arguments
                args = {}
            if tool is None:
                return _rpc_error(id_, -32602, f"Unknown tool: {params.get('name')}")
            if not isinstance(args, dict):
                return _rpc_error(id_, -32602, "Invalid arguments: must be an object")
            why = _invalid(tool.schema()["inputSchema"], args, "arguments")
            if why:
                return _rpc_error(id_, -32602, f"Invalid arguments: {why}")
            status, body = await call_api(request, tool, args, part)
            body = _with_cursor(body)
            kinds = tuple(k for k in tool.provenance if k in ("scryfall", "archidekt"))
            if kinds and status < 400 and isinstance(body, dict) and "provenance" not in body:
                body = {**body, "provenance": provenance_blocks(kinds, body)}  # third-party data is always attributed
            text = json.dumps(body, separators=(",", ":"), default=str)
            result = {"content": [{"type": "text", "text": text}], "isError": status >= 400}
            if isinstance(body, dict):
                result["structuredContent"] = body
            return _result(id_, result)
        return _rpc_error(id_, -32601, f"Method not found: {method}")

    return router
