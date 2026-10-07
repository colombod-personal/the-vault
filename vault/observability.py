"""What the server tells its log, so that the next "server isn't responding" has an explanation (#169).

The first one could not be explained: there was nothing in the log but a traceback per failure. Now every line is one JSON
object on the ``vault.access`` logger with an ``event`` and a ``request_id``, for what a person debugging production needs:

* ``mcp_tool``: every MCP tool call: the tool, the HTTP status of the call it made, how long it took, the connection pool.
* ``slow_request`` (a warning): a request that took ``SLOW_MS`` or more, with its route and the pool.
* ``server_error`` (a warning): an answer of 500 or more that was not an exception (a 503 for a busy pool).
* ``unhandled_exception`` (an error): the exception's class, its cause's class, the SQLSTATE when it is a database error, and
  where in the Vault's code it came from (file, function, line), the route, the duration and the pool.
* ``client_disconnected`` (a warning): the caller gave up before it was answered, and how long it had waited.
* ``db_connect_retry`` and ``migration_deferred`` (warnings): a database connection that was refused and tried again.

``request_id`` is Vercel's ``x-vercel-id`` when there is one (so a line is found from Vercel's own request log, and the
other way round) or a random one; it is also the ``X-Request-Id`` response header and appears in the body of every 5xx
answer, so what a client captured names the line to look for. The route is the pattern (``/api/v1/decks/{deck_id}``), never
the path; no query string, no body, no token, no person, no address and no exception message is logged (a message from the
database can carry the values of a query).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import traceback
import uuid
from pathlib import Path

access_log = logging.getLogger("vault.access")
SLOW_MS = 3000
ROOT = Path(__file__).resolve().parent.parent
SAFE_ID = re.compile(r"[^A-Za-z0-9:_.\-]")


def configure() -> None:
    """Let the access lines reach the log at INFO. Python's default root logger drops them; Vercel shows whatever is written to
    stdout. Done once, and only when nobody configured logging already."""
    access_log.setLevel(logging.INFO)
    if not logging.getLogger().handlers and not access_log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        access_log.addHandler(handler)


def pool_state(engine) -> dict:
    """How busy this instance's connection pool is: ``checked_out`` of ``size`` + ``max_overflow`` (``overflow`` counts
    from minus the size up to the overflow in use)."""
    pool = engine.pool
    try:
        return {"size": pool.size(), "checked_out": pool.checkedout(), "overflow": pool.overflow(),
                "max_overflow": getattr(pool, "_max_overflow", 0)}
    except AttributeError:  # not a queue pool
        return {}


def event(level: int, name: str, **fields) -> None:
    access_log.log(level, json.dumps({"event": name, "ts": round(time.time(), 3), **fields}, separators=(",", ":"), default=str))


def request_id_of(headers: dict[bytes, bytes], trust_forwarded: bool = False) -> str:
    """Vercel's request id, or the one an MCP tool call passes to its in-process call; else a new one."""
    for name, trusted in ((b"x-vercel-id", True), (b"x-request-id", trust_forwarded)):
        value = headers.get(name, b"").decode("latin-1")
        if trusted and value:
            return SAFE_ID.sub("", value)[:80] or uuid.uuid4().hex[:16]
    return uuid.uuid4().hex[:16]


def describe(exc: BaseException) -> dict:
    """What an exception is, without what it says: its class, its cause's class, the database's SQLSTATE and the innermost
    frame of the Vault's own code."""
    original = getattr(exc, "orig", None)  # SQLAlchemy wraps the driver's error
    cause = original or exc.__cause__ or exc.__context__
    where = None
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        path = Path(frame.filename)
        if ROOT in path.parents and "site-packages" not in path.parts:
            where = f"{path.relative_to(ROOT).as_posix()}:{frame.name}:{frame.lineno}"
            break
    return {"error_class": type(exc).__name__, "cause_class": type(cause).__name__ if cause is not None else None,
            "sqlstate": getattr(original or exc, "sqlstate", None), "where": where}


def route_of(scope) -> str:
    route = scope.get("route")
    return getattr(route, "path", None) or "(no route)"


def state_of(scope) -> dict:
    return scope.setdefault("state", {})


def log_unhandled(request, exc: BaseException) -> str:
    """Log an exception nothing handled and return the request id it is logged under."""
    state = state_of(request.scope)
    rid = state.setdefault("request_id", uuid.uuid4().hex[:16])
    started = state.get("started")
    engine = getattr(getattr(request.app.state, "db", None), "engine", None)
    event(logging.ERROR, "unhandled_exception", request_id=rid, route=route_of(request.scope), method=request.method,
          duration_ms=round((time.perf_counter() - started) * 1000) if started else None,
          pool=pool_state(engine) if engine is not None else {}, **describe(exc))
    return rid


def log_client_gone(request) -> None:
    """The caller closed the connection before the request was answered: how long it had waited is what tells a slow answer
    from one that was never going to come ("server isn't responding" is what an agent says when it gives up)."""
    state = state_of(request.scope)
    started = state.get("started")
    engine = getattr(getattr(request.app.state, "db", None), "engine", None)
    event(logging.WARNING, "client_disconnected", request_id=state.get("request_id"), route=route_of(request.scope),
          method=request.method, waited_ms=round((time.perf_counter() - started) * 1000) if started else None,
          pool=pool_state(engine) if engine is not None else {})


class Observe:
    """Pure ASGI middleware: gives every request an id (``X-Request-Id`` on the answer) and logs the ones worth reading."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        state = state_of(scope)
        headers = dict(scope["headers"])
        rid = state["request_id"] = request_id_of(headers, trust_forwarded=bool(state.get("via_mcp")))
        started = state["started"] = time.perf_counter()
        status = []

        async def sending(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])
                message["headers"] = [*(h for h in message.get("headers", []) if h[0].lower() != b"x-request-id"),
                                      (b"x-request-id", rid.encode())]
            await send(message)

        await self.app(scope, receive, sending)
        took = round((time.perf_counter() - started) * 1000)
        code = status[0] if status else 0
        if code < 500 and took < SLOW_MS:
            return
        engine = getattr(getattr(getattr(scope.get("app"), "state", None), "db", None), "engine", None)
        fields = {"request_id": rid, "route": route_of(scope), "method": scope["method"], "status": code, "duration_ms": took,
                  "pool": pool_state(engine) if engine is not None else {}}
        event(logging.WARNING, "server_error" if code >= 500 else "slow_request", **fields)
