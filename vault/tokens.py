"""Bearer tokens for native apps.

* Access tokens: opaque, 1 hour, sent as ``Authorization: Bearer <token>``.
* Refresh tokens: opaque, 60 days, **rotated on every use**. Presenting a refresh
  token that was already rotated revokes the whole session, because it means the
  token was copied (refresh-token reuse detection, RFC 9700 §4.14).
* App codes: one-time, 2-minute codes that hand a browser sign-in (in
  ``ASWebAuthenticationSession``) over to the app, bound with PKCE S256 (RFC 7636).

Only SHA-256 hashes are stored, so a database leak doesn't leak usable tokens.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import ApiSession, AuthCode, User

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=60)
CODE_TTL = timedelta(minutes=2)


class TokenError(Exception):
    def __init__(self, error: str, description: str):
        super().__init__(description)
        self.error = error
        self.description = description


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _pair(session: ApiSession) -> dict:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    session.access_hash, session.access_expires = _hash(access), _now() + ACCESS_TTL
    session.refresh_hash, session.refresh_expires = _hash(refresh), _now() + REFRESH_TTL
    return {
        "access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh, "session_id": session.id,
    }


def issue(db: Session, user: User, client: str = "app", device_name: str | None = None) -> dict:
    session = ApiSession(user_id=user.id, client=client[:40], device_name=(device_name or None) and device_name[:120])
    tokens = _pair(session)
    db.add(session)
    db.flush()
    tokens["session_id"] = session.id
    db.commit()
    return tokens


def authenticate(db: Session, bearer: str) -> User | None:
    session = db.scalar(select(ApiSession).where(ApiSession.access_hash == _hash(bearer)))
    if session is None or _aware(session.access_expires) < _now():
        return None
    now = _now()
    if session.last_used_at is None or now - _aware(session.last_used_at) > timedelta(minutes=5):
        session.last_used_at = now
        db.commit()
    return db.get(User, session.user_id)


def refresh(db: Session, refresh_token: str) -> dict:
    h = _hash(refresh_token)
    session = db.scalar(select(ApiSession).where(or_(ApiSession.refresh_hash == h, ApiSession.previous_refresh_hash == h)))
    if session is None:
        raise TokenError("invalid_grant", "Unknown or revoked refresh token")
    if session.previous_refresh_hash == h:  # an old token came back: someone copied it
        db.delete(session)
        db.commit()
        raise TokenError("invalid_grant", "Refresh token reuse detected; the session was revoked")
    if _aware(session.refresh_expires) < _now():
        db.delete(session)
        db.commit()
        raise TokenError("invalid_grant", "Refresh token expired; sign in again")
    session.previous_refresh_hash = h
    tokens = _pair(session)
    db.commit()
    return tokens


def revoke_by_access(db: Session, bearer: str) -> bool:
    session = db.scalar(select(ApiSession).where(ApiSession.access_hash == _hash(bearer)))
    if session:
        db.delete(session)
        db.commit()
    return session is not None


# -- app codes (browser sign-in -> native app) --------------------------------------------

def create_code(db: Session, user: User, code_challenge: str, redirect_uri: str) -> str:
    code = secrets.token_urlsafe(32)
    db.add(AuthCode(user_id=user.id, code_hash=_hash(code), code_challenge=code_challenge,
                    redirect_uri=redirect_uri, expires_at=_now() + CODE_TTL))
    db.commit()
    return code


def s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def redeem_code(db: Session, code: str, code_verifier: str, redirect_uri: str, device_name: str | None) -> dict:
    row = db.scalar(select(AuthCode).where(AuthCode.code_hash == _hash(code)))
    if row is None:
        raise TokenError("invalid_grant", "Unknown or already used code")
    db.delete(row)  # single use, whatever happens next
    db.commit()
    if _aware(row.expires_at) < _now():
        raise TokenError("invalid_grant", "Code expired")
    if row.redirect_uri != redirect_uri:
        raise TokenError("invalid_grant", "redirect_uri does not match")
    if not secrets.compare_digest(s256(code_verifier), row.code_challenge):
        raise TokenError("invalid_grant", "PKCE verification failed")
    return issue(db, db.get(User, row.user_id), "app", device_name)
