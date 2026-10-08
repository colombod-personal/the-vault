"""Client authentication at the token endpoint with ``private_key_jwt`` (RFC 7523, OpenID Connect Core 9), for
clients identified by a Client ID Metadata Document that publishes its keys (ChatGPT does: issue #210).

A public client (``token_endpoint_auth_method: none``, claude.ai) proves itself with PKCE alone, as before. A client
whose document declares ``private_key_jwt`` must also sign a short-lived assertion with a key listed at its
``jwks_uri`` (same host as its client_id), and the Vault checks, before any code or refresh token is looked at:

- the signature, with the algorithm the document declared (never ``none`` or an HMAC algorithm);
- ``iss`` and ``sub`` are the client_id, ``aud`` names this token endpoint (or the issuer);
- ``exp`` is in the future and at most :data:`MAX_LIFETIME` away, so a leaked assertion is soon useless;
- ``jti`` was never used before (each assertion works once).

Keys are fetched with the SSRF-hardened :class:`vault.oauth_clients.ClientFetcher`, cached for an hour, and fetched
again (at most once a minute per URL) when an assertion names a key id the cache does not have: key rotation works
without letting anyone make the Vault fetch on demand.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from sqlalchemy.orm import Session

from .oauth_clients import ClientError, ClientFetcher
from .ratelimit import hit

JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
ALGORITHMS = ("RS256", "PS256", "ES256")  # asymmetric only: a client's public keys can't forge its signature
MAX_LIFETIME = 600  # seconds an assertion may live
SKEW = 60  # seconds of clock difference tolerated
MAX_ASSERTION = 8192  # bytes
KEYS_TTL = 3600
REFETCH_AFTER = 60  # an unknown key id fetches the keys again, at most this often per URL


def _refuse(why: str) -> ClientError:
    return ClientError("invalid_client", f"Client authentication failed: {why}")


def unverified_issuer(assertion: str) -> str | None:
    """The ``iss`` an assertion claims, read without checking it: only to find which client to check it for."""
    try:
        part = assertion.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    issuer = claims.get("iss") if isinstance(claims, dict) else None
    return issuer if isinstance(issuer, str) else None


class KeyCache:
    """Each client's published keys, per ``jwks_uri``, shared by the requests of one process."""

    def __init__(self) -> None:
        self._keys: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, fetcher: ClientFetcher, uri: str, kid: str | None, now: float) -> KeySet:
        with self._lock:
            cached = self._keys.get(uri)
        fresh = cached is not None and now - cached[0] < KEYS_TTL
        known = cached is not None and (kid is None or any(k.get("kid") == kid for k in cached[1].get("keys", [])))
        if not fresh or (not known and now - cached[0] >= REFETCH_AFTER):
            try:
                document = fetcher.fetch(uri)
            except ClientError:
                if cached is None:
                    raise _refuse("the client's keys could not be fetched") from None
                document = cached[1]  # keep working on the last keys if the client's server is briefly away
            else:
                if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
                    raise _refuse("the client's jwks_uri does not hold a key set")
                with self._lock:
                    self._keys[uri] = (now, document)
            cached = (now, document)
        public = {"keys": [k for k in cached[1]["keys"] if isinstance(k, dict) and "d" not in k]}  # never a private key
        try:
            return KeySet.import_key_set(public)
        except (JoseError, ValueError, TypeError) as exc:
            raise _refuse("the client's keys can't be read") from exc


def verify(db: Session, fetcher: ClientFetcher, cache: KeyCache, *, client_id: str, jwks_uri: str, algorithm: str,
           assertion_type: str | None, assertion: str | None, audiences: tuple[str, ...], now: float | None = None) -> None:
    """Accept the client's assertion, or raise ``invalid_client``. Marks its ``jti`` used (the caller commits)."""
    now = time.time() if now is None else now
    if assertion_type != JWT_BEARER or not assertion:
        raise _refuse("this client must authenticate with a signed client_assertion (private_key_jwt)")
    if len(assertion) > MAX_ASSERTION:
        raise _refuse("the assertion is too large")
    if algorithm not in ALGORITHMS:
        raise _refuse("the client declared an algorithm the Vault does not accept")
    try:
        header = json.loads(base64.urlsafe_b64decode(assertion.split(".")[0] + "=" * (-len(assertion.split(".")[0]) % 4)))
    except (ValueError, UnicodeDecodeError, IndexError, RecursionError):
        raise _refuse("the assertion is not a JWT") from None
    if not isinstance(header, dict) or header.get("alg") != algorithm:
        raise _refuse(f"the assertion must be signed with {algorithm}")
    kid = header.get("kid") if isinstance(header.get("kid"), str) else None
    keys = cache.get(fetcher, jwks_uri, kid, now)
    try:
        token = jwt.decode(assertion, keys, algorithms=[algorithm])
    except (JoseError, ValueError) as exc:
        raise _refuse("the signature does not match the client's keys") from exc
    claims = token.claims
    if claims.get("iss") != client_id or claims.get("sub") != client_id:
        raise _refuse("iss and sub must be the client_id")
    aud = claims.get("aud")
    if not any(a in audiences for a in ([aud] if isinstance(aud, str) else aud if isinstance(aud, list) else [])):
        raise _refuse("aud must be this token endpoint")
    exp, jti = claims.get("exp"), claims.get("jti")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp < now - SKEW:
        raise _refuse("the assertion has expired")
    if exp > now + MAX_LIFETIME + SKEW:
        raise _refuse(f"the assertion lives too long (at most {MAX_LIFETIME} seconds)")
    iat = claims.get("iat")
    if isinstance(iat, (int, float)) and not isinstance(iat, bool) and iat > now + SKEW:
        raise _refuse("the assertion was issued in the future")
    if not isinstance(jti, str) or not 8 <= len(jti) <= 200:
        raise _refuse("the assertion needs a unique jti")
    # Single use: one counter row per (client, jti), kept at least until the assertion expires.
    key = hashlib.sha256(f"client-assertion:{client_id}:{jti}".encode()).hexdigest()
    if hit(db, key, int(exp // 60) + 1) > 1:
        raise _refuse("this assertion was already used")
