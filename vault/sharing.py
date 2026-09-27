"""Sharing a collection or a deck with other users, by one-time invite link."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Deck, Share, User

INVITE_TTL = timedelta(days=14)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC.
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def owned_deck(db: Session, user: User, deck_id: int) -> Deck:
    """The user's own deck, or 404 (never 403: other tenants' ids must not be confirmable)."""
    deck = db.get(Deck, deck_id)
    if deck is None or deck.user_id != user.id:
        raise HTTPException(404, "Deck not found")
    return deck


def create_invite(db: Session, owner: User, kind: str, deck_id: int | None, show_costs: bool) -> tuple[Share, str]:
    if kind not in ("collection", "deck"):
        raise HTTPException(400, "kind must be 'collection' or 'deck'")
    if kind == "deck":
        if deck_id is None:
            raise HTTPException(400, "deck_id is required to share a deck")
        owned_deck(db, owner, deck_id)
    else:
        deck_id = None
    token = secrets.token_urlsafe(24)
    share = Share(
        owner_id=owner.id, kind=kind, deck_id=deck_id, show_costs=show_costs,
        token_hash=_hash(token), expires_at=datetime.now(timezone.utc) + INVITE_TTL,
    )
    db.add(share)
    db.commit()
    return share, token


def accept_invite(db: Session, user: User, token: str) -> Share:
    share = db.scalar(select(Share).where(Share.token_hash == _hash(token)))
    if share is None or share.grantee_id is not None:
        raise HTTPException(404, "This invite link is invalid or has already been used")
    if _aware(share.expires_at) and _aware(share.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(410, "This invite link has expired")
    if share.owner_id == user.id:
        raise HTTPException(400, "You can't accept your own invite")
    existing = db.scalar(select(Share).where(
        Share.owner_id == share.owner_id, Share.grantee_id == user.id,
        Share.kind == share.kind,
        Share.deck_id.is_(None) if share.deck_id is None else Share.deck_id == share.deck_id,
    ))
    if existing:  # already had access: keep one grant, take the newer settings
        existing.show_costs = share.show_costs
        db.delete(share)
        db.commit()
        return existing
    share.grantee_id = user.id
    share.token_hash = None  # single use
    share.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return share


def incoming_share(db: Session, user: User, share_id: int, kind: str) -> Share:
    """A share granted *to* ``user``, or 404."""
    share = db.get(Share, share_id)
    if share is None or share.grantee_id != user.id or share.kind != kind:
        raise HTTPException(404, "Not found")
    return share


def display_name(user: User | None) -> str:
    if user is None:
        return "Unknown"
    return user.name or user.email or f"Vault user #{user.id}"
