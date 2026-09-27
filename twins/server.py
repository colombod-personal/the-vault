"""Serve the twin universe over HTTP, for the web app in a browser, Playwright, or the iOS
simulator.

- ``/h/<real host>/<path>`` is the real service's ``https://<real host>/<path>``. The vault
  (with ``VAULT_TWINS_URL``) sends its server-side calls here and rewrites the sign-in redirects
  it gives browsers, so they land here as well. Redirects between twins stay inside ``/h/``.
- ``/_twins`` is the control panel for people. ``/_twins/api/...`` is the same for scripts
  and agents: state, reset, outages, scripted failures, accounts, key rotation, native ID
  tokens for the iOS simulator, cards, prices and decks.
"""

from __future__ import annotations

import html
import json

import httpx
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .identity import IdentityTwin
from .universe import Universe

HOP_HEADERS = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "*",
        "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS"}


def create_server(universe: Universe, public_url: str) -> Starlette:
    public_url = public_url.rstrip("/")
    universe.set_public_url(public_url)

    def to_public(url: str) -> str:
        u = httpx.URL(url)
        if u.host in universe.by_host:
            return f"{public_url}/h/{u.host}{u.raw_path.decode()}"
        return url

    async def proxy(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=CORS)
        host, path = request.path_params["host"], request.path_params["path"]
        url = f"https://{host}/{path}" + (f"?{request.url.query}" if request.url.query else "")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        outgoing = httpx.Request(request.method, url, headers=headers, content=await request.body())
        try:
            res = await run_in_threadpool(universe.handle, outgoing)
        except httpx.ConnectError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        out = {k: v for k, v in res.headers.items() if k.lower() not in HOP_HEADERS | {"content-encoding"}}
        if "location" in res.headers:
            out["location"] = to_public(res.headers["location"])
        content = res.content
        if request.headers.get("x-twin-browser") and "json" in res.headers.get("content-type", ""):
            # A browser will load the URLs in this body (card images, set icons): point them here.
            # Server-side callers don't send the header and get the body exactly as the twin wrote it.
            text = content.decode()
            for twin_host in universe.by_host:
                text = text.replace(f"https://{twin_host}/", f"{public_url}/h/{twin_host}/")
            content = text.encode()
        return Response(content, status_code=res.status_code, headers=out)

    def twin_or_404(name: str):
        twin = universe.twins.get(name)
        if twin is None:
            raise KeyError(name)
        return twin

    async def api(request: Request) -> Response:
        action = request.path_params["action"]
        name = request.path_params.get("twin")
        body = {}
        if request.method == "POST":
            raw = await request.body()
            body = json.loads(raw) if raw else {}
        try:
            result = _control(universe, twin_or_404(name) if name else None, action, body)
        except KeyError as exc:
            return JSONResponse({"error": f"unknown twin or field: {exc}"}, status_code=404)
        except (TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result, headers=CORS)

    async def panel(request: Request) -> Response:
        return HTMLResponse(_panel(universe))

    return Starlette(routes=[
        Route("/h/{host}/{path:path}", proxy, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"]),
        Route("/_twins", panel),
        Route("/_twins/api/{action}", api, methods=["GET", "POST"]),
        Route("/_twins/api/{twin}/{action}", api, methods=["GET", "POST"]),
        Route("/", lambda r: Response(status_code=307, headers={"location": "/_twins"})),
    ])


def _control(universe: Universe, twin, action: str, body: dict):
    if twin is None:
        if action == "state":
            return universe.state()
        if action == "reset":
            universe.reset()
            return {"ok": True}
        raise KeyError(action)
    if action == "state":
        return twin.state()
    if action == "reset":
        twin.reset()
    elif action == "outage":
        twin.outage = bool(body.get("on", True))
    elif action == "latency":
        twin.latency = float(body.get("seconds", 0))
    elif action == "fail":
        twin.fail_next(body["path"], int(body.get("status", 500)), body.get("body"), int(body.get("times", 1)))
    elif isinstance(twin, IdentityTwin) and action == "accounts":
        account = twin.add_account(**body)
        return account.__dict__
    elif isinstance(twin, IdentityTwin) and action == "rotate-keys":
        return {"kid": twin.rotate_keys(bool(body.get("keep_old", True))).kid}
    elif isinstance(twin, IdentityTwin) and action == "native-token":
        # What Sign in with Apple / Google Sign-In would hand the iOS app (for the simulator).
        claims = {k: v for k, v in body.items() if k not in ("aud", "sub", "nonce")}
        return {"id_token": twin.native_id_token(body["aud"], body["sub"], body.get("nonce"), **claims)}
    elif twin.name == "scryfall" and action == "cards":
        card = twin.add_card(body.pop("name"), body.pop("set"), body.pop("collector_number"), **body)
        return {"id": card["id"]}
    elif twin.name == "scryfall" and action == "prices":
        twin.set_price(body.pop("id"), **body)
    elif twin.name == "scryfall" and action == "rate-limits":
        twin.enforce_rate_limits = bool(body.get("on", True))
    elif twin.name == "archidekt" and action == "decks":
        deck = twin.add_deck(body["name"], body["owner"], [tuple(c) for c in body.get("cards", [])],
                             private=bool(body.get("private")))
        return {"id": deck["id"]}
    else:
        raise KeyError(action)
    return {"ok": True}


def _panel(universe: Universe) -> str:
    rows = []
    for name, twin in universe.twins.items():
        st = twin.state()
        extra = ""
        if isinstance(twin, IdentityTwin):
            accounts = ", ".join(html.escape(a.email or a.sub) for a in twin.accounts.values()) or "none yet"
            extra = f"<div class=small>apps: {html.escape(', '.join(twin.apps) or 'none')} · accounts: {accounts}</div>"
        elif name == "scryfall":
            extra = f"<div class=small>{st['cards']} cards · rate limits {'on' if st['enforce_rate_limits'] else 'off'}</div>"
        elif name == "archidekt":
            extra = f"<div class=small>{len(twin.decks)} decks: " + ", ".join(
                f"#{d['id']} {html.escape(d['name'])}" for d in twin.decks.values()) + "</div>"
        rows.append(f"""<tr><td><b>{name}</b><div class=small>{', '.join(twin.hosts)}</div>{extra}</td>
<td>{st['calls']} calls</td><td>{'<b class=bad>OUTAGE</b>' if twin.outage else 'up'}</td>
<td><button onclick="post('{name}/outage',{{on:{'false' if twin.outage else 'true'}}})">{'end outage' if twin.outage else 'start outage'}</button>
<button onclick="post('{name}/fail',{{path:'*',status:500}})">fail next call</button>
<button onclick="post('{name}/reset',{{}})">reset</button></td></tr>""")
    escapes = "".join(f"<li>{html.escape(e)}</li>" for e in universe.escapes[-10:]) or "<li>none</li>"
    return f"""<!doctype html><meta charset=utf-8><title>Twin universe</title>
<style>body{{font:14px system-ui;margin:24px}} td{{padding:8px;border-top:1px solid #ddd;vertical-align:top}}
.small{{color:#666;font-size:12px}} .bad{{color:#b00}}</style>
<h1>The Vault · digital twin universe</h1>
<p>Behavioural clones of the services the Vault uses. Nothing here reaches the real services.
JSON API: <code>/_twins/api/state</code>.</p>
<table>{''.join(rows)}</table><h3>Requests to hosts outside the universe</h3><ul>{escapes}</ul>
<script>async function post(p,b){{await fetch('/_twins/api/'+p,{{method:'POST',body:JSON.stringify(b)}});location.reload()}}</script>"""
