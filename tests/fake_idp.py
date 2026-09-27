"""A fake identity provider that impersonates Google, Microsoft, Apple and Facebook.

It is plugged into the app as an httpx transport, so the real sign-in code runs
unchanged against it: discovery documents, JWKS, the token endpoint (signed ID
tokens with the nonce the app sent) and Facebook's Graph ``/me``.
"""

from __future__ import annotations

import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from joserfc import jwt
from joserfc.jwk import RSAKey

FAKE = "https://idp.test"
METADATA = {
    "https://accounts.google.com/.well-known/openid-configuration": ("google", "https://accounts.google.com"),
    "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration":
        ("microsoft", "https://login.microsoftonline.com/{tenantid}/v2.0"),
    "https://appleid.apple.com/.well-known/openid-configuration": ("apple", "https://appleid.apple.com"),
}
ISSUERS = {"google": "https://accounts.google.com", "apple": "https://appleid.apple.com"}


class FakeIdP:
    def __init__(self):
        self.key = RSAKey.generate_key(2048, parameters={"kid": "fake-1"})
        self.codes: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.token_requests: list[dict] = []
        self.transport = httpx.MockTransport(self.handle)

    # -- what the "user" does at the provider ------------------------------------------
    def authorize(self, location: str, *, sub: str, email: str | None = None, name: str | None = None,
                  claims: dict | None = None) -> dict:
        """Approve the sign-in the app redirected to. Returns the query the provider sends back."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        code = secrets.token_urlsafe(8)
        self.codes[code] = {"query": q, "sub": sub, "email": email, "name": name, "claims": claims or {}}
        return {"code": code, "state": q["state"]}

    # -- the provider's endpoints ------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url in METADATA:
            provider, issuer = METADATA[url]
            return httpx.Response(200, json={
                "issuer": issuer,
                "authorization_endpoint": f"{FAKE}/{provider}/authorize",
                "token_endpoint": f"{FAKE}/{provider}/token",
                "jwks_uri": f"{FAKE}/jwks",
                "id_token_signing_alg_values_supported": ["RS256"],
                "response_modes_supported": ["query", "form_post"],
            })
        if url == f"{FAKE}/jwks":
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if url.endswith("/token"):
            provider = url.split("/")[-2]
            return self._token(provider, request)
        if url.startswith("https://graph.facebook.com/") and url.endswith("/oauth/access_token"):
            return self._token("facebook", request)
        if url.startswith("https://graph.facebook.com/") and url.endswith("/me"):
            token = request.headers["authorization"].split()[-1]
            grant = self.tokens[token]
            return httpx.Response(200, json={"id": grant["sub"], "name": grant["name"], "email": grant["email"]})
        return httpx.Response(404, json={"error": "unknown fake endpoint", "url": url})

    def _token(self, provider: str, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append({"provider": provider, **form})
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        access = secrets.token_urlsafe(8)
        self.tokens[access] = grant
        body = {"access_token": access, "token_type": "Bearer", "expires_in": 3600}
        if provider != "facebook":
            q = grant["query"]
            now = int(time.time())
            claims = {
                "iss": ISSUERS.get(provider), "aud": q["client_id"], "sub": grant["sub"],
                "iat": now, "exp": now + 600, "nonce": q.get("nonce"),
            }
            if grant["email"]:
                claims["email"] = grant["email"]
            if grant["name"] and provider != "apple":  # Apple never puts the name in the token
                claims["name"] = grant["name"]
            if provider == "microsoft":
                claims["tid"] = "9188040d-6c67-4c5b-b112-36a304b66dad"
                claims["iss"] = f"https://login.microsoftonline.com/{claims['tid']}/v2.0"
            claims.update(grant["claims"])
            body["id_token"] = jwt.encode({"alg": "RS256", "kid": "fake-1"}, claims, self.key)
        return httpx.Response(200, content=json.dumps(body), headers={"content-type": "application/json"})

    # -- tokens a native SDK (Sign in with Apple, Google Sign-In for iOS) would hand the app --
    def native_id_token(self, provider: str, *, sub: str, aud: str, nonce: str | None = None,
                        email: str | None = None, key: RSAKey | None = None, **claims) -> str:
        now = int(time.time())
        body = {"iss": ISSUERS[provider], "aud": aud, "sub": sub, "iat": now, "exp": now + 600}
        if nonce is not None:
            body["nonce"] = nonce
        if email:
            body["email"] = email
        body.update(claims)
        return jwt.encode({"alg": "RS256", "kid": "fake-1"}, body, key or self.key)
