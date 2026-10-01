"""Sign-in with Google, Microsoft, Apple and Facebook (OAuth 2 / OpenID Connect).

Flow: ``GET /api/auth/login/{provider}`` redirects to the provider, which sends
the user back to ``/api/auth/callback/{provider}`` (Apple POSTs the form there).
We look up the ``(provider, subject)`` identity, create the user on first
sign-in, and keep only the user id in a signed session cookie.

Accounts are never merged by e-mail address: if someone is already signed in
and signs in with another provider, that provider is *linked* to the current
account; otherwise a new account is created.

Provider quirks handled here:

* Microsoft (``common`` tenant): the ID token issuer contains the user's tenant
  id, so it is checked against the ``tid`` claim instead of the static metadata.
* Apple: the client secret is a short-lived ES256 JWT we sign with the .p8 key
  (regenerated automatically; no manual 6-month rotation). Apple POSTs the
  callback (``form_post``), which needs a ``SameSite=None`` session cookie, and
  only sends the user's name on the very first sign-in.
* Facebook: plain OAuth 2, profile fetched from the Graph API ``/me`` endpoint.
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.starlette_client import OAuth
from joserfc.errors import JoseError
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import ECKey
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import outbound
from .config import Settings
from .models import Identity, User, new_session_key

log = logging.getLogger(__name__)

PROVIDERS = ("google", "microsoft", "apple", "facebook")
# Anything that can go wrong validating a provider's response: OAuth errors, bad/expired
# tokens, wrong issuer or nonce, bad signatures, missing claims, provider outages.
SIGN_IN_ERRORS = (AuthlibBaseError, JoseError, KeyError, ValueError, httpx.HTTPError)
APPLE_SECRET_TTL = 3600  # seconds; Apple allows up to 6 months


@dataclass
class Profile:
    provider: str
    subject: str
    email: str | None
    name: str | None


def microsoft_issuer_ok(claims, value: str) -> bool:
    """Multi-tenant Entra ID tokens are issued by the user's own tenant."""
    tid = claims.get("tid")
    return bool(tid) and value == f"https://login.microsoftonline.com/{tid}/v2.0"


def apple_client_secret(settings: Settings, now: int | None = None) -> str:
    now = int(now or time.time())
    key = ECKey.import_key(settings.apple_private_key)
    claims = {
        "iss": settings.apple_team_id,
        "iat": now,
        "exp": now + APPLE_SECRET_TTL,
        "aud": "https://appleid.apple.com",
        "sub": settings.apple_client_id,
    }
    return jwt.encode({"alg": "ES256", "kid": settings.apple_key_id}, claims, key)


class Auth:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        """``transport`` replaces the network for all provider calls (tests: the twin universe)."""
        self.settings = settings
        self.oauth = OAuth()
        self._apple_secret_at = 0.0
        s = settings
        if s.google_client_id:
            self.oauth.register(
                "google", client_id=s.google_client_id, client_secret=s.google_client_secret,
                server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                client_kwargs={"scope": "openid email profile"},
            )
        if s.microsoft_client_id:
            self.oauth.register(
                "microsoft", client_id=s.microsoft_client_id, client_secret=s.microsoft_client_secret,
                server_metadata_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
                client_kwargs={"scope": "openid email profile"},
            )
        if s.apple_client_id:
            self.oauth.register(
                "apple", client_id=s.apple_client_id, client_secret="",
                server_metadata_url="https://appleid.apple.com/.well-known/openid-configuration",
                # "openid" is required for an id_token (and the nonce check); without it there's no user id.
                client_kwargs={"scope": "openid name email", "token_endpoint_auth_method": "client_secret_post"},
                authorize_params={"response_mode": "form_post"},
            )
        if s.facebook_client_id:
            v = s.facebook_graph_version
            self.oauth.register(
                "facebook", client_id=s.facebook_client_id, client_secret=s.facebook_client_secret,
                authorize_url=f"https://www.facebook.com/{v}/dialog/oauth",
                access_token_url=f"https://graph.facebook.com/{v}/oauth/access_token",
                api_base_url=f"https://graph.facebook.com/{v}/",
                client_kwargs={"scope": "email public_profile"},
            )

        if transport is not None:
            for name in PROVIDERS:
                client = self.oauth.create_client(name)
                if client is not None:
                    client.client_kwargs["transport"] = transport

    @property
    def enabled(self) -> list[str]:
        return [p for p in PROVIDERS if self.oauth.create_client(p) is not None]

    def client(self, provider: str):
        client = self.oauth.create_client(provider) if provider in PROVIDERS else None
        if client is None:
            raise HTTPException(404, f"Sign-in with {provider} is not configured")
        if provider == "apple" and time.time() - self._apple_secret_at > APPLE_SECRET_TTL / 2:
            client.client_secret = apple_client_secret(self.settings)
            self._apple_secret_at = time.time()
        return client

    async def profile(self, provider: str, request: Request) -> Profile:
        client = self.client(provider)
        if provider == "facebook":  # plain OAuth 2.0, no ID token
            token = await client.authorize_access_token(request)
        else:
            # Authlib checks only what claims_options asks for, and "aud" isn't checked by default.
            # OpenID Connect requires it, so check it here, along with the issuer.
            if provider == "microsoft":  # "common" issues per-tenant issuers
                iss = {"essential": True, "validate": microsoft_issuer_ok}
            else:
                iss = {"essential": True, "values": [(await client.load_server_metadata())["issuer"]]}
            options = {"iss": iss, "aud": {"essential": True, "value": client.client_id}}
            token = await client.authorize_access_token(request, claims_options=options)

        if provider == "facebook":
            me = (await client.get("me", params={"fields": "id,name,email"}, token=token)).json()
            return Profile(provider, str(me["id"]), me.get("email"), me.get("name"))

        info = token.get("userinfo") or {}
        name = info.get("name")
        if provider == "apple":  # name arrives once, as JSON in the POSTed form
            form = await request.form()
            try:
                posted = json.loads(form.get("user") or "{}")
            except (TypeError, ValueError):
                posted = None
            # Whatever was POSTed: only a {"name": {"firstName": "...", "lastName": "..."}} gives a name.
            n = posted.get("name") if isinstance(posted, dict) else None
            parts = [n.get(k) for k in ("firstName", "lastName")] if isinstance(n, dict) else []
            name = " ".join(p for p in parts if isinstance(p, str) and p) or None
        email = info.get("email") or info.get("preferred_username")
        return Profile(provider, str(info["sub"]), email, name)


PKCE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43,128}")

# Providers' names for "the user pressed Cancel", as the one code the sign-in screen knows.
CANCELLED = {"user_cancelled_authorize": "access_denied", "user_denied": "access_denied"}


class IdentityInUse(Exception):
    """Linking a sign-in that already belongs to another Vault account."""

    def __init__(self) -> None:
        super().__init__("This sign-in already belongs to another Vault account, so it can't be linked to this one")


def find_or_create(db: Session, profile: Profile, current: User | None = None) -> User:
    """The user owning ``profile``'s identity. A new identity is linked to ``current`` when
    someone is already signed in; otherwise it gets a new account. Never merged by e-mail.

    Raises IdentityInUse when ``current`` is set and the identity belongs to someone else:
    linking never switches accounts.

    Two first sign-ins with one identity at the same time both try to create it; the unique
    (provider, subject) index lets one win, and the other then signs in to the winner's account."""
    if profile.email and len(profile.email) > 320:  # no real address is this long (users.email is String(320))
        profile = Profile(profile.provider, profile.subject, None, profile.name)
    try:
        return _find_or_create(db, profile, current)
    except IntegrityError:
        db.rollback()
        return _find_or_create(db, profile, current)


def _find_or_create(db: Session, profile: Profile, current: User | None) -> User:
    name = (profile.name or "")[:200] or None  # users.name is String(200), whatever the provider sent
    identity = db.scalar(
        select(Identity).where(Identity.provider == profile.provider, Identity.subject == profile.subject)
    )
    if identity:
        if current is not None and identity.user_id != current.id:
            raise IdentityInUse()
        user = identity.user
        identity.email = profile.email or identity.email
    else:
        user = current or User(email=profile.email, name=name)
        if current is None:
            db.add(user)
        user.identities.append(Identity(provider=profile.provider, subject=profile.subject, email=profile.email))
    if name and not user.name:
        user.name = name
    if profile.email and not user.email:
        user.email = profile.email
    db.commit()
    return user


def sign_in(db: Session, request: Request, profile: Profile, link: bool = True) -> User:
    """Find or create the user for ``profile`` and put them in the browser session.

    With ``link`` (provider sign-ins), a new identity joins the account already signed in here.
    Without it (passkey sign-in and sign-up, dev login) the browser simply switches accounts."""
    current = session_user(db, request) if link else None
    user = find_or_create(db, profile, current)
    if not user.session_key:
        user.session_key = new_session_key()
        db.commit()
    app_flow = request.session.get("app_flow")
    request.session.clear()
    request.session["uid"] = user.id
    request.session["sk"] = user.session_key
    if app_flow:
        request.session["app_flow"] = app_flow
    return user


def session_user(db: Session, request: Request) -> User | None:
    """The user this browser's session cookie signs in, if it is still valid: the cookie's key
    must match the account's (see ``User.session_key``)."""
    uid, key = request.session.get("uid"), request.session.get("sk")
    user = db.get(User, uid) if isinstance(uid, int) and isinstance(key, str) else None
    if user is None or not user.session_key or not hmac.compare_digest(user.session_key, key):
        return None
    return user


APP_FLOW_KEYS = ("app_redirect_uri", "code_challenge")

HANDOFF_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in to the Vault app</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:28rem;margin:4rem auto;padding:0 1rem;color:#1b1b1b;background:#fff}}
button{{font:inherit;padding:.6rem 1.2rem;margin:.25rem .5rem 0 0;border-radius:.5rem;border:1px solid #555;background:#fff;cursor:pointer}}
button[value=continue]{{background:#1b1b1b;color:#fff}}
@media (prefers-color-scheme:dark){{body{{background:#121212;color:#eee}}button{{background:#222;color:#eee}}button[value=continue]{{background:#eee;color:#121212}}}}</style>
</head><body><h1>Sign in to the Vault app?</h1>
<p>You are signed in as <strong>{who}</strong>. Continue only if you just asked the Vault app on this device to sign in.</p>
<form method="post" action="/api/auth/app-handoff">
<button type="submit" name="decision" value="continue" autofocus>Continue to the app</button>
<button type="submit" name="decision" value="cancel">Cancel</button></form></body></html>"""


def build_router(auth: Auth, get_db) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/providers")
    def providers() -> dict:
        from .passkeys import enabled as passkeys_enabled

        return {"providers": auth.enabled, "dev_login": auth.settings.dev_login,
                "passkeys": passkeys_enabled(auth.settings)}

    @router.get("/login/{provider}")
    async def login(provider: str, request: Request, app_redirect_uri: str | None = None,
                    code_challenge: str | None = None, code_challenge_method: str | None = None):
        """Browser sign-in. Native apps open this in ASWebAuthenticationSession with
        ``app_redirect_uri`` (one of APP_REDIRECT_URIS) and a PKCE ``code_challenge`` (S256);
        they get ``<app_redirect_uri>?code=...`` back and redeem it at POST /api/v1/auth/token."""
        request.session.pop("app_flow", None)
        if app_redirect_uri is not None:
            if app_redirect_uri not in auth.settings.app_redirect_uris:
                raise HTTPException(400, "app_redirect_uri is not allowed")
            # RFC 7636: an S256 challenge is 43-128 base64url characters (also the column's limit).
            if (not code_challenge or (code_challenge_method or "S256") != "S256"
                    or not PKCE_CHALLENGE.fullmatch(code_challenge)):
                raise HTTPException(400, "A PKCE code_challenge (S256, 43-128 base64url characters) is required")
            request.session["app_flow"] = {"app_redirect_uri": app_redirect_uri, "code_challenge": code_challenge}
        redirect_uri = f"{auth.settings.base_url}/api/auth/callback/{provider}"
        client = auth.client(provider)
        try:
            response = await client.authorize_redirect(request, redirect_uri)
        except SIGN_IN_ERRORS as exc:  # e.g. the provider's discovery document is unreachable
            log.warning("sign-in with %s could not start: %s", provider, exc)
            app_flow = request.session.pop("app_flow", None)
            target = app_flow["app_redirect_uri"] if app_flow else "/"
            key = "error" if app_flow else "signin_error"
            query = {key: "temporarily_unavailable", **({} if app_flow else {"provider": provider})}
            return RedirectResponse(f"{target}?{urlencode(query)}", status_code=303)
        if auth.settings.twins_url:  # local development: the browser goes to the twin, not the real provider
            response.headers["location"] = outbound.browser_url(auth.settings, response.headers["location"])
        return response

    @router.api_route("/callback/{provider}", methods=["GET", "POST"])
    async def callback(provider: str, request: Request, db: Session = Depends(get_db)):
        app_flow = request.session.get("app_flow")
        try:
            profile = await auth.profile(provider, request)
        except SIGN_IN_ERRORS as exc:
            code = getattr(exc, "error", None) or "invalid_response"
            if code == "missing_code":  # the provider answered with its own error instead of a code
                answer = request.query_params if request.method == "GET" else await request.form()
                code = str(answer.get("error") or code)
            code = CANCELLED.get(code, code)
            log.warning("sign-in with %s failed: %s", provider, exc)
            if app_flow:
                request.session.pop("app_flow", None)
                return RedirectResponse(f"{app_flow['app_redirect_uri']}?{urlencode({'error': code})}", status_code=303)
            return RedirectResponse(f"/?{urlencode({'signin_error': code, 'provider': provider})}", status_code=303)
        try:
            user = sign_in(db, request, profile)
        except IdentityInUse:
            if app_flow:
                request.session.pop("app_flow", None)
                return RedirectResponse(f"{app_flow['app_redirect_uri']}?{urlencode({'error': 'identity_in_use'})}",
                                        status_code=303)
            return RedirectResponse("/?link_error=identity_in_use", status_code=303)
        if app_flow:
            # The code goes to the app only after the person confirms here. Anyone can start this
            # flow with their own PKCE challenge and send the link to someone; a silent sign-in
            # must not then hand that person's code to whichever app claims the custom scheme.
            request.session.pop("app_flow", None)
            request.session["app_handoff"] = {**app_flow, "uid": user.id}
            return RedirectResponse("/api/auth/app-handoff", status_code=303)
        return RedirectResponse("/", status_code=303)

    @router.get("/app-handoff", response_class=HTMLResponse)
    def app_handoff_page(request: Request, db: Session = Depends(get_db)):
        handoff, user = request.session.get("app_handoff"), session_user(db, request)
        if not handoff or user is None or user.id != handoff.get("uid"):
            raise HTTPException(404, "No app sign-in is waiting")
        who = html.escape(user.name or user.email or "your Vault account")
        return HTMLResponse(HANDOFF_PAGE.format(who=who), headers={"Cache-Control": "no-store"})

    @router.post("/app-handoff")
    def app_handoff(request: Request, decision: str = Form(...), db: Session = Depends(get_db)):
        """The person's answer: hand a one-time code to the app, or tell it they declined."""
        handoff, user = request.session.pop("app_handoff", None), session_user(db, request)
        if not handoff or user is None or user.id != handoff.get("uid"):
            raise HTTPException(404, "No app sign-in is waiting")
        target = handoff["app_redirect_uri"]
        if decision != "continue":
            return RedirectResponse(f"{target}?{urlencode({'error': 'access_denied'})}", status_code=303)
        from . import tokens

        code = tokens.create_code(db, user, handoff["code_challenge"], target)
        return RedirectResponse(f"{target}?{urlencode({'code': code})}", status_code=303)

    @router.post("/logout")
    def logout(request: Request, everywhere: bool = False, db: Session = Depends(get_db)) -> dict:
        """Sign this browser out; with ``?everywhere=true``, every browser signed in to the account
        (a copied session cookie stops working too). Apps are signed out under /me/sessions."""
        user = session_user(db, request) if everywhere else None
        if user is not None:
            user.session_key = new_session_key()
            db.commit()
        request.session.clear()
        return {"ok": True}

    @router.post("/dev-login")
    def dev_login(request: Request, db: Session = Depends(get_db),
                  email: str = Query("dev@localhost", min_length=1, max_length=255)) -> dict:
        if not auth.settings.dev_login:
            raise HTTPException(404)
        user = sign_in(db, request, Profile("dev", email, email, "Local developer"), link=False)
        return {"id": user.id}

    return router
