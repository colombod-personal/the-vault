"""Meta's data-deletion callback for Facebook Login (URL configured in Meta's app dashboard)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Identity, User
from ..privacy import purge_user
from ..ratelimit import limited


MAX_AGE = 3600  # seconds a data-deletion request stays valid
MAX_SKEW = 300  # seconds a request may seem to come from the future (clock differences)


def build_router(get_db, settings) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["meta"])

    # -- Facebook data deletion callback (required by Meta for Facebook Login) ----------
    @router.post("/facebook/data-deletion", dependencies=limited("facebook-deletion"))
    def facebook_data_deletion(signed_request: str = Form(...), db: Session = Depends(get_db)) -> dict:
        payload = parse_signed_request(signed_request, settings.facebook_client_secret)
        if payload is None:
            raise HTTPException(400, "Invalid signed_request")
        # Meta sends the request as the person removes the app. An old one (copied from a log, say)
        # is refused: replayed after they signed up again, it would delete their new account.
        issued = payload.get("issued_at")
        if not isinstance(issued, (int, float)) or not -MAX_SKEW <= time.time() - issued <= MAX_AGE:
            raise HTTPException(400, "This signed_request has expired")
        identity = db.scalar(
            select(Identity).where(Identity.provider == "facebook", Identity.subject == str(payload["user_id"]))
        )
        if identity:
            others = db.scalar(select(func.count(Identity.id)).where(
                Identity.user_id == identity.user_id, Identity.id != identity.id))
            if others:  # the account doesn't depend on Facebook: remove what came from Facebook only
                user = db.get(User, identity.user_id)
                if user.email and identity.email and user.email == identity.email and not db.scalar(
                        select(Identity.id).where(Identity.user_id == user.id, Identity.id != identity.id,
                                                  Identity.email == user.email).limit(1)):
                    user.email = None
                db.delete(identity)
                db.commit()
            else:
                purge_user(db, identity.user_id)
        code = _sign(settings.session_secret, secrets.token_urlsafe(12))
        return {"url": f"{settings.base_url}/api/facebook/deletion-status?code={code}", "confirmation_code": code}

    @router.get("/facebook/deletion-status", response_class=PlainTextResponse, dependencies=limited("facebook-status"))
    def facebook_deletion_status(code: str) -> str:
        # Only codes this callback issued (signed with the server's secret) are confirmed;
        # the data was deleted before the code was handed out.
        nonce, _, _sig = code.partition(".")
        if not nonce or not hmac.compare_digest(code, _sign(settings.session_secret, nonce)):
            raise HTTPException(404, "Unknown deletion request")
        return f"Deletion request {code}: all data the Vault received from this Facebook account has been deleted."

    return router


def _sign(secret: str, nonce: str) -> str:
    mac = hmac.new(secret.encode(), b"facebook-deletion:" + nonce.encode(), hashlib.sha256).digest()[:16]
    return f"{nonce}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def parse_signed_request(signed_request: str, secret: str) -> dict | None:
    """Verify and decode a Facebook ``signed_request`` (HMAC-SHA256 with the app secret)."""
    try:
        sig_b64, payload_b64 = signed_request.split(".", 1)
        expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
        if not secret or not hmac.compare_digest(_b64decode(sig_b64), expected):
            return None
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or str(payload.get("algorithm", "")).upper() != "HMAC-SHA256":
        return None
    return payload if "user_id" in payload else None
