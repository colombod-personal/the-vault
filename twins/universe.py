"""The universe: every twin, reachable by the real host names, and nothing else.

In process (tests), :attr:`Universe.transport` replaces the network for httpx clients, both sync
and async. A request to a host no twin owns fails like a network error and is recorded in
:attr:`Universe.escapes`, so a test can assert that nothing tried to reach the real internet.

Over HTTP (``python -m twins``), the same twins answer on ``/h/<real host>/<path>``; see
:mod:`twins.server`.
"""

from __future__ import annotations

import httpx

from .archidekt import ArchidektTwin
from .base import Twin
from .github import GitHubTwin
from .mcp_client import ClientHostTwin
from .neon import NeonTwin
from .identity import AppleTwin, FacebookTwin, GoogleTwin, IdentityTwin, MicrosoftTwin
from .scryfall import ScryfallTwin
from .spellbook import SpellbookTwin
from .vercel import VercelTwin
from .wizards import WizardsTwin


class Universe:
    def __init__(self, *, seed: bool = True, enforce_rate_limits: bool = False):
        self.google = GoogleTwin()
        self.microsoft = MicrosoftTwin()
        self.apple = AppleTwin()
        self.facebook = FacebookTwin()
        self.scryfall = ScryfallTwin(seed, enforce_rate_limits=enforce_rate_limits)
        self.archidekt = ArchidektTwin(self.scryfall)
        self.vercel = VercelTwin()
        self.spellbook = SpellbookTwin(seed)
        self.wizards = WizardsTwin()
        self.github = GitHubTwin()  # the repository issues the budget guard opens (jobs/budget_alert.py)
        self.neon = NeonTwin()  # the console API the monthly usage check reads (jobs/neon_usage.py)
        self.client_hosts = ClientHostTwin()  # where MCP clients publish their OAuth metadata documents
        self.twins: dict[str, Twin] = {t.name: t for t in (self.google, self.microsoft, self.apple, self.facebook,
                                                           self.scryfall, self.archidekt, self.vercel, self.spellbook, self.wizards,
                                                           self.github, self.neon, self.client_hosts)}
        self.by_host: dict[str, Twin] = {h: t for t in self.twins.values() for h in t.hosts}
        self.escapes: list[str] = []
        self.transport = httpx.MockTransport(self.handle)

    @property
    def identity(self) -> dict[str, IdentityTwin]:
        return {n: t for n, t in self.twins.items() if isinstance(t, IdentityTwin)}

    def handle(self, request: httpx.Request) -> httpx.Response:
        twin = self.by_host.get(request.url.host)
        if twin is None:
            self.escapes.append(str(request.url))
            raise httpx.ConnectError(f"{request.url.host} is outside the twin universe", request=request)
        return twin.handle(request)

    def resolve(self, host: str) -> list[str]:
        """DNS for the universe (the Vault's OAuth client-metadata fetcher uses it)."""
        return self.client_hosts.resolve(host)

    def client(self, **kwargs) -> httpx.Client:
        return httpx.Client(transport=self.transport, follow_redirects=True, **kwargs)

    def reset(self) -> None:
        for twin in self.twins.values():
            twin.reset()
        self.escapes.clear()

    def set_public_url(self, url: str | None) -> None:
        for twin in self.twins.values():
            twin.public_url = url

    def register_vault(self, settings, redirect_base: str | None = None, app_public_keys: dict | None = None) -> None:
        """Register the vault's OAuth apps with the identity twins, as you would in each
        provider's console: the client ids and secrets from ``settings``, and the redirect URI
        ``{BASE_URL}/api/auth/callback/{provider}``. Apple verifies the client-secret JWT with
        the public half of ``APPLE_PRIVATE_KEY``."""
        base = (redirect_base or settings.base_url).rstrip("/")
        cb = lambda p: {f"{base}/api/auth/callback/{p}"}  # noqa: E731
        s = settings
        if s.google_client_id:
            self.google.register_app(s.google_client_id, s.google_client_secret, cb("google"))
        if s.microsoft_client_id:
            self.microsoft.register_app(s.microsoft_client_id, s.microsoft_client_secret, cb("microsoft"))
        if s.facebook_client_id:
            self.facebook.register_app(s.facebook_client_id, s.facebook_client_secret, cb("facebook"))
        if s.apple_client_id and s.apple_private_key:
            from joserfc.jwk import ECKey

            public = ECKey.import_key(s.apple_private_key.replace("\\n", "\n")).as_pem(private=False).decode()
            self.apple.register_app(s.apple_client_id, None, cb("apple"), team_id=s.apple_team_id,
                                    key_id=s.apple_key_id, public_key=public)

    def state(self) -> dict:
        return {"twins": {n: t.state() for n, t in self.twins.items()}, "escapes": self.escapes[-20:]}
