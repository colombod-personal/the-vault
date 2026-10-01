"""Sharing a collection or a deck with other users, by one-time invite link."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Deck, Share, User

INVITE_TTL = timedelta(days=14)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invite_token(secret: str, share_id: int) -> str:
    """The invite's link token, derived from the server's secret and the invite's id: the same
    every time, so a retried create can show the link again without storing it, and concurrent
    retries all show the same, valid link. Only its SHA-256 is stored."""
    mac = hmac.new(secret.encode(), f"vault-invite:{share_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; treat them as UTC.
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def owned_deck(db: Session, user: User, deck_id: int) -> Deck:
    """The user's own deck, or 404 (never 403: other tenants' ids must not be confirmable)."""
    deck = db.get(Deck, deck_id)
    if deck is None or deck.user_id != user.id:
        raise HTTPException(404, "Deck not found")
    return deck


def create_invite(db: Session, owner: User, kind: str, deck_id: int | None, show_costs: bool,
                  secret: str) -> tuple[Share, str]:
    if kind not in ("collection", "deck"):
        raise HTTPException(400, "kind must be 'collection' or 'deck'")
    if kind == "deck":
        if deck_id is None:
            raise HTTPException(400, "deck_id is required to share a deck")
        owned_deck(db, owner, deck_id)
    else:
        deck_id = None
    share = Share(owner_id=owner.id, kind=kind, deck_id=deck_id, show_costs=show_costs,
                  expires_at=datetime.now(timezone.utc) + INVITE_TTL)
    db.add(share)
    db.flush()  # the id the token is derived from
    token = invite_token(secret, share.id)
    share.token_hash = _hash(token)
    db.flush()  # the caller commits (with the Idempotency-Key answer)
    return share, token


def _grant_of(db: Session, share: Share, user: User) -> Share | None:
    """``user``'s accepted grant of the same thing ``share`` offers, if any."""
    return db.scalar(select(Share).where(
        Share.owner_id == share.owner_id, Share.grantee_id == user.id, Share.kind == share.kind,
        Share.deck_id.is_(None) if share.deck_id is None else Share.deck_id == share.deck_id,
    ))


def accept_invite(db: Session, user: User, token: str, *, retry: bool = True) -> Share:
    share = db.scalar(select(Share).where(Share.token_hash == _hash(token)))
    if share is None or share.grantee_id is not None:
        raise HTTPException(404, "This invite link is invalid or has already been used")
    if _aware(share.expires_at) and _aware(share.expires_at) < datetime.now(timezone.utc):
        raise HTTPException(410, "This invite link has expired")
    if share.owner_id == user.id:
        raise HTTPException(400, "You can't accept your own invite")
    # Two invites from one owner accepted at once must not both see "no grant yet": hold the
    # guest's row until this claim is committed.
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    existing = _grant_of(db, share, user)
    # Claim the invite atomically: of two requests racing for one link, only one matches the
    # unused row; the other gets the same 404 as a used link.
    unused = (Share.id == share.id, Share.token_hash == share.token_hash, Share.grantee_id.is_(None))
    if existing:  # already had access: keep one grant, take the newer settings
        if not db.execute(delete(Share).where(*unused)).rowcount:
            db.rollback()
            raise HTTPException(404, "This invite link is invalid or has already been used")
        existing.show_costs = share.show_costs
        db.flush()  # the caller commits (with the Idempotency-Key answer)
        return existing
    try:
        claimed = db.execute(update(Share).where(*unused).values(
            grantee_id=user.id, token_hash=None, accepted_at=datetime.now(timezone.utc))).rowcount  # single use
    except IntegrityError:  # a grant appeared since we looked (uq_shares_grant): fold into it
        db.rollback()
        if not retry:
            raise
        return accept_invite(db, user, token, retry=False)
    if not claimed:
        db.rollback()
        raise HTTPException(404, "This invite link is invalid or has already been used")
    db.flush()  # the caller commits (with the Idempotency-Key answer)
    db.refresh(share)
    return share


def invite_again(db: Session, owner: User, share_id: int, secret: str) -> tuple[Share, str]:
    """The link of an invite that hasn't been accepted yet, for a retried create (same
    Idempotency-Key). The token is derived again, so it is the one already handed out; nothing
    changes, and any number of retries, even at once, agree."""
    share = db.get(Share, share_id)
    if share is None or share.owner_id != owner.id:
        raise HTTPException(404, "This invite no longer exists")
    token = invite_token(secret, share.id)
    if share.grantee_id is not None or share.token_hash != _hash(token):
        raise HTTPException(409, "This invite was already accepted, so its link can't be shown again")
    return share, token


def incoming_share(db: Session, user: User, share_id: int, kind: str) -> Share:
    """A share granted *to* ``user``, or 404."""
    share = db.get(Share, share_id)
    if share is None or share.grantee_id != user.id or share.kind != kind:
        raise HTTPException(404, "Not found")
    return share


def display_name(user: User | None) -> str:
    """What the people you share with see: your display name, never your e-mail address."""
    if user is None:
        return "Unknown"
    return user.name or f"Vault user #{user.id}"
