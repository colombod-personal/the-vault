"""Collection, import, deck, sharing and account endpoints (all require sign-in).

Tenant isolation: every query is scoped to the signed-in user. Another user's data
is reachable only through a share they granted, and ids that aren't yours answer
404, so their existence isn't revealed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response
from mtg_toolkits import decklist, delta
from mtg_toolkits.archidekt import ArchidektClient
from mtg_toolkits.http import ApiError
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..importer import ImportError_, export_dragonshield, import_dragonshield, user_entries
from ..models import Deck, Identity, Import, Share, User
from ..prices import history
from ..privacy import export_archive, purge_user
from ..sharing import accept_invite, create_invite, display_name, incoming_share, owned_deck
from ..vault_json import build

DELETE_CONFIRMATION = "DELETE"


class CoverageRequest(BaseModel):
    text: str  # a pasted decklist, any common format


class ProfileUpdate(BaseModel):
    name: str


class DeleteRequest(BaseModel):
    confirm: str  # must be "DELETE"


class DeckIn(BaseModel):
    name: str
    text: str
    source_url: str | None = None


class ShareIn(BaseModel):
    kind: str  # "collection" | "deck"
    deck_id: int | None = None
    show_costs: bool = False


class AcceptIn(BaseModel):
    token: str


def _coverage(deck_text: str, owned_rows) -> dict:
    deck = decklist.parse_text(deck_text)
    lines = delta.coverage(deck.to_entries(), [r.to_collection_entry() for r in owned_rows])
    return {
        "cards": [
            {"name": c.entry.name, "set": c.entry.set_code, "number": c.entry.collector_number,
             "need": c.need, "have": c.have, "missing": c.missing, "status": c.status}
            for c in lines
        ],
        "unparsed": deck.unparsed,
    }


def _deck_json(d: Deck) -> dict:
    return {"id": d.id, "name": d.name, "text": d.text, "source_url": d.source_url,
            "created_at": d.created_at.isoformat(), "updated_at": d.updated_at.isoformat()}


def build_router(get_db, current_user, settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    # -- account ---------------------------------------------------------------
    @router.get("/me")
    def me(user: User = Depends(current_user)) -> dict:
        return {
            "id": user.id, "email": user.email, "name": user.name,
            "providers": sorted({i.provider for i in user.identities}),
        }

    @router.patch("/me")
    def update_me(body: ProfileUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        user.name = body.name.strip()[:200] or None  # right to rectification
        db.commit()
        return {"id": user.id, "name": user.name}

    @router.get("/me/export")
    def export_me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
        """Everything held about you, as a ZIP (GDPR access and portability)."""
        name = f"vault-data-{date.today().isoformat()}.zip"
        return Response(export_archive(db, user), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @router.delete("/me")
    def delete_me(body: DeleteRequest, request: Request, user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> dict:
        """Erase the account and all of its data. Download /api/me/export first to keep a copy."""
        if body.confirm != DELETE_CONFIRMATION:
            raise HTTPException(400, f'Send {{"confirm": "{DELETE_CONFIRMATION}"}} to delete your account')
        removed = purge_user(db, user.id)
        request.session.clear()
        return {"deleted": True, "removed": removed}

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

    @router.post("/decks/parse")
    def parse_deck(req: CoverageRequest, user: User = Depends(current_user)) -> dict:
        """Parse a pasted decklist with mtg_toolkits.decklist (the one parser for the whole app)."""
        deck = decklist.parse_text(req.text)
        return {
            "cards": [
                {"name": line.name, "set": line.set_code or "", "collector_number": line.collector_number or "",
                 "qty": line.quantity, "finish": line.finish.value, "section": line.section,
                 "categories": line.categories}
                for line in deck.lines
            ],
            "unparsed": deck.unparsed,
        }

    @router.post("/decks/coverage")
    def deck_coverage(req: CoverageRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
        return _coverage(req.text, user_entries(db, user))

    @router.get("/decks")
    def list_decks(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
        return [_deck_json(d) for d in db.scalars(select(Deck).where(Deck.user_id == user.id).order_by(Deck.name))]

    @router.post("/decks")
    def create_deck(body: DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        if not decklist.parse_text(body.text).lines:
            raise HTTPException(400, "No cards found in the decklist")
        deck = Deck(user_id=user.id, name=body.name.strip()[:200] or "Untitled deck", text=body.text,
                    source_url=body.source_url)
        db.add(deck)
        db.commit()
        return _deck_json(deck)

    @router.get("/decks/{deck_id}")
    def get_deck(deck_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        return {**_deck_json(deck), "coverage": _coverage(deck.text, user_entries(db, user))}

    @router.put("/decks/{deck_id}")
    def update_deck(deck_id: int, body: DeckIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        deck = owned_deck(db, user, deck_id)
        deck.name, deck.text, deck.source_url = body.name.strip()[:200] or deck.name, body.text, body.source_url
        deck.updated_at = datetime.now(timezone.utc)
        db.commit()
        return _deck_json(deck)

    @router.delete("/decks/{deck_id}")
    def delete_deck(deck_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        deck = owned_deck(db, user, deck_id)
        db.execute(delete(Share).where(Share.deck_id == deck.id))
        db.delete(deck)
        db.commit()
        return {"deleted": True}

    # -- sharing: what I've shared --------------------------------------------------------
    @router.post("/shares")
    def create_share(body: ShareIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        share, token = create_invite(db, user, body.kind, body.deck_id, body.show_costs)
        return {"id": share.id, "url": f"{settings.base_url}/?invite={token}",
                "expires_at": share.expires_at.isoformat()}

    @router.get("/shares")
    def list_shares(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
        rows = db.scalars(select(Share).where(Share.owner_id == user.id).order_by(Share.created_at.desc()))
        out = []
        for s in rows:
            deck = db.get(Deck, s.deck_id) if s.deck_id else None
            out.append({
                "id": s.id, "kind": s.kind, "deck_id": s.deck_id, "deck_name": deck.name if deck else None,
                "show_costs": s.show_costs, "status": "active" if s.grantee_id else "pending",
                "with": display_name(db.get(User, s.grantee_id)) if s.grantee_id else None,
                "created_at": s.created_at.isoformat(),
                "expires_at": s.expires_at.isoformat() if s.expires_at and not s.grantee_id else None,
            })
        return out

    @router.delete("/shares/{share_id}")
    def remove_share(share_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        """The owner revokes access, or the recipient leaves."""
        share = db.get(Share, share_id)
        if share is None or user.id not in (share.owner_id, share.grantee_id):
            raise HTTPException(404, "Not found")
        db.delete(share)
        db.commit()
        return {"deleted": True}

    # -- sharing: what others have shared with me -----------------------------------------
    @router.post("/shares/accept")
    def accept_share(body: AcceptIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        share = accept_invite(db, user, body.token)
        deck = db.get(Deck, share.deck_id) if share.deck_id else None
        return {"id": share.id, "kind": share.kind, "from": display_name(db.get(User, share.owner_id)),
                "deck_name": deck.name if deck else None}

    @router.get("/shared")
    def shared_with_me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
        rows = db.scalars(select(Share).where(Share.grantee_id == user.id).order_by(Share.accepted_at.desc()))
        out = []
        for s in rows:
            deck = db.get(Deck, s.deck_id) if s.deck_id else None
            out.append({"id": s.id, "kind": s.kind, "from": display_name(db.get(User, s.owner_id)),
                        "deck_name": deck.name if deck else None, "show_costs": s.show_costs})
        return out

    @router.get("/shared/{share_id}/collection")
    def shared_collection(share_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        share = incoming_share(db, user, share_id, "collection")
        owner = db.get(User, share.owner_id)
        data = build(db, owner, hide_costs=not share.show_costs)
        data["meta"]["sharedBy"] = display_name(owner)
        return data

    @router.get("/shared/{share_id}/deck")
    def shared_deck(share_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        """A deck someone shared, with coverage against *your* collection."""
        share = incoming_share(db, user, share_id, "deck")
        deck = db.get(Deck, share.deck_id)
        return {"name": deck.name, "text": deck.text, "source_url": deck.source_url,
                "from": display_name(db.get(User, share.owner_id)),
                "coverage": _coverage(deck.text, user_entries(db, user))}

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
