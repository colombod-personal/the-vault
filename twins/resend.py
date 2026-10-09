"""Twin of the slice of Resend's API that ``vault/email.py`` uses (``api.resend.com``): send one e-mail.

It records every accepted message in :attr:`ResendTwin.sent` so a test reads the code from the mail the person would have
received, and nothing leaves the process. What it enforces, from Resend's published documentation (read 2026-10-09; **not yet
compared with the live service**: the owner has not created a Resend account, so ``tests/conformance/test_resend_live.py`` is
written and waits for ``TWINS_LIVE_RESEND=1`` and a key; until it has run, treat this twin as the documentation's reading):

- ``POST /emails`` needs ``Authorization: Bearer <key>``: none answers 401 ``missing_api_key``, an unknown one 401 ``invalid_api_key``.
- ``from``, ``to`` (a string or a list of at most 50) and ``subject`` are required, and ``text`` or ``html``: otherwise 422
  ``missing_required_field`` (400/422 ``validation_error`` in the documentation; the Vault treats both as a rejected request).
- The sending domain must be verified for the key's team (``Resend`` rejects another with 403 ``validation_error``), except
  ``resend.dev``, which is Resend's sandbox for the owner's own address only.
- ``Idempotency-Key`` (at most 256 characters): the same key with the same body answers the first answer again and sends nothing
  more; the same key with another body answers 409 ``invalid_idempotent_request``.
- The free plan's quotas answer 429 ``daily_quota_exceeded`` (default 100 a day) and rate limits 429 ``rate_limit_exceeded``.
- An answer is ``{"id": "<uuid>"}``; an error is ``{"statusCode": 401, "name": "invalid_api_key", "message": "..."}``.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import httpx

from .base import Request, Twin, json_response

HOST = "api.resend.com"
DEV_KEY = "re_twin_dev_key"  # for `python -m twins` and local development: set RESEND_API_KEY to this and read the mails at /_twins/api/resend/sent


class ResendTwin(Twin):
    name = "resend"
    hosts = (HOST,)

    def __init__(self):
        super().__init__()
        self.api_keys: dict[str, set[str]] = {DEV_KEY: {"mtgvault.cards"}}  # key -> the domains verified for its team
        self.sent: list[dict] = []
        self.daily_quota = 100
        self._idempotent: dict[tuple[str, str], tuple[str, httpx.Response]] = {}
        self.route("POST", HOST, "/emails", self._send)

    # -- state ---------------------------------------------------------------------------
    def add_key(self, key: str, *, domains: tuple[str, ...] = ("mtgvault.cards",)) -> None:
        """A team's API key, with the domains the team verified (the DNS records Resend asks for)."""
        self.api_keys[key] = set(domains)

    def mails_to(self, address: str) -> list[dict]:
        return [m for m in self.sent if address.lower() in [t.lower() for t in m["to"]]]

    def reset(self) -> None:
        super().reset()
        self.sent.clear()
        self._idempotent.clear()
        self.daily_quota = 100

    def state(self) -> dict:
        return {**super().state(), "sent": len(self.sent), "keys": len(self.api_keys)}

    def error(self, status, code, details):
        return json_response(status, {"statusCode": status, "name": code, "message": details})

    # -- the API -------------------------------------------------------------------------
    def _send(self, req: Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        if not auth:
            return self.error(401, "missing_api_key", "Missing API key in the authorization header.")
        key = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else ""
        if key not in self.api_keys:
            return self.error(401, "invalid_api_key", "API key is invalid.")
        if "application/json" not in req.ctype:
            return self.error(422, "invalid_content_type", "Content-Type must be application/json.")
        try:
            body = req.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self.error(422, "invalid_json", "The request body is not valid JSON.")
        for field in ("from", "to", "subject"):
            if not body.get(field):
                return self.error(422, "missing_required_field", f"Missing `{field}` field.")
        if not body.get("text") and not body.get("html"):
            return self.error(422, "missing_required_field", "Missing `html` or `text` field.")
        recipients = [body["to"]] if isinstance(body["to"], str) else body["to"]
        if not isinstance(recipients, list) or not recipients or len(recipients) > 50 or not all(
                isinstance(r, str) and "@" in r for r in recipients):
            return self.error(422, "validation_error", "Invalid `to` field.")
        sender = body["from"] if isinstance(body["from"], str) else ""
        domain = sender.rsplit("@", 1)[-1].rstrip(">").strip().lower()
        if domain != "resend.dev" and domain not in self.api_keys[key]:
            return self.error(403, "validation_error", f"The {domain} domain is not verified. Please, add and verify your domain.")
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        idem = req.headers.get("idempotency-key")
        if idem is not None:
            if len(idem) > 256:
                return self.error(400, "validation_error", "The idempotency key must be at most 256 characters.")
            seen = self._idempotent.get((key, idem))
            if seen is not None:
                if seen[0] != fingerprint:
                    return self.error(409, "invalid_idempotent_request", "This idempotency key was used with a different request.")
                return seen[1]
        if len(self.sent) >= self.daily_quota:
            return self.error(429, "daily_quota_exceeded", "You have reached your daily email sending quota.")
        mail = {"id": str(uuid.uuid4()), "from": sender, "to": recipients, "subject": body["subject"],
                "text": body.get("text") or "", "html": body.get("html") or ""}
        self.sent.append(mail)
        response = json_response(200, {"id": mail["id"]})
        if idem is not None:
            self._idempotent[(key, idem)] = (fingerprint, response)
        return response
