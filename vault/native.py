"""Verify ID tokens that native SDKs hand to the iOS app.

Sign in with Apple (AuthenticationServices) and Google Sign-In for iOS give the app a
signed OpenID Connect ID token. The app sends it to ``POST /api/v1/auth/native/{provider}``;
we check the signature against the provider's published keys, the issuer, the audience
(the iOS app's bundle id / iOS client id), expiry and, when given, the nonce.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
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
REFRESH_COOLDOWN = 60  # seconds between early key refreshes (for a token with an unknown key id)


class NativeTokenError(Exception):
    pass


class ProviderUnavailable(Exception):
    """The provider's keys couldn't be fetched; the app should retry later."""


class NativeVerifier:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        # Only tokens made for the app. Web sign-in tokens (Apple's Services ID, Google's web
        # client) are never accepted here.
        self.audiences = {
            "apple": {a for a in (settings.apple_app_bundle_id,) if a},
            "google": {a for a in (settings.google_ios_client_id,) if a},
        }
        # Google Sign-In for iOS configured with a server client id issues tokens with that id as
        # `aud` and the iOS client as `azp`: accepted, but only with the iOS app as `azp`.
        self.google_server_audience = settings.google_client_id if settings.google_ios_client_id else None
        self.transport = transport
        self._keys: dict[str, tuple[float, KeySet]] = {}
        self._refreshed: dict[str, float] = {}  # provider -> when keys were last refetched early
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def enabled(self) -> list[str]:
        return [p for p, auds in self.audiences.items() if auds]

    async def _keyset(self, provider: str, refresh: bool = False) -> KeySet:
        asked = time.time()
        cached = self._keys.get(provider)
        if cached and not refresh and asked - cached[0] < CACHE_SECONDS:
            return cached[1]
        async with self._locks.setdefault(provider, asyncio.Lock()):  # one fetch at a time
            cached = self._keys.get(provider)
            if cached and (cached[0] >= asked if refresh else time.time() - cached[0] < CACHE_SECONDS):
                return cached[1]  # another request fetched them while this one waited
            return await self._fetch_keys(provider)

    async def _fetch_keys(self, provider: str) -> KeySet:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=10) as client:
                meta = (await client.get(DISCOVERY[provider])).raise_for_status().json()
                jwks = (await client.get(meta["jwks_uri"])).raise_for_status().json()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ProviderUnavailable(f"Could not reach {provider}: {exc}") from exc
        keys = KeySet.import_key_set(jwks)
        self._keys[provider] = (time.time(), keys)
        return keys

    async def verify(self, provider: str, id_token: str, nonce: str) -> dict:
        if not nonce:
            raise NativeTokenError("A nonce is required")
        if provider not in self.audiences or not self.audiences[provider]:
            raise NativeTokenError(f"Native sign-in with {provider} is not configured")
        try:
            keys = await self._keyset(provider)
            try:
                token = jwt.decode(id_token, keys)
            except JoseError:
                # Only a key id we don't have means the provider rotated its keys. Anything else
                # (a forged or broken token) is refused from the cached keys, so bad tokens can't
                # make us call the provider; and an early refresh happens at most once a minute.
                kid = _key_id(id_token)
                if not kid or _has_kid(keys, kid) or time.time() - self._refreshed.get(provider, 0) < REFRESH_COOLDOWN:
                    raise
                self._refreshed[provider] = time.time()
                token = jwt.decode(id_token, await self._keyset(provider, refresh=True))
        except (JoseError, ValueError) as exc:
            raise NativeTokenError(f"Invalid ID token: {exc}") from exc
        claims = token.claims
        now = time.time()
        aud = claims.get("aud")
        auds = set(aud) if isinstance(aud, list) else {aud}
        if claims.get("iss") not in ISSUERS[provider]:
            raise NativeTokenError("Wrong issuer")
        if not aud or not all(isinstance(a, str) and a for a in auds):
            raise NativeTokenError("Token has no audience")
        via_server_id = (provider == "google" and self.google_server_audience is not None
                         and self.google_server_audience in auds
                         and claims.get("azp") in self.audiences["google"])
        if not (auds & self.audiences[provider] or via_server_id):
            raise NativeTokenError("Token was issued for another app")
        # A token for several audiences must also have been issued to the app (Google's rule).
        if provider == "google" and len(auds) > 1 and claims.get("azp") not in self.audiences["google"]:
            raise NativeTokenError("Token was issued to another party")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - 60:
            raise NativeTokenError("Token expired")
        # Apple puts SHA-256(nonce) in the token; Google puts the nonce itself. The caller then
        # records the nonce as used (NativeNonce), so the same token can't sign in twice.
        expected = {nonce, hashlib.sha256(nonce.encode()).hexdigest()}
        if claims.get("nonce") not in expected:
            raise NativeTokenError("Nonce mismatch")
        if not claims.get("sub"):
            raise NativeTokenError("Token has no subject")
        return claims


def _key_id(token: str) -> str | None:
    """The ``kid`` in a JWT's header, read without trusting anything else in it."""
    try:
        header = token.split(".", 1)[0]
        kid = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4))).get("kid")
    except (ValueError, AttributeError, TypeError):
        return None
    return kid if isinstance(kid, str) else None


def _has_kid(keys: KeySet, kid: str) -> bool:
    return any(k.kid == kid for k in keys.keys)
