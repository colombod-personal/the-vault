"""Settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./vault.db"))
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET", "dev-insecure-secret"))
    base_url: str = field(default_factory=lambda: _env("BASE_URL", "http://localhost:8000").rstrip("/"))
    dev_login: bool = field(default_factory=lambda: _env("DEV_LOGIN") in ("1", "true", "yes"))

    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET"))
    microsoft_client_id: str = field(default_factory=lambda: _env("MICROSOFT_CLIENT_ID"))
    microsoft_client_secret: str = field(default_factory=lambda: _env("MICROSOFT_CLIENT_SECRET"))
    facebook_client_id: str = field(default_factory=lambda: _env("FACEBOOK_CLIENT_ID"))
    facebook_client_secret: str = field(default_factory=lambda: _env("FACEBOOK_CLIENT_SECRET"))
    facebook_graph_version: str = field(default_factory=lambda: _env("FACEBOOK_GRAPH_VERSION", "v23.0"))
    apple_client_id: str = field(default_factory=lambda: _env("APPLE_CLIENT_ID"))
    apple_team_id: str = field(default_factory=lambda: _env("APPLE_TEAM_ID"))
    apple_key_id: str = field(default_factory=lambda: _env("APPLE_KEY_ID"))
    apple_private_key: str = field(default_factory=lambda: _env("APPLE_PRIVATE_KEY").replace("\\n", "\n"))

    # Native iOS app
    apple_app_bundle_id: str = field(default_factory=lambda: _env("APPLE_APP_BUNDLE_ID"))  # native Sign in with Apple
    google_ios_client_id: str = field(default_factory=lambda: _env("GOOGLE_IOS_CLIENT_ID"))  # Google Sign-In for iOS
    # Where a browser sign-in may hand over to an app (custom scheme or universal link), comma-separated.
    app_redirect_uris: tuple[str, ...] = field(default_factory=lambda: tuple(
        u.strip() for u in _env("APP_REDIRECT_URIS", "vault://auth").split(",") if u.strip()))

    # Local development only: send every outbound call to the digital twin universe (python -m twins).
    twins_url: str = field(default_factory=lambda: _env("VAULT_TWINS_URL"))

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    def check(self) -> None:
        if os.environ.get("VERCEL") and self.database_url.startswith("sqlite"):
            raise RuntimeError(
                "DATABASE_URL is not set. Connect a Neon Postgres database to the Vercel project "
                "(Storage -> Neon) and redeploy."
            )
        if self.secure_cookies and self.session_secret == "dev-insecure-secret":
            raise RuntimeError("SESSION_SECRET must be set when BASE_URL is https")
        if self.twins_url and (self.secure_cookies or os.environ.get("VERCEL")):
            raise RuntimeError("VAULT_TWINS_URL is for local development only")
        if self.secure_cookies and self.dev_login:
            raise RuntimeError("DEV_LOGIN must not be enabled on a public deployment")
