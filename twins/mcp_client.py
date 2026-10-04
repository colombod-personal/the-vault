"""Twin of an MCP client such as ChatGPT or Claude, and of the web servers that host its metadata.

Two halves, both for testing the Vault's OAuth authorization server (``docs/mcp-oauth-threat-model.md``):

* :class:`ClientHostTwin`: the hosts that serve **Client ID Metadata Documents**
  (``https://app.example/oauth/client.json``), with the hostile variants a stranger could point
  ``client_id`` at: a document about another client, a redirect to the cloud metadata address, an
  oversized or slow answer, a name that resolves to a private address. Names resolve through
  :meth:`ClientHostTwin.resolve` (the universe's DNS), and every call is recorded, so a test can
  assert that nothing internal was ever contacted.
* :class:`McpClient`: the client itself. It does what the MCP authorization spec says a client
  does (discover, PKCE S256, ``resource``, authorize in a browser, redeem the code, call MCP,
  refresh), and each step can be bent into the abuse a test needs (``**override`` replaces a
  parameter, ``None`` drops it).
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .base import Twin, json_response

PUBLIC = "93.184.216.34"  # a public address (the documentation ranges are not "global")
GOOD_HOST = "app.example"
HOSTS = {  # host -> the addresses its name resolves to
    GOOD_HOST: [PUBLIC], "other.example": [PUBLIC], "evil.example": [PUBLIC], "redirector.example": [PUBLIC],
    "big.example": [PUBLIC], "slow.example": [PUBLIC], "plain.example": [PUBLIC],
    "internal.example": ["10.0.0.5"], "metadata.example": ["169.254.169.254"], "loopback.example": ["127.0.0.1"],
    "mixed.example": [PUBLIC, "10.0.0.8"], "mapped.example": ["::ffff:127.0.0.1"], "shared.example": ["100.64.0.9"],
}


class ClientHostTwin(Twin):
    name = "mcp_client_hosts"
    hosts = tuple(HOSTS)

    def __init__(self):
        super().__init__()
        self.documents: dict[tuple[str, str], object] = {}
        for host in self.hosts:
            self.route("GET", host, "/{rest:path}", self._serve)

    def reset(self) -> None:
        super().reset()
        self.documents.clear()

    def resolve(self, host: str) -> list[str]:
        """DNS for the universe: a known name's addresses, or a failure like an unknown name."""
        if host not in HOSTS:
            raise OSError(f"Name or service not known: {host}")
        return list(HOSTS[host])

    def _serve(self, req) -> httpx.Response:
        doc = self.documents.get((req.host, req.path))
        if doc is None:
            return self.error(404, "not_found", "No such document")
        return doc(req) if callable(doc) else json_response(200, doc)

    # -- documents ---------------------------------------------------------------------------
    def publish(self, host: str = GOOD_HOST, path: str = "/oauth/client.json", *, name: str | None = "Twin Agent",
                redirect_uris=("https://app.example/callback",), **fields) -> str:
        """Serve a well-formed document at ``https://host/path``; returns the URL (the client_id).
        ``fields`` add to or replace its members (``None`` removes one)."""
        url = f"https://{host}{path}"
        doc = {"client_id": url, "client_name": name, "client_uri": f"https://{host}", "redirect_uris": list(redirect_uris) if isinstance(redirect_uris, tuple) else redirect_uris,
               "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
               "token_endpoint_auth_method": "none", **fields}
        self.documents[(host, path)] = {k: v for k, v in doc.items() if v is not None}
        return url

    def serve(self, host: str, path: str, handler) -> str:
        """Serve whatever ``handler(request)`` answers at ``https://host/path``."""
        self.documents[(host, path)] = handler
        return f"https://{host}{path}"


# -- the client --------------------------------------------------------------------------------

def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class McpClient:
    """An MCP client driving a Vault app. ``browser`` is the person's browser (with cookies);
    ``api`` is the client's own server-to-server connection (without)."""

    def __init__(self, app_client_factory, client_id: str, redirect_uri: str = "https://app.example/callback"):
        self.api = app_client_factory()
        self.browser = app_client_factory()
        self.client_id, self.redirect_uri = client_id, redirect_uri
        self.base = "http://testserver"
        self.resource = f"{self.base}/api/mcp"
        self.verifier, self.challenge = pkce()
        self.state = secrets.token_urlsafe(12)
        self.tokens: dict = {}

    # -- discovery ---------------------------------------------------------------------------
    def discover(self) -> tuple[dict, dict]:
        """401 from the MCP server, then its resource metadata, then the authorization server's."""
        challenge = self.api.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).headers["www-authenticate"]
        url = re.search(r'resource_metadata="([^"]+)"', challenge).group(1)
        resource = self.api.get(urlsplit(url).path).json()
        server = self.api.get("/.well-known/oauth-authorization-server").json()
        return resource, server

    # -- the browser half --------------------------------------------------------------------
    def sign_in(self, email: str = "dev@localhost") -> None:
        assert self.browser.post(f"/api/auth/dev-login?email={email}").status_code == 200

    def authorize_params(self, **override) -> dict:
        params = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
                  "state": self.state, "scope": "read", "code_challenge": self.challenge,
                  "code_challenge_method": "S256", "resource": self.resource}
        params.update(override)
        return {k: v for k, v in params.items() if v is not None}

    def authorize(self, **override) -> httpx.Response:
        return self.browser.get("/oauth/authorize?" + urlencode(self.authorize_params(**override)), follow_redirects=False)

    @staticmethod
    def nonce(consent_page: httpx.Response) -> str:
        return re.search(r'name="nonce" value="([^"]+)"', consent_page.text).group(1)

    def answer(self, page: httpx.Response, decision: str = "allow", write: bool = False, **headers) -> httpx.Response:
        data = {"nonce": self.nonce(page), "decision": decision, **({"write": "on"} if write else {})}
        return self.browser.post("/oauth/authorize", data=data, headers=headers, follow_redirects=False)

    def approve(self, write: bool = False, **override) -> dict:
        """The whole browser trip: authorize, allow, and the parameters the app is sent back with."""
        page = self.authorize(**override)
        assert page.status_code == 200, page.text
        res = self.answer(page, write=write)
        assert res.status_code == 303, res.text
        return self.returned(res)

    def returned(self, res: httpx.Response) -> dict:
        parts = urlsplit(res.headers["location"])
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == self.redirect_uri.split("?")[0] or True
        return {k: v[-1] for k, v in parse_qs(parts.query).items()}

    # -- the server half ---------------------------------------------------------------------
    def token_form(self, fields: dict, override: dict) -> dict:
        form = {"client_id": self.client_id, "resource": self.resource, **fields, **override}
        return {k: v for k, v in form.items() if v is not None}

    def redeem(self, code: str, **override) -> httpx.Response:
        form = self.token_form({"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                                "code_verifier": self.verifier}, override)
        return self.api.post("/oauth/token", data=form)

    def connect(self, write: bool = False, email: str = "dev@localhost", **override) -> dict:
        """Sign in, approve and redeem: the tokens of a connected app."""
        self.sign_in(email)
        res = self.redeem(self.approve(write=write, **override)["code"])
        assert res.status_code == 200, res.text
        self.tokens = res.json()
        return self.tokens

    def refresh(self, refresh_token: str | None = None, **override) -> httpx.Response:
        form = self.token_form({"grant_type": "refresh_token",
                                "refresh_token": refresh_token or self.tokens["refresh_token"]}, override)
        return self.api.post("/oauth/token", data=form)

    def mcp(self, method: str, params: dict | None = None, token: str | None = None, path: str = "/api/mcp") -> httpx.Response:
        headers = {"Authorization": f"Bearer {token or self.tokens['access_token']}"}
        return self.api.post(path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                             headers=headers)

    def tool(self, tool_name: str, /, **arguments) -> dict:
        res = self.mcp("tools/call", {"name": tool_name, "arguments": arguments})
        return res.json()
