"""Building blocks shared by every twin: routing, scenario knobs, request log."""

from __future__ import annotations

import fnmatch
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs

import httpx


@dataclass
class Fault:
    """A scripted failure: the next ``times`` requests matching ``path`` (a glob) answer
    ``status`` with ``body``, before the twin looks at them."""

    path: str
    status: int
    body: Any = None
    times: int = 1
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Call:
    method: str
    host: str
    path: str
    query: dict[str, str]
    form: dict[str, str]
    headers: dict[str, str]
    status: int = 0


class Request:
    """What a twin handler sees: the httpx request plus parsed query, form and JSON."""

    def __init__(self, req: httpx.Request, params: dict[str, str]):
        self.raw = req
        self.method = req.method
        self.host = req.url.host
        self.path = req.url.path
        self.params = params
        self.query = {k: v[-1] for k, v in parse_qs(req.url.query.decode(), keep_blank_values=True).items()}
        self.headers = req.headers
        body = req.content
        ctype = req.headers.get("content-type", "")
        self.form = ({k: v[-1] for k, v in parse_qs(body.decode(), keep_blank_values=True).items()}
                     if "application/x-www-form-urlencoded" in ctype else {})
        self._body = body
        self.ctype = ctype

    def json(self) -> Any:
        return json.loads(self._body or b"null")

    def value(self, name: str) -> str | None:
        """A parameter from the form or the query string (OAuth endpoints accept both)."""
        return self.form.get(name, self.query.get(name))


Handler = Callable[[Request], httpx.Response]


def json_response(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(),
                          headers={"content-type": "application/json; charset=utf-8", **(headers or {})})


def html_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=body.encode(), headers={"content-type": "text/html; charset=utf-8"})


def redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


class Twin:
    """A behavioural clone of one third-party service, answering on its real host names.

    Subclasses register routes with :meth:`route`. Every twin supports the same scenario knobs:
    :meth:`fail_next` (scripted errors), :attr:`outage` (everything answers 503) and
    :attr:`latency` (seconds added to every answer), and records every call in :attr:`calls`.
    """

    name = "twin"
    hosts: tuple[str, ...] = ()

    def __init__(self):
        self._routes: list[tuple[str, re.Pattern, str, Handler]] = []
        self.faults: list[Fault] = []
        self.outage = False
        self.latency = 0.0
        self.calls: list[Call] = []
        self.public_url: str | None = None  # set when served over HTTP (see twins.server)

    # -- routing -------------------------------------------------------------------------
    def route(self, method: str, host: str, pattern: str, handler: Handler) -> None:
        """``pattern`` is a path with ``{name}`` placeholders (``{name:path}`` spans slashes)."""
        regex = re.sub(r"\{(\w+):path\}", r"(?P<\1>.+)", pattern)
        regex = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", regex)
        self._routes.append((method, re.compile(f"^{regex}$"), host, handler))

    def handle(self, req: httpx.Request) -> httpx.Response:
        call = Call(req.method, req.url.host, req.url.path, dict(req.url.params), {}, dict(req.headers))
        self.calls.append(call)
        response = self._dispatch(req, call)
        call.status = response.status_code
        if self.latency:
            time.sleep(self.latency)
        return response

    def _dispatch(self, req: httpx.Request, call: Call) -> httpx.Response:
        if self.outage:
            return self.error(503, "unavailable", "The service is down (twin outage scenario)")
        for fault in self.faults:
            if fault.times > 0 and fnmatch.fnmatch(req.url.path, fault.path):
                fault.times -= 1
                body = fault.body if fault.body is not None else {"error": "twin_fault", "status": fault.status}
                return json_response(fault.status, body, fault.headers)
        self.faults = [f for f in self.faults if f.times > 0]
        methods_seen = False
        for method, regex, host, handler in self._routes:
            if host != req.url.host:
                continue
            m = regex.match(req.url.path)
            if not m:
                continue
            methods_seen = True
            if method != req.method and not (method == "GET" and req.method == "HEAD"):
                continue
            request = Request(req, m.groupdict())
            call.form = request.form
            return handler(request)
        if methods_seen:
            return self.error(405, "method_not_allowed", f"{req.method} is not allowed here")
        return self.error(404, "not_found", f"No such endpoint: {req.url.host}{req.url.path}")

    def error(self, status: int, code: str, details: str) -> httpx.Response:
        """The service's own error format. Subclasses override to match the real one."""
        return json_response(status, {"error": code, "error_description": details})

    # -- scenario knobs ------------------------------------------------------------------
    def fail_next(self, path: str, status: int = 500, body: Any = None, times: int = 1, **headers: str) -> Fault:
        fault = Fault(path, status, body, times, {k.replace("_", "-"): v for k, v in headers.items()})
        self.faults.append(fault)
        return fault

    def reset(self) -> None:
        self.faults.clear()
        self.calls.clear()
        self.outage = False
        self.latency = 0.0

    def browser_url(self, url: str) -> str:
        """A URL on this twin as a browser should open it: unchanged in-process, or on the
        twin server when served over HTTP."""
        if not self.public_url:
            return url
        u = httpx.URL(url)
        return f"{self.public_url}/h/{u.host}{u.raw_path.decode()}"

    def state(self) -> dict:
        """What the control panel shows."""
        return {"name": self.name, "hosts": list(self.hosts), "outage": self.outage, "latency": self.latency,
                "faults": [f.__dict__ for f in self.faults], "calls": len(self.calls)}
