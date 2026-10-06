"""Who is asking: OAuth clients of the Vault's MCP authorization server.

Two ways for an AI app (ChatGPT, Claude, an IDE, a script) to identify itself:

* **Client ID Metadata Documents** (``draft-ietf-oauth-client-id-metadata-document``): the
  ``client_id`` is an https URL, and the document at that URL names the app and lists its redirect
  URIs. The Vault fetches it, which is a request to an address a stranger chose, so
  :class:`ClientFetcher` is built against SSRF: https on port 443 only, no IP literals, every
  address the name resolves to must be a public one, the connection goes to the address that was
  checked (no DNS rebinding), redirects are refused, and size and time are capped.
* **Dynamic Client Registration** (RFC 7591), as a constrained fallback: public clients only,
  validated redirect URIs, a per-IP rate limit (in the route), a cap on how many exist, and
  registrations that are never used expire after a day.

Redirect URIs are matched exactly, except loopback ones (``http://127.0.0.1:port/...``, RFC 8252)
whose port may differ. Anything else must be https.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import secrets
import socket
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import OAuthClient, OAuthGrant
from .ratelimit import hit

log = logging.getLogger(__name__)

MAX_DOCUMENT = 32 * 1024  # bytes read from a metadata document
FETCH_SECONDS = 5.0  # for the whole fetch, however slowly the server drips
CACHE_TTL = timedelta(hours=1)  # a fetched document is trusted this long
UNUSED_TTL = timedelta(days=1)  # a registration (or cached document) nobody used
USED_TTL = timedelta(days=90)
MAX_REDIRECT_URIS = 10
MAX_URL = 512
MAX_NAME = 80
STALE_MAX = timedelta(days=7)  # a cached document older than the cache TTL is still served when fetching is not possible
FETCH_FAILED = "The app's metadata document could not be fetched"  # the one message for every fetch-stage failure
BLANKS = "\u2800\u3164\u115f\u1160\uffa0\u180e"  # letters and symbols that draw nothing
MAX_CONCURRENT_FETCHES = 8  # per process: a flood of metadata URLs can not tie up every worker
LOOPBACK_HOSTS = ("127.0.0.1", "[::1]", "localhost")
LOOPBACK_NAMES = ("127.0.0.1", "::1", "localhost")


def _refuse(detail: str) -> ClientError:
    """The error for any failure while fetching a document. The caller gets one message whatever went
    wrong (an answer that differed for private addresses, redirects and bad content would tell a
    stranger what the Vault can reach); the detail goes to the log, which holds no secrets."""
    log.warning("client metadata refused: %s", detail)
    return ClientError("invalid_client", FETCH_FAILED)


class ClientError(Exception):
    """The client can't be identified or its metadata is not acceptable. ``code`` is the OAuth
    error to report; ``description`` is safe to show (it never carries what was fetched)."""

    def __init__(self, code: str, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# -- redirect URIs ---------------------------------------------------------------------------

def _parts(uri: str):
    try:
        return urlsplit(uri)
    except ValueError:
        return None


def is_loopback(uri: str) -> bool:
    parts = _parts(uri)
    return bool(parts and parts.scheme == "http" and parts.hostname in LOOPBACK_NAMES)


def valid_redirect_uri(uri) -> bool:
    """https, or http to the loopback interface (a desktop app listening on a port). No
    fragments, no credentials, no control characters, no custom schemes."""
    if (not isinstance(uri, str) or not uri or len(uri) > 2000 or not uri.isascii() or "%5c" in uri.lower()
            or re.search(r"[\x00-\x20\x7f\\]", uri)):
        return False
    parts = _parts(uri)
    if parts is None or parts.fragment or parts.username or parts.password or not parts.hostname:
        return False
    try:
        parts.port  # noqa: B018  (raises ValueError for a bad port)
    except ValueError:
        return False
    return (parts.scheme == "https" or is_loopback(uri)) and "@" not in parts.netloc


def match_redirect(registered: list[str], given: str) -> str | None:
    """The address to send the browser to for a requested ``redirect_uri``, or None.

    The requested string is validated strictly first (:func:`valid_redirect_uri`: no backslash,
    control characters, userinfo, fragment or encoded backslash), because different parsers read a
    sloppy string differently, and a browser may go somewhere this parser did not see. Then it must
    equal a registered URI exactly. For a loopback URI (RFC 8252) only the port may differ: the
    target is **rebuilt from the registered scheme, host, path and query plus the requested port**,
    and the request is refused unless it is exactly that rebuilt string, so what is checked is
    what is used."""
    if not valid_redirect_uri(given):
        return None
    if given in registered:
        return given
    if not is_loopback(given):
        return None
    wanted = _parts(given)
    for uri in registered:
        known = _parts(uri)
        if known and is_loopback(uri) and known.hostname == wanted.hostname and (known.path, known.query) == (wanted.path, wanted.query):
            host = f"[{known.hostname}]" if ":" in known.hostname else known.hostname
            port = f":{wanted.port}" if wanted.port else ""
            target = f"{known.scheme}://{host}{port}{known.path}" + (f"?{known.query}" if known.query else "")
            if target == given:
                return target
    return None


def redirect_matches(registered: list[str], given: str) -> bool:
    return match_redirect(registered, given) is not None


def display_host(host: str) -> tuple[str, str]:
    """(Unicode form, ASCII/punycode form) of a host name, so an address that only looks like
    another one (a homograph) shows its technical form next to the readable one."""
    try:
        ascii_form = host.encode("idna").decode("ascii")
    except UnicodeError:
        ascii_form = host
    try:
        readable = ascii_form.encode("ascii").decode("idna")
    except UnicodeError:
        readable = ascii_form
    return readable, ascii_form


def clean_name(value) -> str:
    """A display name the consent screen can show: printable, one line, short."""
    if not isinstance(value, str):
        return ""
    kept = []
    for ch in value:
        kind = unicodedata.category(ch)
        if kind == "Cc":
            kept.append(" ")  # a control character separates words
        elif kind[0] == "C" or ch in BLANKS:
            continue  # format characters (soft hyphen, zero-width, bidi, tag characters), surrogates, private use: invisible, dropped
        else:
            kept.append(ch)
    return " ".join("".join(kept).split())[:MAX_NAME]


def _redirect_list(value) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_REDIRECT_URIS or not all(valid_redirect_uri(u) for u in value):
        raise ClientError("invalid_redirect_uri", f"redirect_uris must list 1 to {MAX_REDIRECT_URIS} https "
                          "(or http loopback) URIs without fragments")
    return list(dict.fromkeys(value))


# -- Client ID Metadata Documents ------------------------------------------------------------

def is_metadata_client_id(client_id: str) -> bool:
    return client_id.startswith("https://")


def check_client_id_url(url: str) -> str:
    """The host of a client_id URL that is safe to fetch, or ClientError."""
    parts = _parts(url)
    bad = ClientError("invalid_client", "client_id is not an acceptable https URL")
    if (parts is None or len(url) > MAX_URL or re.search(r"[\x00-\x20\x7f\\]", url) or parts.scheme != "https"
            or not parts.hostname or parts.username or parts.password or parts.fragment
            or parts.path in ("", "/") or "/." in parts.path):
        raise bad
    try:
        port = parts.port
    except ValueError:
        raise bad from None
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
        raise bad  # an address literal, never a name
    except ValueError:
        pass
    if port not in (None, 443) or "." not in host or host.endswith((".local", ".internal", ".localhost")):
        raise bad
    return host


def public_address(value: str) -> bool:
    """True for an address on the public internet. IPv4-mapped, NAT64 and 6to4 forms are judged
    by the IPv4 address they carry; Teredo and anything private, loopback, link-local, shared
    (100.64/10), multicast, reserved or unspecified is refused."""
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return public_address(str(ip.ipv4_mapped))
        if ip in ipaddress.ip_network("64:ff9b::/96"):
            return public_address(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
        if ip in ipaddress.ip_network("2002::/16"):
            return public_address(str(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)))
        if ip in ipaddress.ip_network("2001::/32"):
            return False
    return ip.is_global and not ip.is_multicast


def system_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


class ClientFetcher:
    """Fetches client metadata documents safely. ``transport`` and ``resolver`` replace the network
    and DNS in tests (the twin universe's). Without a transport, the connection is made to the
    address that was checked, with the host name kept for TLS (SNI and certificate) and the Host
    header, so DNS can't answer differently between the check and the connection."""

    def __init__(self, transport: httpx.BaseTransport | None = None, resolver=None, *, pin: bool | None = None,
                 seconds: float = FETCH_SECONDS, max_bytes: int = MAX_DOCUMENT):
        self.transport = transport
        self.resolver = resolver or system_resolver
        self.pin = transport is None if pin is None else pin
        self.seconds = seconds
        self.max_bytes = max_bytes
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_FETCHES)

    def addresses(self, host: str) -> list[str]:
        try:
            found = self.resolver(host)
        except OSError:
            raise _refuse(f"{host} does not resolve") from None
        if not found or not all(public_address(a) for a in found):
            raise _refuse(f"{host} resolves to a non-public address")
        return found

    def prepare(self, url: str) -> tuple[str, list[str]]:
        """Check the URL's shape and classify its host (every address public). Costs nothing and uses no
        slot, so a URL that is refused here never spends the shared budgets."""
        host = check_client_id_url(url)
        return host, self.addresses(host)

    def fetch(self, url: str) -> dict:
        host, found = self.prepare(url)
        return self.fetch_prepared(url, host, found)

    def fetch_prepared(self, url: str, host: str, found: list[str]) -> dict:
        if not self._slots.acquire(blocking=False):
            raise ClientError("temporarily_unavailable", "Too many apps are being checked right now; try again in a moment")
        try:
            return self._fetch_pinned(url, host, found)
        finally:
            self._slots.release()

    def _fetch_pinned(self, url: str, host: str, found: list[str]) -> dict:
        deadline = time.monotonic() + self.seconds
        for address in (found if self.pin else [None]):
            try:
                return self._get(url, host, address, deadline)
            except httpx.TransportError as exc:
                log.warning("client metadata fetch failed: %s", type(exc).__name__)
        raise _refuse("no address answered")

    def _get(self, url: str, host: str, address: str | None, deadline: float) -> dict:
        target, extensions, headers = url, {}, {"Accept": "application/json", "User-Agent": "TheVault-client-metadata/1"}
        if address is not None:
            target = str(httpx.URL(url).copy_with(host=address))
            extensions["sni_hostname"] = host
            headers["Host"] = host
        with httpx.Client(transport=self.transport, timeout=httpx.Timeout(self.seconds), follow_redirects=False,
                          trust_env=False) as client:
            with client.stream("GET", target, headers=headers, extensions=extensions) as res:
                if res.status_code != 200:  # redirects included: a redirect could lead anywhere
                    raise _refuse(f"status {res.status_code} (redirects are not followed)")
                if not res.headers.get("content-type", "").lower().startswith("application/json"):
                    raise _refuse("not application/json")
                declared = res.headers.get("content-length", "0")
                if declared.isdigit() and int(declared) > self.max_bytes:
                    raise _refuse("too large")
                body = b""
                for chunk in res.iter_bytes():
                    body += chunk
                    if len(body) > self.max_bytes:
                        raise _refuse("too large")
                    if time.monotonic() > deadline:
                        raise _refuse("too slow")
        if time.monotonic() > deadline:
            raise _refuse("too slow")
        try:
            return json.loads(body)
        except ValueError:
            raise _refuse("not valid JSON") from None


SIGNING_ALGORITHMS = ("RS256", "PS256", "ES256")  # for private_key_jwt (vault.client_auth); asymmetric only


@dataclass(frozen=True)
class TokenAuth:
    """How a client authenticates at the token endpoint: ``none`` (public, PKCE only) or ``private_key_jwt``."""

    method: str = "none"
    jwks_uri: str | None = None
    algorithm: str | None = None


def _token_auth(url: str, doc: dict) -> TokenAuth:
    method = doc.get("token_endpoint_auth_method", "none")
    if method == "none":
        return TokenAuth()
    if method != "private_key_jwt":
        raise ClientError("invalid_client", "Supported token_endpoint_auth_method values: none, private_key_jwt")
    algorithm = doc.get("token_endpoint_auth_signing_alg", "RS256")
    if algorithm not in SIGNING_ALGORITHMS:
        raise ClientError("invalid_client", f"token_endpoint_auth_signing_alg must be one of {', '.join(SIGNING_ALGORITHMS)}")
    jwks_uri = doc.get("jwks_uri")
    if not isinstance(jwks_uri, str) or len(jwks_uri) > MAX_URL:
        raise ClientError("invalid_client", "private_key_jwt needs a jwks_uri (inline jwks are not supported)")
    # The keys must live where the client_id does: the same SSRF rules, and the same host.
    if check_client_id_url(jwks_uri) != check_client_id_url(url):
        raise ClientError("invalid_client", "The jwks_uri must be on the same host as the client_id")
    return TokenAuth("private_key_jwt", jwks_uri, algorithm)


def parse_document(url: str, doc) -> tuple[str, list[str], TokenAuth]:
    """(name, redirect URIs, token authentication) of a metadata document, if it is about ``url`` and acceptable."""
    if not isinstance(doc, dict) or doc.get("client_id") != url:
        raise ClientError("invalid_client", "The metadata document is not about this client_id")
    name = clean_name(doc.get("client_name"))
    if not name:
        raise ClientError("invalid_client", "The metadata document has no client_name")
    auth = _token_auth(url, doc)
    return name, _redirect_list(doc.get("redirect_uris")), auth


# -- registry --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Limits:
    """Caps on what anonymous requests can make the Vault store or fetch."""

    dcr_cap: int = 2000  # registered clients
    cimd_cap: int = 5000  # cached metadata documents
    fetch_per_minute: int = 60  # metadata fetches, all callers together
    fetch_per_caller: int = 10  # metadata fetches a minute for one caller (a keyed hash of the IP)


def _make_room(db: Session, cap: int, kind: str) -> None:
    """Forget clients nobody used in time (and no grant uses), and refuse a new ``kind`` of client past
    ``cap``. Registrations and cached documents are counted apart, so neither can crowd out the other;
    at the cap, the oldest unused cached documents are dropped first (they are fetched again when used)."""
    in_use = select(OAuthGrant.client_id)
    db.execute(delete(OAuthClient).where(OAuthClient.expires_at < _now(), OAuthClient.client_id.not_in(in_use)))
    count = lambda: db.scalar(select(func.count(OAuthClient.id)).where(OAuthClient.kind == kind))  # noqa: E731
    if count() >= cap and kind == "cimd":
        oldest = (select(OAuthClient.id).where(OAuthClient.kind == "cimd", OAuthClient.client_id.not_in(in_use))
                  .order_by(OAuthClient.fetched_at).limit(max(1, cap // 10)))
        db.execute(delete(OAuthClient).where(OAuthClient.id.in_(oldest)))
    if count() >= cap:
        raise ClientError("temporarily_unavailable", "Too many clients are registered; try again later")


def _spend_fetch_budget(db: Session, per_minute: int, scope: str = "") -> None:
    """One fetch of a stranger's URL, counted for everyone together (``scope`` empty) or for one caller.
    The per-caller budget is much smaller than the shared one, so one address can not use all of it."""
    key = hashlib.sha256(b"oauth-metadata-fetch:" + scope.encode()).hexdigest()
    used = hit(db, key, int(time.time() // 60))
    db.commit()
    if used > per_minute:
        raise ClientError("temporarily_unavailable", "Too many apps are being checked right now; try again in a minute")


def _store_cimd(db: Session, client_id: str, name: str, uris: list[str], cap: int, auth: TokenAuth = TokenAuth()) -> OAuthClient:
    """Save a fetched document. Two first fetches of one URL at once both reach the insert: the
    unique index lets one win, and the other updates the winner's row."""
    for _ in range(2):
        row = db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
        try:
            if row is None:
                _make_room(db, cap, "cimd")
                row = OAuthClient(client_id=client_id, kind="cimd")
                db.add(row)
            row.name, row.redirect_uris, row.fetched_at, row.expires_at = name, uris, _now(), _now() + UNUSED_TTL
            row.token_auth, row.jwks_uri, row.auth_alg = auth.method, auth.jwks_uri, auth.algorithm
            db.commit()
            return row
        except IntegrityError:
            db.rollback()
    raise ClientError("temporarily_unavailable", "Could not save the app's details; try again")


def _cimd_client(db: Session, fetcher: ClientFetcher, client_id: str, limits: Limits, caller: str = "") -> OAuthClient:
    row = db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))
    cached = row is not None and row.kind == "cimd" and row.fetched_at is not None
    if cached and _now() - _aware(row.fetched_at) < CACHE_TTL:
        return row
    stale = row if cached and _now() - _aware(row.fetched_at) < STALE_MAX else None
    db.commit()  # end the read transaction: the connection goes back to the pool while a stranger's server answers
    host, found = fetcher.prepare(client_id)  # refused URLs and hosts cost nothing: no budget, no slot
    try:
        if caller:
            _spend_fetch_budget(db, limits.fetch_per_caller, caller)  # first: one caller over its share spends none of the shared one
        _spend_fetch_budget(db, limits.fetch_per_minute)
        document = fetcher.fetch_prepared(client_id, host, found)
    except ClientError as exc:
        if exc.code == "temporarily_unavailable" and stale is not None:
            return stale  # out of budget or slots: an app seen before keeps working on what was fetched last
        raise
    name, uris, auth = parse_document(client_id, document)
    return _store_cimd(db, client_id, name, uris, limits.cimd_cap, auth)


def resolve_client(db: Session, fetcher: ClientFetcher, client_id: str, limits: Limits = Limits(),
                   caller: str = "") -> OAuthClient:
    """The client for a ``client_id``: a metadata document URL (fetched, cached an hour) or a
    registered id. Unknown ids and ids that are neither answer the same ``invalid_client``."""
    if not client_id or len(client_id) > MAX_URL:
        raise ClientError("invalid_client", "Unknown client_id")
    if is_metadata_client_id(client_id):
        return _cimd_client(db, fetcher, client_id, limits, caller)
    row = db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id, OAuthClient.kind == "dcr"))
    if row is None or (row.expires_at and _aware(row.expires_at) < _now()):
        raise ClientError("invalid_client", "Unknown client_id")
    return row


def known_client(db: Session, client_id: str) -> OAuthClient | None:
    """A client already in the registry (no fetching): enough at the token endpoint, where the
    code is already bound to the client."""
    return db.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))


def mark_used(db: Session, client: OAuthClient) -> None:
    """A registration someone actually used lives longer."""
    client.expires_at = _now() + USED_TTL
    db.commit()


def register(db: Session, body, cap: int) -> dict:
    """RFC 7591: a public client registering itself. Returns the registration response."""
    if not isinstance(body, dict):
        raise ClientError("invalid_client_metadata", "The registration must be a JSON object")
    uris = _redirect_list(body.get("redirect_uris"))
    if body.get("token_endpoint_auth_method", "none") != "none":
        raise ClientError("invalid_client_metadata", "Only public clients are supported: token_endpoint_auth_method none")
    grants = body.get("grant_types", ["authorization_code"])
    if not isinstance(grants, list) or not grants or not set(grants) <= {"authorization_code", "refresh_token"}:
        raise ClientError("invalid_client_metadata", "grant_types may be authorization_code and refresh_token")
    if body.get("response_types", ["code"]) != ["code"]:
        raise ClientError("invalid_client_metadata", "response_types must be [\"code\"]")
    name = clean_name(body.get("client_name")) or "Unnamed app"
    _make_room(db, cap, "dcr")
    client = OAuthClient(client_id="vault_client_" + secrets.token_urlsafe(24), kind="dcr", name=name,
                         redirect_uris=uris, expires_at=_now() + UNUSED_TTL)
    db.add(client)
    db.commit()
    return {"client_id": client.client_id, "client_id_issued_at": int(_aware(client.created_at).timestamp()),
            "client_name": name, "redirect_uris": uris, "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none"}
