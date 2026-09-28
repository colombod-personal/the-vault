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

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .models import AccessToken, ApiSession, AuthCode, RetiredRefreshToken, User

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


def _new_pair() -> tuple[dict, dict]:
    """(column values for the session, the token response)."""
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    values = {"access_hash": _hash(access), "access_expires": _now() + ACCESS_TTL,
              "refresh_hash": _hash(refresh), "refresh_expires": _now() + REFRESH_TTL}
    return values, {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
                    "refresh_token": refresh}


def issue(db: Session, user: User, client: str = "app", device_name: str | None = None) -> dict:
    values, tokens = _new_pair()
    session = ApiSession(user_id=user.id, client=client[:40], device_name=(device_name or None) and device_name[:120],
                         **values)
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
    session = db.scalar(select(ApiSession).where(ApiSession.refresh_hash == h))
    if session is None:
        retired = db.scalar(select(RetiredRefreshToken).where(RetiredRefreshToken.token_hash == h))
        if retired is None:
            raise TokenError("invalid_grant", "Unknown or revoked refresh token")
        # Any already-rotated token coming back means it was copied: revoke the whole session.
        db.execute(delete(RetiredRefreshToken).where(RetiredRefreshToken.session_id == retired.session_id))
        db.execute(delete(ApiSession).where(ApiSession.id == retired.session_id))
        db.commit()
        raise TokenError("invalid_grant", "Refresh token reuse detected; the session was revoked")
    if _aware(session.refresh_expires) < _now():
        db.execute(delete(RetiredRefreshToken).where(RetiredRefreshToken.session_id == session.id))
        db.delete(session)
        db.commit()
        raise TokenError("invalid_grant", "Refresh token expired; sign in again")
    retired_expires, values, tokens = session.refresh_expires, *_new_pair()
    # Compare-and-swap: of two refreshes racing with the same token, only one rotates it.
    if not db.execute(update(ApiSession).where(ApiSession.id == session.id, ApiSession.refresh_hash == h)
                      .values(**values)).rowcount:
        db.rollback()
        raise TokenError("invalid_grant", "This refresh token was just used by another request")
    db.execute(delete(RetiredRefreshToken).where(RetiredRefreshToken.session_id == session.id,
                                                 RetiredRefreshToken.expires_at < _now()))
    db.add(RetiredRefreshToken(user_id=session.user_id, session_id=session.id, token_hash=h,
                               expires_at=retired_expires))
    db.commit()
    return {**tokens, "session_id": session.id}


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
    # Single use, whatever happens next: claim it with a conditional delete, so of two requests
    # racing with the same code only one gets past here.
    claimed = db.execute(delete(AuthCode).where(AuthCode.id == row.id)).rowcount
    db.commit()
    if not claimed:
        raise TokenError("invalid_grant", "Unknown or already used code")
    if _aware(row.expires_at) < _now():
        raise TokenError("invalid_grant", "Code expired")
    if row.redirect_uri != redirect_uri:
        raise TokenError("invalid_grant", "redirect_uri does not match")
    if not secrets.compare_digest(s256(code_verifier), row.code_challenge):
        raise TokenError("invalid_grant", "PKCE verification failed")
    return issue(db, db.get(User, row.user_id), "app", device_name)


# -- personal access tokens (agents, scripts, MCP clients) ---------------------------------

PAT_PREFIX = "vault_pat_"
SCOPES = ("read", "write")
PAT_MAX_DAYS = 365


def is_pat(bearer: str) -> bool:
    return bearer.startswith(PAT_PREFIX)


def create_pat(db: Session, user: User, name: str, scopes: list[str], days: int) -> tuple[AccessToken, str]:
    token = PAT_PREFIX + secrets.token_urlsafe(32)
    row = AccessToken(user_id=user.id, name=name[:80], prefix=token[:len(PAT_PREFIX) + 4], token_hash=_hash(token),
                      scopes=" ".join(s for s in SCOPES if s in scopes), expires_at=_now() + timedelta(days=days))
    db.add(row)
    db.commit()
    return row, token


def authenticate_pat(db: Session, bearer: str) -> tuple[User, set[str]] | None:
    row = db.scalar(select(AccessToken).where(AccessToken.token_hash == _hash(bearer)))
    if row is None or _aware(row.expires_at) < _now():
        return None
    now = _now()
    if row.last_used_at is None or now - _aware(row.last_used_at) > timedelta(minutes=5):
        row.last_used_at = now
        db.commit()
    return db.get(User, row.user_id), set(row.scopes.split())
