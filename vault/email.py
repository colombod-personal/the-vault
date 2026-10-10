"""Sending e-mail, behind one small interface (#347).

The Vault sends exactly one kind of mail: the one-time code and link that confirm it is the person before a serious account
action (``vault.recent_signin``). Nothing else is sent, and nothing is sent unless ``RESEND_API_KEY`` is set: with no key
:func:`make_sender` returns ``None`` and the feature offers the passkey and linked-sign-in paths only.

``EmailSender`` is what the rest of the code knows. :class:`ResendSender` is the only real implementation: one HTTPS call to
Resend's API (``POST https://api.resend.com/emails``, bearer key, JSON), five seconds, and one retry on a network error or a
server-side failure, made with the same ``Idempotency-Key`` so a retry after a lost answer cannot send twice. A rejected
request (a bad key, an unverified domain, a quota) is not retried. The key is never logged; neither is the message.
Tests and local development reach a twin of Resend (``twins/resend.py``) through ``transport``: nothing contacts the network.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
TIMEOUT_SECONDS = 5.0
RETRY_STATUSES = frozenset({500, 502, 503, 504})  # Resend's own failure: worth one more try (429 and 4xx are answers, not faults)


class EmailError(Exception):
    """The mail was not sent. ``retryable`` is true for a fault that may pass (the service or the network)."""

    def __init__(self, reason: str, *, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailSender(Protocol):
    def send(self, message: EmailMessage) -> None:
        """Send ``message`` or raise :class:`EmailError`."""


class ResendSender:
    def __init__(self, api_key: str, from_address: str, transport: httpx.BaseTransport | None = None,
                 timeout: float = TIMEOUT_SECONDS):
        self._key = api_key
        self.from_address = from_address
        self._transport = transport
        self._timeout = timeout

    def send(self, message: EmailMessage) -> None:
        body = {"from": self.from_address, "to": [message.to], "subject": message.subject, "text": message.text,
                **({"html": message.html} if message.html else {})}
        headers = {"Authorization": f"Bearer {self._key}", "Idempotency-Key": secrets.token_hex(16)}
        failure: EmailError | None = None
        for attempt in (1, 2):  # one try and one retry, no more
            try:
                with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                    response = client.post(RESEND_URL, json=body, headers=headers)
            except httpx.HTTPError as exc:
                failure = EmailError(f"could not reach the mail service ({type(exc).__name__})", retryable=True)
                continue
            if response.status_code < 300:
                return
            reason = self._reason(response)
            if response.status_code not in RETRY_STATUSES:
                raise EmailError(reason, retryable=response.status_code == 429)
            failure = EmailError(reason, retryable=True)
        log.warning("the mail service did not accept a message: %s", failure)
        raise failure  # type: ignore[misc]

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        """What Resend said, as its error name and status only: never the body (it can echo the address)."""
        try:
            name = response.json().get("name")
        except (ValueError, AttributeError):
            name = None
        return f"the mail service answered {response.status_code}" + (f" ({name})" if isinstance(name, str) and len(name) < 60 else "")


def make_sender(settings, transport: httpx.BaseTransport | None = None) -> EmailSender | None:
    """The sender for these settings, or ``None`` when no key is set (the feature is then off)."""
    if not settings.resend_api_key:
        return None
    return ResendSender(settings.resend_api_key, settings.email_from, transport)


def mask_address(address: str) -> str:
    """``ann@example.com`` -> ``***@e***.com``: enough for the person to know which mailbox, nothing that identifies it."""
    domain = address.rsplit("@", 1)[-1]
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    return f"***@{domain[:1]}***" + (f".{tld}" if tld else "")


def usable_address(address: str | None) -> bool:
    """Something that can be a mailbox: one @, text on both sides, no whitespace or control characters."""
    if not address or len(address) > 320 or address.count("@") != 1:
        return False
    local, domain = address.split("@")
    return bool(local and "." in domain and not any(c.isspace() or ord(c) < 32 or c in "<>,;\"" for c in address))
