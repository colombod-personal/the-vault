"""Verify ID tokens that native SDKs hand to the iOS app.

Sign in with Apple (AuthenticationServices) and Google Sign-In for iOS give the app a
signed OpenID Connect ID token. The app sends it to ``POST /api/v1/auth/native/{provider}``;
we check the signature against the provider's published keys, the issuer, the audience
(the iOS app's bundle id / iOS client id), expiry and, when given, the nonce.
"""

from __future__ import annotations

import hashlib
import time

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from .config import Settings

DISCOVERY = {
    "apple": "https://appleid.apple.com/.well-known/openid-configuration",
    "google": "https://accounts.google.com/.well-known/openid-configuration",
}
ISSUERS = {
    "apple": {"https://appleid.apple.com"},
    "google": {"https://accounts.google.com", "accounts.google.com"},
}
CACHE_SECONDS = 3600


class NativeTokenError(Exception):
    pass


class ProviderUnavailable(Exception):
    """The provider's keys couldn't be fetched; the app should retry later."""


class NativeVerifier:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.audiences = {
            "apple": {a for a in (settings.apple_app_bundle_id, settings.apple_client_id) if a},
            "google": {a for a in (settings.google_ios_client_id, settings.google_client_id) if a},
        }
        self.transport = transport
        self._keys: dict[str, tuple[float, KeySet]] = {}

    @property
    def enabled(self) -> list[str]:
        return [p for p, auds in self.audiences.items() if auds]

    async def _keyset(self, provider: str, refresh: bool = False) -> KeySet:
        cached = self._keys.get(provider)
        if cached and not refresh and time.time() - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=10) as client:
                meta = (await client.get(DISCOVERY[provider])).raise_for_status().json()
                jwks = (await client.get(meta["jwks_uri"])).raise_for_status().json()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ProviderUnavailable(f"Could not reach {provider}: {exc}") from exc
        keys = KeySet.import_key_set(jwks)
        self._keys[provider] = (time.time(), keys)
        return keys

    async def verify(self, provider: str, id_token: str, nonce: str | None = None) -> dict:
        if provider not in self.audiences or not self.audiences[provider]:
            raise NativeTokenError(f"Native sign-in with {provider} is not configured")
        try:
            try:
                token = jwt.decode(id_token, await self._keyset(provider))
            except JoseError:  # maybe the provider rotated its keys
                token = jwt.decode(id_token, await self._keyset(provider, refresh=True))
        except (JoseError, ValueError) as exc:
            raise NativeTokenError(f"Invalid ID token: {exc}") from exc
        claims = token.claims
        now = time.time()
        aud = claims.get("aud")
        auds = set(aud) if isinstance(aud, list) else {aud}
        if claims.get("iss") not in ISSUERS[provider]:
            raise NativeTokenError("Wrong issuer")
        if not auds & self.audiences[provider]:
            raise NativeTokenError("Token was issued for another app")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - 60:
            raise NativeTokenError("Token expired")
        if nonce is not None:
            # Apple puts SHA-256(nonce) in the token; Google puts the nonce itself.
            expected = {nonce, hashlib.sha256(nonce.encode()).hexdigest()}
            if claims.get("nonce") not in expected:
                raise NativeTokenError("Nonce mismatch")
        if not claims.get("sub"):
            raise NativeTokenError("Token has no subject")
        return claims
