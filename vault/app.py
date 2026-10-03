"""FastAPI application."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth as auth_module
from . import outbound, passkeys, tokens
from .api import mcp, meta, v1
from .api.hal import problem
from .config import Settings
from .db import Database
from .models import User
from .native import NativeVerifier

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ACCOUNT_COOKIE = "vault_account"  # see account_marker
# Called cross-site by design: OAuth providers (Apple POSTs) and Meta's deletion callback.
CROSS_SITE_ALLOWED = ("/api/auth/callback/", "/api/facebook/data-deletion")
# POSTs a read-only token may call: they only compute an answer, or revoke the token itself.
READ_ONLY_POSTS = {"/api/v1/decks/parse", "/api/v1/decks/coverage", "/api/v1/auth/revoke", "/api/v1/cards/lookup"}


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def create_app(settings: Settings | None = None, *, serve_static: bool = True, transport=None) -> FastAPI:
    """``transport`` replaces the network for every outbound call: sign-in providers, Archidekt
    (tests pass the twin universe's). Without one, ``VAULT_TWINS_URL`` routes calls to the twin
    server; otherwise they go to the real services."""
    settings = settings or Settings()
    settings.check()
    transport = transport or outbound.transport(settings)
    db = Database(settings.database_url)
    db.migrate()
    auth = auth_module.Auth(settings, transport=transport)
    verifier = NativeVerifier(settings, transport=transport)

    app = FastAPI(
        title="The Vault API", version="1",
        description=(v1.__doc__ or "") + "\n\nCard data and images: Scryfall. Unofficial Fan Content "
                    "permitted under the Fan Content Policy. Not approved/endorsed by Wizards.",
        docs_url="/api/docs", openapi_url="/api/openapi.json",
    )
    app.state.db = db
    app.state.settings = settings

    @app.exception_handler(StarletteHTTPException)
    async def http_problem(request: Request, exc: StarletteHTTPException):
        res = problem(exc.status_code, str(exc.detail))
        for k, v in (exc.headers or {}).items():
            res.headers[k] = v
        return res

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError):
        return problem(422, "The request is not valid", errors=[
            {"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()])
    # Vercel caps a function response at 4.5 MB; the collection JSON compresses ~10x.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="vault_session",
        max_age=30 * 24 * 3600,
        # Apple POSTs the OAuth callback cross-site, so production needs SameSite=None (+Secure).
        same_site="none" if settings.secure_cookies else "lax",
        https_only=settings.secure_cookies,
    )

    account_marker_key = hashlib.sha256(b"vault-account-marker:" + settings.session_secret.encode()).digest()

    @app.middleware("http")
    async def account_marker(request: Request, call_next):
        """A readable cookie naming the signed-in account by an opaque keyed hash (not the id).
        The web app keys its offline copy by it, so after the account changes, by any route,
        an offline browser never shows the previous account's collection."""
        response = await call_next(request)
        session = request.scope.get("session") or {}
        uid = session.get("uid")
        # The account's session key is in it too: an account recreated with a reused id (a restored
        # database) gets a new marker, and so does every browser after "sign out everywhere".
        account = f"{uid}:{session.get('sk', '')}".encode()
        marker = hmac.new(account_marker_key, account, hashlib.sha256).hexdigest()[:32] if uid else None
        current = request.cookies.get(ACCOUNT_COOKIE)
        if marker and marker != current:
            response.set_cookie(ACCOUNT_COOKIE, marker, max_age=30 * 24 * 3600, path="/", samesite="lax",
                                secure=settings.secure_cookies, httponly=False)
        elif not marker and current and "session" in request.scope:
            response.delete_cookie(ACCOUNT_COOKIE, path="/", samesite="lax", secure=settings.secure_cookies)
        return response

    allowed_origin = _origin(settings.base_url)
    # Local development with twins: the twin Apple page POSTs the sign-in back from the twin server.
    cross_site_origins = {_origin(settings.twins_url)} if settings.twins_url else set()

    @app.middleware("http")
    async def reject_cross_site_writes(request: Request, call_next):
        """CSRF protection. The session cookie is SameSite=None in production (Apple's
        form_post callback needs it), so browsers would attach it to a form another site
        submits to us. Browsers always send Origin on such requests: refuse foreign ones."""
        origin = request.headers.get("origin")
        if (
            request.method in UNSAFE_METHODS
            and origin
            and _origin(origin) != allowed_origin
            and not request.url.path.startswith(CROSS_SITE_ALLOWED)
            and _origin(origin) not in cross_site_origins
        ):
            return problem(403, "Cross-site request refused")
        return await call_next(request)

    def get_db():
        yield from db.session()

    def optional_user(request: Request, session: Session = Depends(get_db)) -> User | None:
        """The caller: a bearer token (native apps, or a personal access token for agents) or
        the web session cookie. ``request.state.scopes`` says what the caller may do."""
        header = request.headers.get("authorization", "")
        request.state.scopes = {"read", "write", "account"}
        if header[:7].lower() == "bearer ":
            bearer = header[7:].strip()
            if tokens.is_pat(bearer):
                found = tokens.authenticate_pat(session, bearer)
                user, request.state.scopes = found if found else (None, set())
            else:
                user = tokens.authenticate(session, bearer)
            if user is not None:
                request.state.bearer = bearer
            return user
        return auth_module.session_user(session, request)

    def current_user(request: Request, user: User | None = Depends(optional_user)) -> User:
        if user is None:
            bearer = request.headers.get("authorization", "")[:7].lower() == "bearer "
            raise HTTPException(401, "Invalid or expired access token" if bearer else "Sign in required",
                                headers={"WWW-Authenticate": 'Bearer error="invalid_token"' if bearer else "Bearer"})
        scopes = request.state.scopes
        writes = request.method in UNSAFE_METHODS and request.url.path not in READ_ONLY_POSTS
        if writes and "write" not in scopes:
            raise HTTPException(403, "This access token is read-only. Create one with the write scope to make changes.",
                                headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="write"'})
        if not writes and "read" not in scopes:
            raise HTTPException(403, "This access token can't read. Create one with the read scope.",
                                headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="read"'})
        return user

    def account_user(request: Request, user: User = Depends(current_user)) -> User:
        """Account-level actions (creating tokens, deleting the account) need the person, signed in
        on the web or in the app. Personal access tokens can't do them."""
        if "account" not in request.state.scopes:
            raise HTTPException(403, "Personal access tokens can't manage the account. Use the web or iOS app.")
        return user

    app.include_router(auth_module.build_router(auth, get_db))
    app.include_router(v1.build_router(get_db, current_user, optional_user, settings, verifier,
                                       lambda: auth.offered, transport, account_user))
    app.include_router(passkeys.build_router(settings, get_db, auth_module.sign_in, account_user))
    app.include_router(mcp.build_router(optional_user))
    app.include_router(meta.build_router(get_db, settings))

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    # Local development: serve the front end too. On Vercel, public/ is served by the CDN.
    if serve_static and PUBLIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="web")
    return app
