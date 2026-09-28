"""The Vault as an MCP server, so people can point their own AI agents at their collection.

``POST /api/mcp`` speaks the Model Context Protocol over Streamable HTTP. It is stateless:
no session id and no server-sent events. Each JSON-RPC request gets one JSON answer, which
suits serverless hosting. Authenticate with ``Authorization: Bearer <token>``: a personal
access token from Account → Agents & API (or ``POST /api/v1/me/tokens``).

Every tool is a thin wrapper around a ``/api/v1`` endpoint, called in-process with the
caller's credentials. Tenancy, sharing, scopes, paging and validation are therefore
enforced in one place. With a read-only token, write tools aren't listed, and calling one
fails with the API's 403.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..models import User

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
V1 = "/api/v1"

INSTRUCTIONS = """\
The Vault holds one person's Magic: The Gathering collection (imported from Dragon Shield),
priced daily from Scryfall, plus their saved decks and what others have shared with them.
- Start with get_collection_summary. Use search_cards to find printings; it pages (pass
  next_cursor back as cursor), sorts with sort="-value" (most valuable first), and filters by
  query, set, name, finish and condition.
- check_decklist tells which cards of any pasted decklist the person owns, partly owns, or is missing.
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
    method: str = "GET"
    path: Callable[[dict], str] = lambda a: V1
    query: tuple[str, ...] = ()
    body: Callable[[dict], Any] | None = None
    write: bool = False
    title: str = ""

    def schema(self) -> dict:
        return {
            "name": self.name, "title": self.title or self.name.replace("_", " ").capitalize(),
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.properties, "required": self.required,
                            "additionalProperties": False},
            "annotations": {"readOnlyHint": not self.write, "destructiveHint": False, "openWorldHint": False},
        }


def _base(args: dict) -> str:
    return f"{V1}/shared/{int(args['share_id'])}/collection" if args.get("share_id") else f"{V1}/collection"


SHARE = {"share_id": {"type": "integer", "description": "Read a collection someone shared with you (from list_shared_with_me) instead of your own"}}
PAGING = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25, "description": "Items per page"},
    "cursor": {"type": "string", "description": "next_cursor from the previous page"},
}

TOOLS = [
    Tool("get_collection_summary", "Totals for the collection: copies, printings, sets, market value, amount paid, "
         "prices date, and breakdowns by condition and printing.", dict(SHARE), path=_base),
    Tool("search_cards", "Find printings in the collection. Each item has name, set, collector number, finish, "
         "condition, language, quantity, price (market/low/mid), value, paid and acquisition dates. Paged.",
         {"query": {"type": "string", "description": "Text in the card or set name"},
          "set": {"type": "string", "description": "Set code, e.g. 'mh3'"},
          "name": {"type": "string", "description": "Exact card name (front face for double-faced cards)"},
          "finish": {"type": "string", "enum": ["nonfoil", "foil", "etched"]},
          "condition": {"type": "string", "enum": ["mint", "near_mint", "excellent", "good", "light_played", "played", "poor"]},
          "sort": {"type": "string", "enum": ["name", "-value", "value", "-quantity", "set", "-acquired"], "default": "name",
                   "description": "'-value' = most valuable first, '-acquired' = most recently bought first"},
          **PAGING, **SHARE},
         path=lambda a: _base(a) + "/cards", query=("q", "set", "name", "finish", "condition", "sort", "limit", "cursor")),
    Tool("get_card", "One printing in detail: every copy (condition, language, folder, price paid, date), "
         "Scryfall card data (type, text, image with artist credit) and 90 days of prices.",
         {"card_id": {"type": "string", "description": "The id from search_cards"}, **SHARE}, ["card_id"],
         path=lambda a: _base(a) + f"/cards/{a['card_id']}"),
    Tool("list_sets", "Market value, copies and printings per set. Paged.", {**PAGING, **SHARE},
         path=lambda a: _base(a) + "/sets", query=("limit", "cursor")),
    Tool("get_collection_stats", "Highlights: most valuable printings, biggest price gains and losses, duplicates.",
         dict(SHARE), path=lambda a: _base(a) + "/stats"),
    Tool("get_value_history", "The collection's market value (and cost) day by day. Paged.",
         {"since": {"type": "string", "format": "date", "description": "YYYY-MM-DD"}, **PAGING, **SHARE},
         path=lambda a: _base(a) + "/history", query=("since", "limit", "cursor")),
    Tool("get_acquisition_timeline", "How many copies were bought each month.", dict(SHARE),
         path=lambda a: _base(a) + "/timeline"),
    Tool("check_decklist", "Which cards of a decklist the person owns, partly owns or is missing. Accepts "
         "Archidekt, Moxfield, Arena and MTGO text formats.",
         {"text": {"type": "string", "description": "The decklist, one card per line, e.g. '1 Sol Ring'"}}, ["text"],
         method="POST", path=lambda a: f"{V1}/decks/coverage", body=lambda a: {"text": a["text"]}),
    Tool("parse_decklist", "Parse a decklist into cards with quantity, set, collector number, finish and section.",
         {"text": {"type": "string"}}, ["text"], method="POST", path=lambda a: f"{V1}/decks/parse",
         body=lambda a: {"text": a["text"]}),
    Tool("list_decks", "The person's saved decks.", dict(PAGING), path=lambda a: f"{V1}/decks", query=("limit", "cursor")),
    Tool("get_deck", "A saved deck with its text and coverage against the collection.",
         {"deck_id": {"type": "integer"}}, ["deck_id"], path=lambda a: f"{V1}/decks/{int(a['deck_id'])}"),
    Tool("save_deck", "Save a decklist to the person's decks.",
         {"name": {"type": "string"}, "text": {"type": "string"}, "source_url": {"type": "string"}}, ["name", "text"],
         method="POST", path=lambda a: f"{V1}/decks",
         body=lambda a: {"name": a["name"], "text": a["text"], "source_url": a.get("source_url")}, write=True),
    Tool("update_deck", "Replace a saved deck's name and text.",
         {"deck_id": {"type": "integer"}, "name": {"type": "string"}, "text": {"type": "string"}},
         ["deck_id", "name", "text"], method="PUT", path=lambda a: f"{V1}/decks/{int(a['deck_id'])}",
         body=lambda a: {"name": a["name"], "text": a["text"]}, write=True),
    Tool("get_archidekt_deck", "A public deck from Archidekt by its id (the number in archidekt.com/decks/<id>).",
         {"deck_id": {"type": "integer"}}, ["deck_id"], path=lambda a: f"{V1}/archidekt/decks/{int(a['deck_id'])}"),
    Tool("list_imports", "Past collection imports, newest first, with what changed each time.", dict(PAGING),
         path=lambda a: f"{V1}/imports", query=("limit", "cursor")),
    Tool("import_collection_csv", "Replace the collection with a collection file and record what changed. Dragon "
         "Shield, Moxfield and generic CSV exports are detected automatically.",
         {"csv": {"type": "string", "description": "The CSV file's content"},
          "filename": {"type": "string", "default": "agent-import.csv"}}, ["csv"],
         method="POST", path=lambda a: f"{V1}/imports", write=True),
    Tool("list_export_formats", "Formats the collection can be exported in to move it to another app (Dragon "
         "Shield, Moxfield, Archidekt, generic CSV, text list), each with a download link. The files can be "
         "large; give the person the link rather than reading the whole file.",
         path=lambda a: f"{V1}/collection/exports"),
    Tool("list_shared_with_me", "Collections and decks other people have shared with this person.",
         path=lambda a: f"{V1}/shared"),
    Tool("get_shared_deck", "A deck someone shared, checked against this person's collection.",
         {"share_id": {"type": "integer"}}, ["share_id"], path=lambda a: f"{V1}/shared/{int(a['share_id'])}/deck"),
]
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


def build_router(optional_user) -> APIRouter:
    router = APIRouter()

    async def call_api(request: Request, tool: Tool, args: dict, part: int | None) -> tuple[int, Any]:
        forward = ("authorization", "cookie", "idempotency-key")  # a retried tool call must not create twice
        headers = {k.lower(): v for k, v in request.headers.items() if k.lower() in forward}
        if part is not None and "idempotency-key" in headers:  # a batch: one key per call in it
            headers["idempotency-key"] = f"{headers['idempotency-key']}#{part}"
        kwargs: dict[str, Any] = {"headers": headers}
        params = {"q" if k == "query" else k: v for k, v in args.items() if v is not None}
        kwargs["params"] = {k: v for k, v in params.items() if k in tool.query}
        if tool.name in ("search_cards",) or "limit" in tool.query:
            kwargs["params"].setdefault("limit", 25)
        if tool.name == "import_collection_csv":
            kwargs["files"] = {"file": (args.get("filename") or "agent-import.csv", args["csv"].encode(), "text/csv")}
        elif tool.body is not None:
            kwargs["json"] = tool.body(args)
        transport = httpx.ASGITransport(app=request.app)
        async with httpx.AsyncClient(transport=transport, base_url=str(request.base_url).rstrip("/")) as client:
            res = await client.request(tool.method, tool.path(args), **kwargs)
        try:
            body = res.json()
        except ValueError:
            body = {"text": res.text}
        return res.status_code, body

    @router.post("/api/mcp", tags=["agents"], summary="MCP server (Streamable HTTP, JSON-RPC 2.0) for AI agents")
    async def mcp(request: Request, user: User | None = Depends(optional_user)):
        if user is None:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message":
                 "Authentication required: send Authorization: Bearer <personal access token> "
                 "(create one in the Vault: Account → Agents & API)."}},
                status_code=401, headers={"WWW-Authenticate": 'Bearer realm="the-vault"'})
        try:
            message = await request.json()
        except ValueError:
            return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
        if isinstance(message, list):  # JSON-RPC batch (older protocol versions)
            answers = [a for a in [await handle(request, m, i) for i, m in enumerate(message)] if a is not None]
            return JSONResponse(answers) if answers else Response(status_code=202)
        answer = await handle(request, message)
        return JSONResponse(answer) if answer is not None else Response(status_code=202)

    @router.get("/api/mcp", include_in_schema=False)
    @router.delete("/api/mcp", include_in_schema=False)
    def mcp_no_stream() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    async def handle(request: Request, msg: Any, part: int | None = None) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
        id_, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if "id" not in msg:  # a notification (e.g. notifications/initialized): nothing to answer
            return None
        scopes = request.state.scopes
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _result(id_, {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "the-vault", "title": "The Vault", "version": "1"},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return _result(id_, {})
        if method == "tools/list":
            return _result(id_, {"tools": [t.schema() for t in TOOLS if not t.write or "write" in scopes]})
        if method == "tools/call":
            tool = BY_NAME.get(params.get("name", ""))
            args = params.get("arguments") or {}
            if tool is None:
                return _rpc_error(id_, -32602, f"Unknown tool: {params.get('name')}")
            missing = [r for r in tool.required if args.get(r) in (None, "")]
            unknown = [k for k in args if k not in tool.properties]
            if missing or unknown or not isinstance(args, dict):
                return _rpc_error(id_, -32602, f"Invalid arguments: missing {missing}, unknown {unknown}")
            status, body = await call_api(request, tool, args, part)
            body = _with_cursor(body)
            text = json.dumps(body, separators=(",", ":"), default=str)
            result = {"content": [{"type": "text", "text": text}], "isError": status >= 400}
            if isinstance(body, dict):
                result["structuredContent"] = body
            return _result(id_, result)
        return _rpc_error(id_, -32601, f"Method not found: {method}")

    return router
