"""A file a chat app hands to a tool by address: fetched safely (vault-0n9.3).

ChatGPT passes an attached file to a tool as ``{download_url, file_id, mime_type?, file_name?}`` when the tool declares
``_meta["openai/fileParams"]``. The Vault then **fetches an address the caller chose**, which is an SSRF surface, so every
step here is built against it (docs/mcp-oauth-threat-model.md, "A file dropped in the chat"):

* the value is parsed strictly: https on port 443, no credentials, no fragment, no address literal, 4096 characters at most;
* the host name must belong to the allow-list (``FILE_INTAKE_HOSTS``: a domain and its subdomains); a host that is refused is
  logged by **name only**, never with its path or query, which carry the token;
* the name is resolved here and **every** address must be public (the same rule as the OAuth metadata fetcher:
  ``oauth_clients.public_address``: private, loopback, link-local, shared 100.64/10, multicast, metadata, IPv4-mapped
  and NAT64 forms), and the connection goes to the address that was checked, with the name kept for TLS and the Host
  header, so DNS cannot answer differently between the check and the connection;
* at most 2 redirects, each one checked against exactly the same rules (a redirect to another host must be on the
  allow-list too, and resolve to public addresses);
* at most ``INTAKE_MAX_BYTES`` (5 MB) read, in plain bytes (``Accept-Encoding: identity``; a compressed answer is refused), and 15
  seconds in total, as a wall clock the caller waits on, however slowly the server drips;
* a text-like content type only; no cookies, no authorization, no referrer: a bare GET;
* the address is never logged and never returned, and no error repeats it (HTTP client libraries log requests at INFO: that is
  switched off here).

Nothing in this module touches the database or the person's collection: it returns bytes, or raises ``Refused``.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .config import DEFAULT_FILE_INTAKE_HOSTS, DOMAIN, parse_hosts
from .oauth_clients import interleaved, public_address, system_resolver

# httpx logs "HTTP Request: GET <url>" at INFO, and the address carries the file's token (and, pinned, it is the IP).
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

FETCH_SECONDS = 15.0
# What the intake takes from a chat: about what production already handles (the upload page is cut near 4 MB by the platform). The
# importer's own cap is 20 MB; a 17.9 MB, 190,000-row file measured 8.9 s and about 450 MB of memory for one request (review of PR
# #478), and a read-only connection may make 10 such calls a minute, so the intake does not take it.
INTAKE_MAX_BYTES = 5 * 1024 * 1024
MAX_URL = 4096  # signed links are long
MAX_REDIRECTS = 2
MAX_ADDRESSES = 3  # of a host's addresses: so many unreachable records cannot hold a fetch for minutes
MAX_CONCURRENT = 4  # per process
DEFAULT_HOSTS = parse_hosts(DEFAULT_FILE_INTAKE_HOSTS)
TEXT_TYPES = ("text/csv", "text/plain", "application/csv", "application/x-csv", "text/x-csv", "text/comma-separated-values",
              "application/vnd.ms-excel")  # besides anything text/*
GENERIC_TYPES = ("application/octet-stream", "binary/octet-stream")  # storage hosts answer these for any file
REDIRECTS = (301, 302, 303, 307, 308)

# What the caller (the intake route) tells the model, by ``Refused.kind``. Plain sentences, never the address or any detail.
CANNOT_HAND_OVER = ("This app cannot hand the file to the Vault yet; nothing was changed, and 'Import CSV' on the Vault website "
                    "works with the same file.")


class Refused(Exception):
    """The Vault will not (or could not) take the file. ``kind`` picks what the person is told; ``reason`` and ``host`` go to the
    server log (never the address, never what was fetched)."""

    def __init__(self, reason: str, *, kind: str = "fetch_failed", host: str | None = None, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind  # cannot_hand_over | fetch_failed | too_large | not_text | busy
        self.host = host
        self.status = status


@dataclass(frozen=True)
class Handoff:
    """What the host gave: only ``url`` is used to fetch; the rest is declared metadata, never trusted."""

    url: str
    file_id: str | None = None
    mime_type: str | None = None
    file_name: str | None = None


@dataclass(frozen=True)
class Fetched:
    content: bytes
    content_type: str


# -- what the host gave ----------------------------------------------------------------------------------------------

def _printable(text: str, limit: int) -> str:
    return re.sub(r"[^ -~]", "?", text[:limit])


KNOWN_KEYS = ("download_url", "file_id", "mime_type", "file_name", "name", "id", "url", "path", "size", "type", "file")
SENTINELS = ("chat_upload",)  # the one string a host is known to send in place of a file (ChatGPT mobile, issue #185): kept as it is


def mask(text: str) -> str:
    """The form of a string with its content gone: letters become a or A, digits 9, anything else ``?`` (``-``, ``_`` and ``.``
    stay): enough to tell a label, a UUID and a signed token apart in the log, and nothing that can be replayed."""
    return re.sub(r"[^-_.aA9]", "?", re.sub(r"[0-9]", "9", re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", text))))


def shape_of(value) -> dict:
    """The shape of a value, for the log: its type, its length and a prefix (a known sentinel as it is, anything else as its form), and
    nothing that could be a secret.
    A web address is recorded as its scheme only; a path as its root only (a path may name the person); a bare string whole only
    when it is the known sentinel (``chat_upload``), else as the form of its first 12 characters (``mask``);
    an object by the names of its keys when they are the ones a file object has, and by how many others there are."""
    if isinstance(value, str):
        low = value.lower()
        if low.startswith("https://"):
            kind, prefix = "https", "https://"
        elif low.startswith("http://"):
            kind, prefix = "http", "http://"
        elif low.startswith("file://"):
            kind, prefix = "file_uri", "file://"
        elif re.match(r"^[A-Za-z]:[\\/]", value):
            kind, prefix = "windows_path", value[:2]
        elif value.startswith(("/", "\\")):
            kind, prefix = "posix_path", value[:1]
        elif "://" in value:
            kind, prefix = "other_scheme", mask(value.split("://", 1)[0][:12]) + "://"  # a scheme is caller-chosen text too
        else:
            kind, prefix = "token", value if value in SENTINELS else mask(value[:12])
        return {"type": "str", "kind": kind, "prefix": prefix, "length": len(value)}
    if isinstance(value, dict):
        names = [str(k) for k in value]
        out: dict = {"type": "object", "keys": sorted(k for k in names if k in KNOWN_KEYS),
                     "other_keys": sum(1 for k in names if k not in KNOWN_KEYS)}
        if "download_url" in value:
            out["download_url"] = shape_of(value["download_url"])
        return out
    if isinstance(value, list):
        return {"type": "array", "length": len(value), "first": shape_of(value[0]) if value else None}
    return {"type": "null" if value is None else type(value).__name__}


def read_handoff(value) -> Handoff:
    """The ``file`` argument as a Handoff, or ``Refused(kind="cannot_hand_over")`` for anything that is not an object with a web
    address: the string a phone app sends ('chat_upload'), a local path, a missing key."""
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict) or not isinstance(value.get("download_url"), str) or not value["download_url"]:
        raise Refused("no web address in the file value", kind="cannot_hand_over")

    def text(key: str, limit: int) -> str | None:
        found = value.get(key)
        return found[:limit] if isinstance(found, str) and found else None

    return Handoff(value["download_url"], text("file_id", 200), text("mime_type", 100), text("file_name", 300))


def clean_filename(name: str | None) -> str:
    """The name to show for the staged file: the declared file name without any path, and without control, format (bidirectional
    overrides, zero-width), line-separator or unassigned characters; runs of blanks become one. It is still the app's own text, and
    every place that shows it treats it as data, never as an instruction."""
    base = re.split(r"[\\/]", name or "")[-1]
    kept = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C" and unicodedata.category(ch) not in ("Zl", "Zp"))
    base = " ".join(kept.split())
    return (base if base not in (".", "..") and base else "chat-upload.csv")[:255]


# -- the allow-list ----------------------------------------------------------------------------------------------------


def host_allowed(host: str, hosts: tuple[str, ...]) -> bool:
    return any(host == entry or host.endswith("." + entry) for entry in hosts)


def check_url(url: str, hosts: tuple[str, ...]) -> str:
    """The host name of an address that may be fetched, or ``Refused``. Pure: nothing is resolved or contacted."""
    bad = Refused("address is not an acceptable https address", kind="cannot_hand_over")
    if len(url) > MAX_URL or re.search(r"[\x00-\x20\x7f\\]", url):
        raise bad
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise bad from None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username is not None or parts.password is not None or parts.fragment or "@" in parts.netloc:
        raise Refused("not https, or has credentials", kind="cannot_hand_over")
    if port not in (None, 443):
        raise Refused("not port 443", kind="cannot_hand_over", host=_host_for_log(host))
    try:
        ipaddress.ip_address(host.strip("[]"))
        raise Refused("an address literal, not a name", kind="cannot_hand_over")
    except ValueError:
        pass
    if not host.isascii() or "." not in host or host.endswith(".") or not DOMAIN.match(host):
        raise Refused("not a plain host name", kind="cannot_hand_over", host=_host_for_log(host))
    if not host_allowed(host, hosts):
        raise Refused("host is not on the allow-list", kind="cannot_hand_over", host=_host_for_log(host))
    return host


def _host_for_log(host: str) -> str:
    """A host name as it may go to the log: printable, and only its last three labels (``*.`` marks what was left off), so a
    name that carries a secret in its first labels (``<token>.files.example.com``) does not reach the log. Never a path or a
    query. Three labels keep what the first real run needs: ``files.oaiusercontent.com`` is shown whole."""
    labels = re.sub(r"[^a-z0-9.\-]", "?", host.lower()).split(".")
    return ("*." if len(labels) > 3 else "") + ".".join(labels[-3:])[:80]


def text_like(content_type: str, handoff: Handoff) -> bool:
    """Whether the type the file host answered with is one a CSV or a text export has. A generic binary type counts only when
    the host's own description of the file says text (a .csv name, or a text type): storage hosts often answer octet-stream."""
    kind = content_type.split(";")[0].strip().lower()
    if kind == "text/html":  # a storage host's error page, not a file
        return False
    if kind.startswith("text/") or kind in TEXT_TYPES:
        return True
    if kind in GENERIC_TYPES:
        declared = (handoff.mime_type or "").split(";")[0].strip().lower()
        named = (handoff.file_name or "").lower().endswith((".csv", ".txt", ".tsv"))
        return declared.startswith("text/") or declared in TEXT_TYPES or named
    return False


# -- the fetch ---------------------------------------------------------------------------------------------------------

class FileFetcher:
    """``transport`` and ``resolver`` replace the network and DNS in tests. Without a transport, the connection is made to the
    address that was checked (``pin``)."""

    def __init__(self, hosts: tuple[str, ...] = DEFAULT_HOSTS, transport: httpx.BaseTransport | None = None, resolver=None, *,
                 pin: bool | None = None, seconds: float = FETCH_SECONDS, max_bytes: int = INTAKE_MAX_BYTES):
        self.hosts = hosts
        self.transport = transport
        self.resolver = resolver or system_resolver
        self.pin = transport is None if pin is None else pin
        self.seconds = seconds
        self.max_bytes = max_bytes
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    # -- checks that cost nothing ------------------------------------------------------------------------------------

    def host_of(self, url: str) -> str:
        return check_url(url, self.hosts)

    def addresses(self, host: str) -> list[str]:
        try:
            found = self.resolver(host)
        except OSError:
            raise Refused("host does not resolve", host=_host_for_log(host)) from None
        if not found or not all(public_address(a) for a in found):
            raise Refused("host resolves to a non-public address", kind="cannot_hand_over", host=_host_for_log(host))
        return list(found)

    # -- the fetch itself --------------------------------------------------------------------------------------------

    def fetch(self, handoff: Handoff) -> Fetched:
        """The file's bytes, or ``Refused``. Runs the whole fetch (the DNS lookup too) in a worker the caller waits on for at
        most ``seconds``, so a host that drips its headers or never answers a lookup cannot keep the request."""
        self.host_of(handoff.url)  # the shape and the allow-list first: a refused address starts nothing
        if not self._slots.acquire(blocking=False):
            raise Refused("every fetch slot is busy", kind="busy")
        outcome: dict = {}

        def work() -> None:
            try:
                outcome["fetched"] = self._follow(handoff, time.monotonic() + self.seconds)
            except Refused as exc:
                outcome["refused"] = exc
            except Exception as exc:  # noqa: BLE001  (the class name is all that is kept: a message can carry the address)
                outcome["error"] = type(exc).__name__
            finally:
                self._slots.release()

        worker = threading.Thread(target=work, daemon=True, name="file-intake-fetch")
        worker.start()
        worker.join(self.seconds + 0.5)
        if "fetched" in outcome:
            return outcome["fetched"]
        if "refused" in outcome:
            raise outcome["refused"]
        if "error" in outcome:
            raise Refused(f"fetch failed: {outcome['error']}")
        raise Refused("too slow")  # the worker is still running; it ends at its own timeouts and gives its slot back

    def _follow(self, handoff: Handoff, deadline: float) -> Fetched:
        url = handoff.url
        for hop in range(MAX_REDIRECTS + 1):
            host = self.host_of(url) if hop == 0 else self._redirect_host(url)
            found = self.addresses(host)
            result = self._get(url, host, found, deadline, handoff)
            if isinstance(result, Fetched):
                return result
            url = result  # a redirect target, absolute: checked in full at the top of the loop
        raise Refused("too many redirects")

    def _redirect_host(self, url: str) -> str:
        try:
            return check_url(url, self.hosts)
        except Refused as exc:  # the same rules, and the target's host name is what the log needs
            exc.kind = "fetch_failed"
            exc.reason = "redirect to " + exc.reason
            raise

    def _get(self, url: str, host: str, found: list[str], deadline: float, handoff: Handoff) -> Fetched | str:
        for address in (interleaved(found)[:MAX_ADDRESSES] if self.pin else [None]):
            if time.monotonic() >= deadline:
                break
            try:
                return self._request(url, host, address, deadline, handoff)
            except httpx.TransportError:
                continue
        raise Refused("no address answered in time", host=_host_for_log(host))

    def _request(self, url: str, host: str, address: str | None, deadline: float, handoff: Handoff) -> Fetched | str:
        # Plain bytes only: a compressed answer is decoded a whole chunk at a time, so a few KB could become tens of MB before the
        # size limit looks at it. No cookie, no authorization, no referrer.
        target, extensions = url, {}
        headers = {"Accept": "text/csv,text/plain;q=0.9,*/*;q=0.1", "User-Agent": "TheVault-file-intake/1",
                   "Accept-Encoding": "identity"}
        if address is not None:
            target = str(httpx.URL(url).copy_with(host=address))
            extensions["sni_hostname"] = host
            headers["Host"] = host
        left = max(deadline - time.monotonic(), 0.05)
        with httpx.Client(transport=self.transport, timeout=httpx.Timeout(left), follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", target, headers=headers, extensions=extensions) as res:
                if res.status_code in REDIRECTS:
                    where = res.headers.get("location", "")
                    if not where:
                        raise Refused("redirect without a place", host=_host_for_log(host), status=res.status_code)
                    return str(httpx.URL(url).join(where))
                if res.status_code != 200:
                    raise Refused("the host did not give the file", host=_host_for_log(host), status=res.status_code)
                content_type = res.headers.get("content-type", "")
                if not text_like(content_type, handoff):
                    raise Refused("not a text type", kind="not_text", host=_host_for_log(host))
                if res.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                    raise Refused("Content-Encoding is not identity", host=_host_for_log(host))
                declared = res.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_bytes:
                    raise Refused("too large", kind="too_large", host=_host_for_log(host))
                body = bytearray()
                for chunk in res.iter_bytes():  # no decoder: a Content-Encoding other than identity was refused above
                    body += chunk
                    if len(body) > self.max_bytes:
                        raise Refused("too large", kind="too_large", host=_host_for_log(host))
                    if time.monotonic() > deadline:
                        raise Refused("too slow", host=_host_for_log(host))
        return Fetched(bytes(body), _printable(content_type.split(";")[0].strip().lower(), 60))
