"""FastAPI application."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import auth as auth_module
from .config import Settings
from .db import Database
from .models import User
from .routes import api

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


def create_app(settings: Settings | None = None, *, serve_static: bool = True) -> FastAPI:
    settings = settings or Settings()
    settings.check()
    db = Database(settings.database_url)
    db.create_all()
    auth = auth_module.Auth(settings)

    app = FastAPI(title="The Vault", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.db = db
    app.state.settings = settings
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="vault_session",
        max_age=30 * 24 * 3600,
        # Apple POSTs the OAuth callback cross-site, so production needs SameSite=None (+Secure).
        same_site="none" if settings.secure_cookies else "lax",
        https_only=settings.secure_cookies,
    )

    def get_db():
        yield from db.session()

    def current_user(request: Request, session: Session = Depends(get_db)) -> User:
        uid = request.session.get("uid")
        user = session.get(User, uid) if uid else None
        if user is None:
            raise HTTPException(401, "Sign in required")
        return user

    app.include_router(auth_module.build_router(auth, get_db))
    app.include_router(api.build_router(get_db, current_user, settings))

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    # Local development: serve the front end too. On Vercel, public/ is served by the CDN.
    if serve_static and PUBLIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="web")
    return app
