"""Meta's data-deletion callback for Facebook Login (URL configured in Meta's app dashboard)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Identity
from ..privacy import purge_user


def build_router(get_db, settings) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["meta"])

    # -- Facebook data deletion callback (required by Meta for Facebook Login) ----------
    @router.post("/facebook/data-deletion")
    def facebook_data_deletion(signed_request: str = Form(...), db: Session = Depends(get_db)) -> dict:
        payload = parse_signed_request(signed_request, settings.facebook_client_secret)
        if payload is None:
            raise HTTPException(400, "Invalid signed_request")
        identity = db.scalar(
            select(Identity).where(Identity.provider == "facebook", Identity.subject == str(payload["user_id"]))
        )
        if identity:
            purge_user(db, identity.user_id)
        code = secrets.token_urlsafe(12)
        return {"url": f"{settings.base_url}/api/facebook/deletion-status?code={code}", "confirmation_code": code}

    @router.get("/facebook/deletion-status", response_class=PlainTextResponse)
    def facebook_deletion_status(code: str) -> str:
        return f"Deletion request {code}: all data for this Facebook account has been deleted."

    return router


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
    return payload if payload.get("algorithm", "").upper() == "HMAC-SHA256" and "user_id" in payload else None
