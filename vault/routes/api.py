"""Collection, import, deck and account endpoints (all require sign-in)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response
from mtg_toolkits import decklist, delta
from mtg_toolkits.archidekt import ArchidektClient
from mtg_toolkits.http import ApiError
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..importer import ImportError_, export_dragonshield, import_dragonshield, user_entries
from ..models import Identity, Import, User
from ..prices import history
from ..vault_json import build


class CoverageRequest(BaseModel):
    text: str  # a pasted decklist, any common format


def build_router(get_db, current_user, settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    # -- account ---------------------------------------------------------------
    @router.get("/me")
    def me(user: User = Depends(current_user)) -> dict:
        return {
            "id": user.id, "email": user.email, "name": user.name,
            "providers": sorted({i.provider for i in user.identities}),
        }

    @router.delete("/me")
    def delete_me(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        db.delete(user)
        db.commit()
        request.session.clear()
        return {"deleted": True}

    # -- collection --------------------------------------------------------------
    @router.get("/collection")
    def collection(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        return build(db, user)

    @router.get("/collection/export.csv")
    def export_csv(user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
        return Response(
            export_dragonshield(db, user), media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="vault-export.csv"'},
        )

    @router.get("/history")
    def value_history(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
        return history(db, user)

    @router.post("/imports")
    async def create_import(file: UploadFile, user: User = Depends(current_user), db: Session = Depends(get_db)):
        content = await file.read()
        try:
            imp = import_dragonshield(db, user, file.filename or "upload.csv", content)
        except ImportError_ as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": imp.id, "rows": imp.rows, "copies": imp.copies, "changes": imp.summary}

    @router.get("/imports")
    def list_imports(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
        rows = db.scalars(select(Import).where(Import.user_id == user.id).order_by(Import.created_at.desc()))
        return [
            {"id": i.id, "filename": i.filename, "rows": i.rows, "copies": i.copies,
             "changes": i.summary, "created_at": i.created_at.isoformat()}
            for i in rows
        ]

    # -- decks ---------------------------------------------------------------------
    @router.get("/archidekt/decks/{deck_id}")
    def archidekt_deck(deck_id: int, user: User = Depends(current_user)) -> dict:
        """Server-side fetch, replacing the prototype's public CORS proxies."""
        try:
            with ArchidektClient() as client:
                return client.get_deck(deck_id).raw
        except ApiError as exc:
            raise HTTPException(exc.status_code if exc.status_code == 404 else 502, str(exc)) from exc

    @router.post("/decks/coverage")
    def deck_coverage(req: CoverageRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
        deck = decklist.parse_text(req.text)
        owned = [r.to_collection_entry() for r in user_entries(db, user)]
        lines = delta.coverage(deck.to_entries(), owned)
        return {
            "cards": [
                {"name": c.entry.name, "set": c.entry.set_code, "number": c.entry.collector_number,
                 "need": c.need, "have": c.have, "missing": c.missing, "status": c.status}
                for c in lines
            ],
            "unparsed": deck.unparsed,
        }

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
            db.execute(delete(User).where(User.id == identity.user_id))
            db.commit()
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
