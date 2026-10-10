"""OAuth 2.1 authorization server endpoints for MCP clients (``docs/mcp-oauth-threat-model.md``).

* ``/.well-known/oauth-protected-resource[/api/mcp]``  RFC 9728 metadata of the MCP server
* ``/.well-known/oauth-authorization-server``           RFC 8414 metadata of this server
* ``/oauth/authorize``   GET: check the request, sign in, show the consent screen; POST: the answer
* ``/oauth/token``       authorization_code and refresh_token grants
* ``/oauth/register``    RFC 7591 dynamic client registration (constrained fallback)
* ``/oauth/revoke``      RFC 7009

Everything is validated before anything is shown or redirected: a bad ``client_id`` or
``redirect_uri`` gets an error page (never a redirect, so no open redirect), any other problem is
sent back to the app's own redirect URI as ``error`` with its ``state``.
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams

from . import client_auth, recent_signin
from . import oauth_clients as clients
from . import oauth_server as server
from .auth import PKCE_CHALLENGE as PKCE_CHALLENGE_RE, SessionEnded, require_live_session, session_user
from .config import Settings
from .models import OAuthClient, User
from .ratelimit import client_ip, limited

log = logging.getLogger(__name__)

PARAMS = ("response_type", "client_id", "redirect_uri", "state", "scope", "code_challenge",
          "code_challenge_method", "resource")
MAX_QUERY = 2000  # the pending request lives in the session cookie
MAX_STATE = 500
REGISTER_MAX_BYTES = 8192
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization", "Access-Control-Max-Age": "3600"}
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

SCOPE_TEXT = {
    "read": "Read your collection, decks, value history and what others shared with you",
    "write": "Make changes: import collections, save and edit decks, change which cards you own, refresh prices, share",
}
NEVER = ("Delete your account", "Export all your data", "Create or revoke access tokens",
         "Manage your sign-in methods or other connected apps")


class PageError(Exception):
    """A problem with the client or redirect URI: shown to the person, never redirected."""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.text, self.status = text, status


class RedirectError(Exception):
    """A problem the app should hear about, at the redirect URI it declared (so a person is asked to go there, not sent)."""

    def __init__(self, redirect_uri: str, state: str | None, code: str, description: str, client: OAuthClient | None = None):
        super().__init__(description)
        self.redirect_uri, self.state, self.code, self.description = redirect_uri, state, code, description
        self.client = client


@dataclass
class AuthRequest:
    client: OAuthClient
    redirect_uri: str
    state: str | None
    code_challenge: str
    scopes: list[str]
    resource: str


def with_query(uri: str, params: dict) -> str:
    parts = urlsplit(uri)
    query = "&".join(q for q in (parts.query, urlencode({k: v for k, v in params.items() if v is not None})) if q)
    return parts._replace(query=query).geturl()


def parse_authorize(query: QueryParams, db: Session, fetcher: clients.ClientFetcher, settings: Settings,
                    caller: str = "") -> AuthRequest:
    """Validate an authorization request, in the order that decides what may be redirected."""
    if any(len(query.getlist(name)) > 1 for name in PARAMS):
        raise PageError("This request repeats a parameter, so it can't be trusted.")
    get = lambda name: query.get(name) or None  # noqa: E731
    try:
        limits = clients.Limits(settings.oauth_client_cap, settings.oauth_cimd_cap, settings.oauth_fetch_limit,
                                settings.oauth_fetch_ip_limit)
        client = clients.resolve_client(db, fetcher, get("client_id") or "", limits, caller)
    except clients.ClientError as exc:
        raise PageError(exc.description, 503 if exc.code == "temporarily_unavailable" else 400) from None
    redirect_uri = get("redirect_uri")
    target = clients.match_redirect(client.redirect_uris, redirect_uri) if redirect_uri else None
    if target is None:
        raise PageError("The app's redirect address is not one it registered, so the Vault won't send you there.")
    redirect_uri = target  # what the browser is sent to is rebuilt from what was registered, never the raw input
    state = get("state")
    fail = lambda code, text: RedirectError(redirect_uri, state, code, text, client)  # noqa: E731
    if state is not None and len(state) > MAX_STATE:
        raise RedirectError(redirect_uri, None, "invalid_request", "state is too long", client)  # not echoed back: it is too long
    if get("response_type") != "code":
        raise fail("unsupported_response_type", "response_type must be code")
    challenge = get("code_challenge")
    if not challenge or not PKCE_CHALLENGE_RE.fullmatch(challenge):
        raise fail("invalid_request", "A PKCE code_challenge is required")
    if get("code_challenge_method") != "S256":
        raise fail("invalid_request", "code_challenge_method must be S256")
    if get("resource") != server.resource_uri(settings.base_url):
        raise fail("invalid_target", "resource must be the Vault's MCP server: " + server.resource_uri(settings.base_url))
    try:
        scopes = server.parse_scopes(get("scope"))
    except server.OAuthError as exc:
        raise fail(exc.code, exc.description) from None
    return AuthRequest(client, redirect_uri, state, challenge, scopes, get("resource"))


# -- pages -----------------------------------------------------------------------------------

STYLE = """body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem;color:#1b1b1b;background:#fff}
h1{font-size:1.4rem}code{background:#0001;padding:.1rem .3rem;border-radius:.25rem}
fieldset{border:1px solid #8886;border-radius:.5rem;margin:1rem 0}label{display:block;margin:.4rem 0}
button,a.button{font:inherit;padding:.6rem 1.2rem;margin:.25rem .5rem .25rem 0;border-radius:.5rem;border:1px solid #555;background:#fff;color:inherit;cursor:pointer;display:inline-block;text-decoration:none}
button[value=allow]{background:#1b1b1b;color:#fff}.warn{border-left:4px solid #b45309;padding:.1rem .8rem;background:#b4530918}
.small{font-size:.9rem;opacity:.8}ul.never{margin:.3rem 0}
@media (prefers-color-scheme:dark){body{background:#121212;color:#eee}button,a.button{background:#222;border-color:#999}button[value=allow]{background:#eee;color:#121212}}"""

PASSKEY_SCRIPT = """
const fromB64u=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')+'==='.slice((s.length+3)%4)),c=>c.charCodeAt(0)).buffer;
const toB64u=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const note=t=>{document.getElementById('note').textContent=t};
async function post(u,j){const r=await fetch(u,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(j)});if(!r.ok)throw new Error((await r.json().catch(()=>({}))).detail||r.status);return r.json()}
async function passkey(){try{
 const o=await post('/api/auth/passkey/login/options',{});
 const pk=PublicKeyCredential.parseRequestOptionsFromJSON?PublicKeyCredential.parseRequestOptionsFromJSON(o):{...o,challenge:fromB64u(o.challenge),allowCredentials:(o.allowCredentials||[]).map(c=>({...c,id:fromB64u(c.id)}))};
 const c=await navigator.credentials.get({publicKey:pk});
 const r=c.response;const j=typeof c.toJSON==='function'?c.toJSON():{id:c.id,rawId:toB64u(c.rawId),type:c.type,clientExtensionResults:c.getClientExtensionResults(),response:{clientDataJSON:toB64u(r.clientDataJSON),authenticatorData:toB64u(r.authenticatorData),signature:toB64u(r.signature),userHandle:r.userHandle?toB64u(r.userHandle):null}};
 await post('/api/auth/passkey/login/verify',{credential:j});location.reload()}catch(e){note('Could not sign in with a passkey: '+e.message)}}
async function dev(){await post('/api/auth/dev-login',{});location.reload()}
async function reviewer(){try{await post('/api/auth/reviewer-login',{passphrase:document.getElementById('rp').value});location.reload()}catch(e){note('Could not sign in: '+e.message)}}
document.getElementById('passkey')?.addEventListener('click',passkey);
document.getElementById('dev')?.addEventListener('click',dev);
document.getElementById('reviewer')?.addEventListener('click',reviewer);
"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def page(title: str, body: str, *, status: int = 200, script_nonce: str | None = None) -> HTMLResponse:
    """A server-rendered page that can't be framed (clickjacking) and loads nothing from elsewhere."""
    script = f" script-src 'nonce-{script_nonce}'; connect-src 'self';" if script_nonce else ""
    script_tag = f'<script nonce="{script_nonce}">{PASSKEY_SCRIPT}</script>' if script_nonce else ""
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
           f'content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">'
           f'<title>{esc(title)}</title><style>{STYLE}</style></head><body>{body}{script_tag}</body></html>')
    return HTMLResponse(doc, status_code=status, headers={
        **NO_STORE, "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": ("default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
                                    "base-uri 'none';" + script)})


def error_page(text: str, status: int = 400) -> HTMLResponse:
    return page("Can't connect this app", f"<h1>This app can't be connected</h1><p>{esc(text)}</p>"
                "<p class=small>Nothing was shared. Go back to the app and try again.</p>", status=status)


def return_page(exc: RedirectError, settings: Settings) -> HTMLResponse:
    """A request error found before anyone has agreed to anything. The redirect address is only what the app declared
    (anyone can declare one), so the Vault does not send a person there by itself: it says what was wrong and who the
    address belongs to, and the person chooses to go back (#339)."""
    target = with_query(exc.redirect_uri, {"error": exc.code, "error_description": exc.description, "state": exc.state,
                                           "iss": settings.base_url})
    host, ascii_form = clients.display_host(urlsplit(exc.redirect_uri).hostname or "")
    who = client_label(exc.client) if exc.client is not None else "An app"
    look_alike = (f' <span class="warn">This address uses non-English letters; its technical form is '
                  f"<code>{esc(ascii_form)}</code>. Check it carefully.</span>") if host != ascii_form else ""
    return page("Can't connect this app", f"<h1>This app can't be connected</h1><p><b>{esc(who)}</b> sent a request the Vault "
                f"can't use: {esc(exc.description)}.</p><p class=small>Nothing was shared. The app gave "
                f"<code>{esc(host)}</code> as the address to return to; go there only if you started this from that app.{look_alike}</p>"
                f'<p><a class="button" href="{esc(target)}">Return to {esc(host)}</a></p>', status=400)


def client_label(client: OAuthClient) -> str:
    """The app as a title: a named app is always followed by the address that identifies it (a name
    is whatever the app says; the address is what it is), and a self-registered app is called unverified."""
    if client.kind == "cimd":
        return f"{client.name} ({clients.display_host(urlsplit(client.client_id).hostname)[0]})"
    return f"Unverified app: {client.name}"


def who_is_asking(client: OAuthClient) -> str:
    if client.kind == "cimd":
        readable, ascii_form = clients.display_host(urlsplit(client.client_id).hostname)
        text = f"Identified by its web address <strong><code>{esc(readable)}</code></strong>."
        if readable != ascii_form:
            text += (f' <span class="warn">This address uses non-English letters; its technical form is '
                     f"<code>{esc(ascii_form)}</code>. Check it carefully.</span>")
        return text
    return ('<span class="warn"><strong>Unverified app.</strong> It registered itself, so the Vault can\'t confirm who '
            "made it or that its name is true. Continue only if you started this connection.</span>")


def where_it_returns(redirect_uri: str) -> str:
    if clients.is_loopback(redirect_uri):
        return ("<p class=warn>This app runs <strong>on this computer</strong> (it will receive the answer at "
                "<code>localhost</code>). Continue only if you just started it yourself.</p>")
    return f"<p class=small>After you answer, you are sent back to <code>{esc(urlsplit(redirect_uri).netloc)}</code>.</p>"


def sign_in_page(req: AuthRequest, providers: list[str], passkeys: bool, dev_login: bool, reviewers: bool = False) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    buttons = "".join(f'<a class=button href="/api/auth/login/{esc(p)}?continue=oauth">Continue with {esc(p.capitalize())}</a>'
                      for p in providers)
    if passkeys:
        buttons += '<button type="button" id="passkey">Sign in with a passkey</button>'
    if dev_login:
        buttons += '<button type="button" id="dev">Developer sign-in</button>'
    if reviewers:  # only while REVIEWER_PASSPHRASE is set (vault.reviewer): for the stores' review accounts
        buttons += ('</p><p class="small"><label>Reviewer sign-in (for app store reviewers) <input type="password" id="rp" '
                    'autocomplete="off" aria-label="Reviewer passphrase"></label> <button type="button" id="reviewer">Sign in</button>')
    body = (f"<h1>Sign in to connect {esc(client_label(req.client))}</h1><p>{who_is_asking(req.client)}</p>"
            f"<p>Sign in to your Vault account to choose what it may do.</p><p>{buttons}</p>"
            '<p id="note" class="small" role="status"></p>'
            '<p class="small">New to the Vault? Create your account at <a href="/">the Vault</a> first, then connect again.</p>')
    return page("Sign in to the Vault", body, script_nonce=nonce)


def consent_page(req: AuthRequest, user: User, nonce: str) -> HTMLResponse:
    write = ('<label><input type="checkbox" name="write" value="on"> <strong>Write.</strong> '
             f"{esc(SCOPE_TEXT['write'])}</label>") if "write" in req.scopes else ""
    never = "".join(f"<li>{esc(n)}</li>" for n in NEVER)
    body = (f"<h1>Connect {esc(client_label(req.client))} to your Vault?</h1><p>{who_is_asking(req.client)}</p>"
            f"{where_it_returns(req.redirect_uri)}"
            f'<p class=small>Signed in as <strong>{esc(user.name or user.email or "your account")}</strong>.</p>'
            '<form method="post" action="/oauth/authorize"><input type="hidden" name="nonce" value="' + esc(nonce) + '">'
            f"<fieldset><legend>It will be able to</legend><label><input type=\"checkbox\" checked disabled> "
            f"<strong>Read.</strong> {esc(SCOPE_TEXT['read'])}</label>{write}</fieldset>"
            f"<p>It will <strong>never</strong> be able to:</p><ul class=never>{never}</ul>"
            '<button type="submit" name="decision" value="allow">Allow</button>'
            '<button type="submit" name="decision" value="deny">Cancel</button>'
            '<button type="submit" name="decision" value="switch">Use a different account</button></form>'
            '<p class="small">You can disconnect this app at any time under Connected apps in your account.</p>')
    return page("Connect an app to the Vault", body)


# -- responses -------------------------------------------------------------------------------

def redirect(uri: str, params: dict, settings: Settings) -> RedirectResponse:
    # RFC 9207: the issuer lets a client tell this server's answer from another's (mix-up attacks).
    return RedirectResponse(with_query(uri, {**params, "iss": settings.base_url}), status_code=303, headers=NO_STORE)


def token_error(exc: server.OAuthError | clients.ClientError, status: int | None = None) -> JSONResponse:
    code = exc.code
    description = getattr(exc, "description", str(exc))
    status = status or getattr(exc, "status", 400)
    if code == "invalid_client":
        status = 401
    return JSONResponse({"error": code, "error_description": description}, status_code=status,
                        headers={**NO_STORE, **CORS})


OPENAI_APPS_CHALLENGE = "0x5MnvfLbnis5EFcH-Ub17kVBdb435wzrN1b5tYCYyM"  # domain check of the ChatGPT plugin (#240)


def build_router(get_db, settings: Settings, fetcher: clients.ClientFetcher, auth) -> APIRouter:
    router = APIRouter(tags=["oauth"])
    resource = server.resource_uri(settings.base_url)
    resource_path = urlsplit(resource).path
    limit = limited("oauth", setting="oauth_rate_limit")
    metadata_headers = {**CORS, "Cache-Control": "public, max-age=3600"}

    # -- metadata -------------------------------------------------------------------------
    @router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @router.get("/.well-known/oauth-protected-resource" + resource_path, include_in_schema=False)
    def protected_resource() -> JSONResponse:
        return JSONResponse({
            "resource": resource, "authorization_servers": [settings.base_url],
            "scopes_supported": list(server.SCOPES), "bearer_methods_supported": ["header"],
            "resource_name": "The Vault (MTG collection)", "resource_documentation": f"{settings.base_url}/llms.txt",
        }, headers=metadata_headers)

    @router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    def authorization_server() -> JSONResponse:
        base = settings.base_url
        return JSONResponse({
            "issuer": base, "authorization_endpoint": f"{base}/oauth/authorize", "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register", "revocation_endpoint": f"{base}/oauth/revoke",
            "scopes_supported": list(server.SCOPES), "response_types_supported": ["code"],
            "response_modes_supported": ["query"], "grant_types_supported": ["authorization_code", "refresh_token"],
            # public clients (PKCE) and clients that sign an assertion with their published keys (vault.client_auth)
            "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
            "token_endpoint_auth_signing_alg_values_supported": list(client_auth.ALGORITHMS),
            "revocation_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"], "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True, "service_documentation": f"{base}/llms.txt",
        }, headers=metadata_headers)

    @router.get("/.well-known/openai-apps-challenge", include_in_schema=False)
    def openai_apps_challenge() -> Response:
        # OpenAI's plugin portal checks that we own the MCP host: the exact token, as plain text (not a secret).
        return Response(OPENAI_APPS_CHALLENGE, media_type="text/plain")

    @router.options("/.well-known/oauth-protected-resource", include_in_schema=False)
    @router.options("/.well-known/oauth-protected-resource" + resource_path, include_in_schema=False)
    @router.options("/.well-known/oauth-authorization-server", include_in_schema=False)
    @router.options("/oauth/token", include_in_schema=False)
    @router.options("/oauth/register", include_in_schema=False)
    @router.options("/oauth/revoke", include_in_schema=False)
    def preflight() -> Response:
        return Response(status_code=204, headers=CORS)

    # -- authorization endpoint -----------------------------------------------------------
    def check(request: Request, raw: str, db: Session) -> AuthRequest:
        # What is stored is the raw string (consent row, session), so its own length is what is limited.
        if len(raw) > MAX_QUERY:
            raise PageError("This request is too long.")
        caller = hmac.new(settings.session_secret.encode(), f"oauth-fetch:{client_ip(request, settings)}".encode(),
                          "sha256").hexdigest()
        return parse_authorize(QueryParams(raw), db, fetcher, settings, caller)

    @router.get("/oauth/authorize", dependencies=limit, include_in_schema=False)
    def authorize(request: Request, db: Session = Depends(get_db)):
        raw = request.url.query
        try:
            req = check(request, raw, db)
        except PageError as exc:
            return error_page(exc.text, exc.status)
        except RedirectError as exc:
            return return_page(exc, settings)
        user = session_user(db, request)
        # Connecting an app mints a 30-day credential (read, and write if allowed): the same power as a personal access token, which needs a
        # recent sign-in (#347). A stale session signs in again first, with a method the account has (that makes it fresh) and comes back here.
        # A store reviewer's demo session is exempt: it has no account powers and is the way the stores' review connects an app.
        if user is not None and "rv" not in request.session and not recent_signin.is_fresh(db, request, settings):
            user = None
        if user is None:
            request.session["oauth_pending"] = {"q": raw, "t": int(time.time())}
            return sign_in_page(req, auth.offered, _passkeys_on(), settings.dev_login, bool(settings.reviewer_passphrase))
        request.session.pop("oauth_pending", None)
        nonce = secrets.token_urlsafe(24)
        server.save_consent(db, user.id, nonce, raw)
        # The browser's half: the nonces of its open consent screens (oldest dropped past the cap, as in the
        # database), so a second authorize request in another tab does not replace the first screen's.
        open_screens = [n for n in request.session.get("oauth_consents", []) if isinstance(n, str)]
        request.session["oauth_consents"] = open_screens[-(server.MAX_PENDING_CONSENTS - 1):] + [nonce]
        return consent_page(req, user, nonce)

    def _passkeys_on() -> bool:
        from .passkeys import enabled

        return enabled(settings)

    @router.post("/oauth/authorize", dependencies=limit, include_in_schema=False)
    def decide(request: Request, nonce: str = Form(""), decision: str = Form(""), write: str = Form(""),
               db: Session = Depends(get_db)):
        """The person's answer. The request being answered comes from the session, not the form, and
        the one-time nonce proves this form is the one we just showed this browser (CSRF)."""
        mine = [n for n in request.session.get("oauth_consents", []) if isinstance(n, str)]
        shown = next((n for n in mine if hmac.compare_digest(n.encode(), nonce.encode())), None)
        if shown is not None:
            request.session["oauth_consents"] = [n for n in mine if n != shown]
        user = session_user(db, request)
        if user is not None and "rv" not in request.session and not recent_signin.is_fresh(db, request, settings):
            return error_page("This needs a sign-in from the last few minutes, and yours is older. Start again from the app: the Vault "
                              "will ask you to sign in first.")  # (#347; the screen was shown while the session was recent)
        # Both halves must match (this browser's, and the form's), then the database row is consumed
        # atomically: a copied cookie and form can not be played twice.
        asked = server.take_consent(db, user.id, nonce) if user is not None and shown is not None else None
        if asked is None:
            return error_page("This page expired or was not made for this browser. Start again from the app.")
        try:
            req = check(request, asked, db)
        except PageError as exc:
            return error_page(exc.text, exc.status)
        except RedirectError as exc:
            return redirect(exc.redirect_uri, {"error": exc.code, "state": exc.state}, settings)
        if decision == "switch":  # another account: sign out here, then sign in again
            request.session.clear()
            request.session["oauth_pending"] = {"q": asked, "t": int(time.time())}
            return RedirectResponse("/oauth/authorize?" + asked, status_code=303, headers=NO_STORE)
        if decision != "allow":
            return redirect(req.redirect_uri, {"error": "access_denied", "state": req.state}, settings)
        granted = ["read"] + (["write"] if write == "on" and "write" in req.scopes else [])
        try:
            require_live_session(db, request, user.id)  # "Sign out the other browsers" may have ended this session meanwhile (#347)
        except SessionEnded:
            return error_page("Your session ended while you were deciding. Start again from the app.")
        code = server.issue_code(db, user, req.client.client_id, req.redirect_uri, req.code_challenge, req.resource, granted)
        clients.mark_used(db, req.client)
        return redirect(req.redirect_uri, {"code": code, "state": req.state}, settings)

    # -- token endpoint -------------------------------------------------------------------
    keys = client_auth.KeyCache()

    def authenticate_client(db: Session, client_id: str, assertion_type: str | None, assertion: str | None) -> None:
        """A private_key_jwt client must prove itself with a signed assertion; a public one must not send one."""
        client = clients.known_client(db, client_id)
        if client is not None and client.token_auth == "private_key_jwt":
            client_auth.verify(db, fetcher, keys, client_id=client_id, jwks_uri=client.jwks_uri or "",
                               algorithm=client.auth_alg or "", assertion_type=assertion_type, assertion=assertion,
                               audiences=(f"{settings.base_url}/oauth/token", settings.base_url))
            db.commit()  # the assertion is spent even if the grant then fails
        elif assertion or assertion_type:
            raise clients.ClientError("invalid_client", "This client is public (PKCE only): it must not send a client_assertion")

    @router.post("/oauth/token", dependencies=limit, include_in_schema=False)
    def token(grant_type: str | None = Form(None), client_id: str | None = Form(None), code: str | None = Form(None),
              redirect_uri: str | None = Form(None), code_verifier: str | None = Form(None),
              refresh_token: str | None = Form(None), scope: str | None = Form(None),
              resource_: str | None = Form(None, alias="resource"),
              client_assertion_type: str | None = Form(None), client_assertion: str | None = Form(None),
              db: Session = Depends(get_db)):
        try:
            if not client_id and client_assertion:
                client_id = client_auth.unverified_issuer(client_assertion)  # only to know whose keys to check it with
            if not client_id:
                raise server.OAuthError("invalid_client", "client_id is required")
            authenticate_client(db, client_id, client_assertion_type, client_assertion)
            if grant_type == "authorization_code":
                if not (code and redirect_uri and code_verifier):
                    raise server.OAuthError("invalid_request", "code, redirect_uri and code_verifier are required")
                result = server.exchange_code(db, client_id, code, redirect_uri, code_verifier, resource_)
            elif grant_type == "refresh_token":
                if not refresh_token:
                    raise server.OAuthError("invalid_request", "refresh_token is required")
                result = server.refresh(db, client_id, refresh_token, scope, resource_)
            else:
                raise server.OAuthError("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")
        except (server.OAuthError, clients.ClientError) as exc:
            if exc.code == "invalid_client":  # why an app could not connect: its public client_id and our reason, never a secret
                log.warning("token request refused: client=%r grant=%r assertion=%s type=%r reason=%s", (client_id or "")[:200],
                            (grant_type or "")[:40], bool(client_assertion), (client_assertion_type or "")[:80],
                            getattr(exc, "description", str(exc)))
            return token_error(exc)
        return JSONResponse(result, headers={**NO_STORE, **CORS})

    @router.post("/oauth/revoke", dependencies=limit, include_in_schema=False)
    def revoke(token: str | None = Form(None), client_id: str | None = Form(None), db: Session = Depends(get_db)):
        if token and client_id:
            server.revoke_token(db, client_id, token)
        return Response(status_code=200, headers={**NO_STORE, **CORS})  # the same answer whatever the token was

    # -- dynamic client registration ------------------------------------------------------
    @router.post("/oauth/register", dependencies=limited("oauth-register", setting="oauth_register_rate_limit"),
                 include_in_schema=False)
    async def register(request: Request):
        raw = await request.body()
        if len(raw) > REGISTER_MAX_BYTES:
            return JSONResponse({"error": "invalid_client_metadata", "error_description": "The registration is too large"},
                                status_code=413, headers={**NO_STORE, **CORS})

        def run() -> JSONResponse:
            try:
                body = json.loads(raw or b"null")
                with request.app.state.db.sessions() as db:
                    return JSONResponse(clients.register(db, body, settings.oauth_client_cap), status_code=201,
                                        headers={**NO_STORE, **CORS})
            except (ValueError, RecursionError):
                return JSONResponse({"error": "invalid_client_metadata", "error_description": "The body must be JSON"},
                                    status_code=400, headers={**NO_STORE, **CORS})
            except clients.ClientError as exc:
                status = 503 if exc.code == "temporarily_unavailable" else 400
                return JSONResponse({"error": exc.code, "error_description": exc.description}, status_code=status,
                                    headers={**NO_STORE, **CORS})

        return await run_in_threadpool(run)

    return router
