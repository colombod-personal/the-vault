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

import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.starlette_client import OAuth
from joserfc.errors import JoseError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from joserfc import jwt
from joserfc.jwk import ECKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Identity, User

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
        """``transport`` replaces the network for all provider calls (used by the tests'
        fake identity provider)."""
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
        if provider == "microsoft":
            token = await client.authorize_access_token(
                request, claims_options={"iss": {"essential": True, "validate": microsoft_issuer_ok}}
            )
        else:
            token = await client.authorize_access_token(request)

        if provider == "facebook":
            me = (await client.get("me", params={"fields": "id,name,email"}, token=token)).json()
            return Profile(provider, str(me["id"]), me.get("email"), me.get("name"))

        info = token.get("userinfo") or {}
        name = info.get("name")
        if provider == "apple":  # name arrives once, as JSON in the POSTed form
            form = await request.form()
            try:
                n = json.loads(form.get("user") or "{}").get("name") or {}
                name = " ".join(filter(None, [n.get("firstName"), n.get("lastName")])) or None
            except ValueError:
                name = None
        email = info.get("email") or info.get("preferred_username")
        return Profile(provider, str(info["sub"]), email, name)


def find_or_create(db: Session, profile: Profile, current: User | None = None) -> User:
    """The user owning ``profile``'s identity. A new identity is linked to ``current`` when
    someone is already signed in; otherwise it gets a new account. Never merged by e-mail."""
    identity = db.scalar(
        select(Identity).where(Identity.provider == profile.provider, Identity.subject == profile.subject)
    )
    if identity:
        user = identity.user
        identity.email = profile.email or identity.email
    else:
        user = current or User(email=profile.email, name=profile.name)
        if current is None:
            db.add(user)
        user.identities.append(Identity(provider=profile.provider, subject=profile.subject, email=profile.email))
    if profile.name and not user.name:
        user.name = profile.name
    if profile.email and not user.email:
        user.email = profile.email
    db.commit()
    return user


def sign_in(db: Session, request: Request, profile: Profile) -> User:
    """Find or create the user for ``profile`` and put them in the browser session."""
    current = db.get(User, request.session.get("uid")) if request.session.get("uid") else None
    user = find_or_create(db, profile, current)
    app_flow = request.session.get("app_flow")
    request.session.clear()
    request.session["uid"] = user.id
    if app_flow:
        request.session["app_flow"] = app_flow
    return user


APP_FLOW_KEYS = ("app_redirect_uri", "code_challenge")


def build_router(auth: Auth, get_db) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/providers")
    def providers() -> dict:
        return {"providers": auth.enabled, "dev_login": auth.settings.dev_login}

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
            if not code_challenge or (code_challenge_method or "S256") != "S256" or len(code_challenge) < 43:
                raise HTTPException(400, "A PKCE code_challenge (S256) is required")
            request.session["app_flow"] = {"app_redirect_uri": app_redirect_uri, "code_challenge": code_challenge}
        redirect_uri = f"{auth.settings.base_url}/api/auth/callback/{provider}"
        return await auth.client(provider).authorize_redirect(request, redirect_uri)

    @router.api_route("/callback/{provider}", methods=["GET", "POST"])
    async def callback(provider: str, request: Request, db: Session = Depends(get_db)):
        app_flow = request.session.get("app_flow")
        try:
            profile = await auth.profile(provider, request)
        except SIGN_IN_ERRORS as exc:
            code = getattr(exc, "error", None) or "invalid_response"
            log.warning("sign-in with %s failed: %s", provider, exc)
            if app_flow:
                request.session.pop("app_flow", None)
                return RedirectResponse(f"{app_flow['app_redirect_uri']}?{urlencode({'error': code})}", status_code=303)
            return RedirectResponse(f"/?signin_error={code}", status_code=303)
        user = sign_in(db, request, profile)
        if app_flow:
            from . import tokens

            request.session.pop("app_flow", None)
            code = tokens.create_code(db, user, app_flow["code_challenge"], app_flow["app_redirect_uri"])
            return RedirectResponse(f"{app_flow['app_redirect_uri']}?{urlencode({'code': code})}", status_code=303)
        return RedirectResponse("/", status_code=303)

    @router.post("/logout")
    def logout(request: Request) -> dict:
        request.session.clear()
        return {"ok": True}

    @router.post("/dev-login")
    def dev_login(request: Request, db: Session = Depends(get_db), email: str = "dev@localhost") -> dict:
        if not auth.settings.dev_login:
            raise HTTPException(404)
        user = sign_in(db, request, Profile("dev", email, email, "Local developer"))
        return {"id": user.id}

    return router
