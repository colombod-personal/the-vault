"""Settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _default_base_url() -> str:
    """BASE_URL if set. On Vercel it can be left out: production uses the project's production
    domain (its shortest custom domain, else its vercel.app domain), and previews use their
    branch URL. Locally the default is localhost."""
    explicit = _env("BASE_URL")
    if explicit:
        return explicit
    if os.environ.get("VERCEL_ENV") == "production" and _env("VERCEL_PROJECT_PRODUCTION_URL"):
        return "https://" + _env("VERCEL_PROJECT_PRODUCTION_URL")
    host = _env("VERCEL_BRANCH_URL") or _env("VERCEL_URL")
    return "https://" + host if host else "http://localhost:8000"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))  # Postgres, always
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET", "dev-insecure-secret"))
    base_url: str = field(default_factory=lambda: _default_base_url().rstrip("/"))
    dev_login: bool = field(default_factory=lambda: _env("DEV_LOGIN") in ("1", "true", "yes"))
    # The store reviewers' way in (vault.reviewer): unset, there is none. Set once by the owner, and the same value goes
    # into the directories' private credentials field.
    reviewer_passphrase: str = field(default_factory=lambda: _env("REVIEWER_PASSPHRASE"))
    # MCP Apps (#50): the server always records whether a client advertised the io.modelcontextprotocol/ui extension (and logs
    # it). With this on, a client that said it cannot show Apps gets no view links. Off by default until the logs show that
    # claude.ai and ChatGPT both advertise it, so a host that renders the views without advertising is never cut off.
    mcp_apps_require_capability: bool = field(default_factory=lambda: _env("MCP_APPS_REQUIRE_CAPABILITY") in ("1", "true", "yes"))

    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET"))
    microsoft_client_id: str = field(default_factory=lambda: _env("MICROSOFT_CLIENT_ID"))
    microsoft_client_secret: str = field(default_factory=lambda: _env("MICROSOFT_CLIENT_SECRET"))
    facebook_client_id: str = field(default_factory=lambda: _env("FACEBOOK_CLIENT_ID"))
    facebook_client_secret: str = field(default_factory=lambda: _env("FACEBOOK_CLIENT_SECRET"))
    facebook_graph_version: str = field(default_factory=lambda: _env("FACEBOOK_GRAPH_VERSION", "v23.0"))
    # configured providers left off the sign-in screen; their sign-in and linking still work
    hidden_providers: tuple[str, ...] = field(default_factory=lambda: tuple(
        p.strip().lower() for p in _env("AUTH_HIDDEN_PROVIDERS").split(",") if p.strip()))
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

    # Requests per minute per client IP to each sign-in endpoint (vault.ratelimit): starting a
    # sign-in, and the steps that check a credential or redeem a token.
    auth_rate_limit: int = field(default_factory=lambda: int(_env("AUTH_RATE_LIMIT", "30")))
    auth_verify_rate_limit: int = field(default_factory=lambda: int(_env("AUTH_VERIFY_RATE_LIMIT", "10")))
    # OAuth for MCP clients (vault.oauth_*): dynamic client registrations per minute per IP, and how many
    # unexpired self-registered clients may exist at once (storage cap).
    oauth_register_rate_limit: int = field(default_factory=lambda: int(_env("OAUTH_REGISTER_RATE_LIMIT", "20")))
    oauth_rate_limit: int = field(default_factory=lambda: int(_env("OAUTH_RATE_LIMIT", "120")))  # authorize, token, revoke
    oauth_client_cap: int = field(default_factory=lambda: int(_env("OAUTH_CLIENT_CAP", "2000")))
    oauth_cimd_cap: int = field(default_factory=lambda: int(_env("OAUTH_CIMD_CAP", "5000")))  # cached client metadata documents
    oauth_fetch_ip_limit: int = field(default_factory=lambda: int(_env("OAUTH_FETCH_IP_LIMIT", "10")))  # ... per caller
    oauth_fetch_limit: int = field(default_factory=lambda: int(_env("OAUTH_FETCH_LIMIT", "60")))  # metadata fetches a minute, all callers
    passkey_challenge_cap: int = field(default_factory=lambda: int(_env("PASSKEY_CHALLENGE_CAP", "10000")))
    # Calls per minute per user to POST /api/v1/collection/refresh (each fetches up to 300
    # printings from Scryfall, so a whole collection takes a few calls).
    refresh_rate_limit: int = field(default_factory=lambda: int(_env("REFRESH_RATE_LIMIT", "20")))
    # Behind Vercel's edge, which sets the client's address in x-forwarded-for / x-real-ip.
    on_vercel: bool = field(default_factory=lambda: bool(os.environ.get("VERCEL")))

    # Local development only: send every outbound call to the digital twin universe (python -m twins).
    twins_url: str = field(default_factory=lambda: _env("VAULT_TWINS_URL"))
    # Catalog lookups (cards, rulings, rules) without an account. Off until the registration question in
    # docs/compliance.md is decided: Wizards' Fan Content Policy says no registration to access its content.
    catalog_rate_limit: int = field(default_factory=lambda: int(_env("CATALOG_RATE_LIMIT", "60")))  # a minute, per person or per IP

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    def check(self) -> None:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. On Vercel, connect a Neon Postgres database to the project "
                "(Storage -> Neon) and redeploy; locally, see README -> Run it locally."
            )
        if os.environ.get("VERCEL") and not self.secure_cookies:
            # Secure cookies, the session-secret check and OAuth redirects all follow BASE_URL.
            raise RuntimeError("BASE_URL must be https on a Vercel deployment (or left unset)")
        if self.secure_cookies and (not self.session_secret.strip() or self.session_secret == "dev-insecure-secret"):
            raise RuntimeError("SESSION_SECRET must be set when BASE_URL is https")
        if self.twins_url and (self.secure_cookies or os.environ.get("VERCEL")):
            raise RuntimeError("VAULT_TWINS_URL is for local development only")
        if self.secure_cookies and self.dev_login:
            raise RuntimeError("DEV_LOGIN must not be enabled on a public deployment")
        if self.reviewer_passphrase and len(self.reviewer_passphrase) < 16:
            raise RuntimeError("REVIEWER_PASSPHRASE must be at least 16 characters (it is the way in for the stores' reviewers)")
