"""Hypermedia (HAL-style) helpers: links, cursor pagination, ETags, problem details.

Response conventions for ``/api/v1``:

* Every resource has ``_links`` with at least ``self``; related resources are linked,
  so a client can navigate from ``GET /api/v1`` without hard-coding URLs.
* Lists are ``{"items": [...], "count": n, "total": N, "_links": {"self", "next"?}}``.
  Follow ``next`` until it's absent. Cursors are opaque; don't build them yourself.
* Responses carry an ``ETag``; send it back as ``If-None-Match`` to get ``304``.
* Errors are ``application/problem+json`` (RFC 9457): ``type``, ``title``, ``status``, ``detail``.
"""

from __future__ import annotations

import base64
import bisect
import hashlib
import json
from typing import Any, Callable, Sequence
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def link(href: str, **extra) -> dict:
    return {"href": href, **extra}


def url(request: Request, path: str, **params) -> str:
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return path + ("?" + query if query else "")


def encode_cursor(key: Sequence[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(key), separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> list:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid cursor") from None


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        raise HTTPException(400, "limit must be at least 1")
    return min(limit, MAX_LIMIT)


def paginate(items: list, key: Callable[[Any], tuple], ident: Callable[[Any], Any], *,
             cursor: str | None, limit: int | None) -> tuple[list, str | None]:
    """Keyset pagination over an in-memory list: stable under inserts/deletes elsewhere."""
    limit = clamp_limit(limit)
    keyed = sorted(((list(key(i)) + [ident(i)], i) for i in items), key=lambda p: p[0])
    keys = [k for k, _ in keyed]
    start = 0
    if cursor:
        after = decode_cursor(cursor)
        try:  # a cursor this endpoint didn't make (wrong shape or types) can't be compared
            if not isinstance(after, list):
                raise TypeError
            start = bisect.bisect_right(keys, after)
        except TypeError:
            raise HTTPException(400, "Invalid cursor") from None
    page = keyed[start:start + limit]
    more = start + limit < len(keyed)
    return [i for _, i in page], (encode_cursor(page[-1][0]) if more and page else None)


def page_body(request: Request, items: list, next_cursor: str | None, total: int, **params) -> dict:
    path = request.url.path
    links = {"self": link(url(request, path, **params, cursor=request.query_params.get("cursor")))}
    if next_cursor:
        links["next"] = link(url(request, path, **params, cursor=next_cursor))
    links["first"] = link(url(request, path, **params))
    return {"items": items, "count": len(items), "total": total, "_links": links}


def etag_response(request: Request, version: str, body: dict | Callable[[], dict]) -> Response:
    """Return 304 when the client's cached copy is current; otherwise the JSON with an ETag.

    ``body`` may be a callable so an unchanged resource is never built.
    """
    tag = '"' + hashlib.sha1(f"{version}|{request.url.path}?{request.url.query}".encode()).hexdigest()[:20] + '"'
    headers = {"ETag": tag, "Cache-Control": "private, no-cache", "Vary": "Authorization, Cookie"}
    if tag in [t.strip() for t in request.headers.get("if-none-match", "").split(",")]:
        return Response(status_code=304, headers=headers)
    return JSONResponse(body() if callable(body) else body, headers=headers)


def problem(status: int, detail: str, title: str | None = None, **extra) -> JSONResponse:
    titles = {400: "Bad request", 401: "Sign-in required", 403: "Forbidden", 404: "Not found",
              409: "Conflict", 410: "Gone", 413: "Too large", 422: "Invalid request", 503: "Unavailable"}
    return JSONResponse(
        {"type": "about:blank", "title": title or titles.get(status, "Error"), "status": status,
         "detail": detail, **extra},
        status_code=status, media_type="application/problem+json",
    )
