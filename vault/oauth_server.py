"""The Vault as an OAuth 2.1 authorization server for MCP clients: codes, grants and tokens.

* **Authorization codes**: 60 seconds, single use, bound to the client, redirect URI, PKCE S256
  challenge, resource and person. Redeeming one twice revokes what the first redemption issued.
* **Grants** (``oauth_grants``): one person's consent to one app. Each holds the current access
  token (1 hour) and refresh token (30 days, **rotated on every use**; a rotated one coming back
  revokes the whole grant, RFC 9700 section 4.14). Only SHA-256 hashes are stored.
* **Scopes** ``read`` and ``write``. Tokens never carry the ``account`` power: they can't create
  tokens, export or delete the account, or manage sign-ins (``account_user`` in ``vault.app``).
* **Audience**: tokens are for one resource, the MCP server (RFC 8707), and are accepted nowhere else.

Personal access tokens (``vault.tokens``) are separate and unchanged.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .models import OAuthClient, OAuthCode, OAuthConsent, OAuthGrant, OAuthRetiredRefresh, User
from .tokens import PKCE_VERIFIER, _touch, s256

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
GRANT_MAX_AGE = timedelta(days=90)  # from first consent: refreshing never extends a grant past this
CONSENT_TTL = timedelta(minutes=10)
MAX_PENDING_CONSENTS = 5  # unanswered consent screens per person
CODE_TTL = timedelta(seconds=60)
ACCESS_PREFIX = "vault_oat_"
REFRESH_PREFIX = "vault_ort_"
SCOPES = ("read", "write")
IGNORED_SCOPES = ("offline_access",)  # refresh tokens are always issued
MAX_APPS = 50  # connected apps per person


class OAuthError(Exception):
    """An OAuth error answer (RFC 6749 section 5.2). ``description`` never says whether an
    account, code or token exists beyond what the caller already knows."""

    def __init__(self, code: str, description: str, status: int = 400):
        super().__init__(description)
        self.code, self.description, self.status = code, description, status


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def resource_uri(base_url: str) -> str:
    """The MCP server's canonical resource identifier (RFC 8707 / RFC 9728)."""
    return f"{base_url.rstrip('/')}/api/mcp"


def parse_scopes(raw: str | None) -> list[str]:
    """The requested scopes, in canonical order, always including read. No ``scope`` means
    ``read``: write is never a default."""
    asked = [s for s in (raw or "").split() if s not in IGNORED_SCOPES]
    if any(s not in SCOPES for s in asked):
        raise OAuthError("invalid_scope", "Unsupported scope. The Vault offers: read, write")
    return [s for s in SCOPES if s in asked or s == "read"]


def is_access_token(bearer: str) -> bool:
    return bearer.startswith(ACCESS_PREFIX)


# -- codes -----------------------------------------------------------------------------------

def issue_code(db: Session, user: User, client_id: str, redirect_uri: str, code_challenge: str, resource: str,
               scopes: list[str]) -> str:
    code = secrets.token_urlsafe(32)
    db.execute(delete(OAuthCode).where(OAuthCode.expires_at < _now() - timedelta(minutes=5)))
    db.add(OAuthCode(user_id=user.id, code_hash=_hash(code), client_id=client_id, redirect_uri=redirect_uri,
                     code_challenge=code_challenge, resource=resource, scopes=" ".join(scopes),
                     expires_at=_now() + CODE_TTL))
    db.commit()
    return code


def _new_pair() -> tuple[dict, str, str]:
    access, refresh = ACCESS_PREFIX + secrets.token_urlsafe(32), REFRESH_PREFIX + secrets.token_urlsafe(48)
    values = {"access_hash": _hash(access), "access_expires": _now() + ACCESS_TTL,
              "refresh_hash": _hash(refresh), "refresh_expires": _now() + REFRESH_TTL}
    return values, access, refresh


def _response(access: str, refresh: str, scopes: str) -> dict:
    return {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
            "refresh_token": refresh, "scope": scopes}


def revoke_grant(db: Session, grant_id: int | None) -> None:
    """Revoke a grant: its tokens and its retired refresh tokens go with it."""
    if grant_id is not None:
        db.execute(delete(OAuthRetiredRefresh).where(OAuthRetiredRefresh.grant_id == grant_id))
        db.execute(delete(OAuthGrant).where(OAuthGrant.id == grant_id))
        db.commit()


def _check_code(row: OAuthCode, client_id: str, redirect_uri: str, verifier: str, resource: str | None) -> None:
    if _aware(row.expires_at) < _now():
        raise OAuthError("invalid_grant", "The authorization code is invalid, expired or already used")
    if row.client_id != client_id or row.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "client_id or redirect_uri does not match the authorization request")
    if not PKCE_VERIFIER.fullmatch(verifier or "") or not secrets.compare_digest(s256(verifier), row.code_challenge):
        raise OAuthError("invalid_grant", "PKCE verification failed")
    if resource is not None and resource != row.resource:
        raise OAuthError("invalid_target", "resource does not match the authorization request")


def _make_room(db: Session, user_id: int) -> None:
    """At most MAX_APPS grants per person: connecting one more drops the oldest. Connections nobody can use
    any more (refresh token expired, anyone's) go with them."""
    purge_expired_grants(db, commit=False)
    old = list(db.scalars(select(OAuthGrant.id).where(OAuthGrant.user_id == user_id).order_by(OAuthGrant.id.desc())
                          .offset(MAX_APPS - 1)))
    if old:  # not committed here: the caller's transaction (the code's redemption) commits it
        db.execute(delete(OAuthRetiredRefresh).where(OAuthRetiredRefresh.grant_id.in_(old)))
        db.execute(delete(OAuthGrant).where(OAuthGrant.id.in_(old)))


def exchange_code(db: Session, client_id: str, code: str, redirect_uri: str, verifier: str, resource: str | None) -> dict:
    """Redeem a code. Claiming it and making the grant are **one transaction**: a second request with
    the same code waits on the claim's row lock, and when it gets through the grant exists, so the
    replay finds it and revokes it. A code that fails a check is burnt on purpose (the claim is
    committed): a wrong verifier or client does not get a second try."""
    row = db.scalar(select(OAuthCode).where(OAuthCode.code_hash == _hash(code or "")))
    if row is None:
        raise OAuthError("invalid_grant", "The authorization code is invalid, expired or already used")
    claimed = db.execute(update(OAuthCode).where(OAuthCode.id == row.id, OAuthCode.used_at.is_(None))
                         .values(used_at=_now()).execution_options(synchronize_session=False)).rowcount
    if not claimed:
        # A code coming back means it was copied: what the first redemption issued is revoked.
        db.rollback()
        revoke_grant(db, db.scalar(select(OAuthCode.grant_id).where(OAuthCode.id == row.id)))
        raise OAuthError("invalid_grant", "The authorization code is invalid, expired or already used")
    try:
        _check_code(row, client_id, redirect_uri, verifier, resource)
        user = db.get(User, row.user_id)
        if user is None:
            raise OAuthError("invalid_grant", "The authorization code is invalid, expired or already used")
    except OAuthError:
        db.commit()  # the burn
        raise
    _make_room(db, user.id)
    values, access, refresh = _new_pair()
    grant = OAuthGrant(user_id=user.id, client_id=row.client_id, scopes=row.scopes, access_scopes=row.scopes,
                       resource=row.resource, **values)
    db.add(grant)
    db.flush()
    db.execute(update(OAuthCode).where(OAuthCode.id == row.id).values(grant_id=grant.id))
    db.commit()
    return _response(access, refresh, row.scopes)


# -- refresh ---------------------------------------------------------------------------------

def _narrow(requested: str | None, allowed: str) -> str:
    """The scopes of a refreshed access token: never more than the person allowed."""
    if not requested:
        return allowed
    asked = [s for s in requested.split() if s not in IGNORED_SCOPES]
    if not asked or any(s not in allowed.split() for s in asked):
        raise OAuthError("invalid_scope", "A refresh can't widen the scopes the person allowed")
    return " ".join(s for s in SCOPES if s in asked)


def _unknown_refresh(db: Session, token_hash: str) -> None:
    """A refresh token nobody holds: if it was rotated, it was copied, so its grant is revoked.
    Always ends in invalid_grant."""
    retired = db.scalar(select(OAuthRetiredRefresh).where(OAuthRetiredRefresh.token_hash == token_hash))
    if retired is not None and _aware(retired.expires_at) >= _now():
        revoke_grant(db, retired.grant_id)
    elif retired is not None:
        db.delete(retired)
        db.commit()
    raise OAuthError("invalid_grant", "The refresh token is invalid, expired or revoked")


def refresh(db: Session, client_id: str, refresh_token: str, scope: str | None, resource: str | None) -> dict:
    h = _hash(refresh_token or "")
    grant = db.scalar(select(OAuthGrant).where(OAuthGrant.refresh_hash == h))
    if grant is None:
        _unknown_refresh(db, h)
    ends = _aware(grant.created_at) + GRANT_MAX_AGE
    if grant.client_id != client_id or _aware(grant.refresh_expires) < _now() or _now() > ends:
        revoke_grant(db, grant.id)  # presented by another client (copied), or past its life
        raise OAuthError("invalid_grant", "The refresh token is invalid, expired or revoked")
    if resource is not None and resource != grant.resource:
        raise OAuthError("invalid_target", "resource does not match the grant")
    scopes = _narrow(scope, grant.scopes)
    values, access, refresh_new = _new_pair()
    values["refresh_expires"] = min(values["refresh_expires"], ends)  # refreshing never outlives the grant
    # Compare-and-swap: of two refreshes racing with the same token, only one rotates it.
    if not db.execute(update(OAuthGrant).where(OAuthGrant.id == grant.id, OAuthGrant.refresh_hash == h)
                      .values(access_scopes=scopes, **values)).rowcount:
        db.rollback()
        raise OAuthError("invalid_grant", "The refresh token is invalid, expired or revoked")
    db.execute(delete(OAuthRetiredRefresh).where(OAuthRetiredRefresh.grant_id == grant.id,
                                                 OAuthRetiredRefresh.expires_at < _now()))
    db.add(OAuthRetiredRefresh(user_id=grant.user_id, grant_id=grant.id, token_hash=h, expires_at=grant.refresh_expires))
    db.commit()
    return _response(access, refresh_new, scopes)


# -- consent screens ------------------------------------------------------------------------

def save_consent(db: Session, user_id: int, nonce: str, query: str) -> None:
    """Remember a consent screen that was shown (by the nonce's hash), so the answer can be matched
    to it exactly once."""
    db.execute(delete(OAuthConsent).where(OAuthConsent.expires_at < _now()))
    stale = list(db.scalars(select(OAuthConsent.id).where(OAuthConsent.user_id == user_id)
                            .order_by(OAuthConsent.id.desc()).offset(MAX_PENDING_CONSENTS - 1)))
    if stale:
        db.execute(delete(OAuthConsent).where(OAuthConsent.id.in_(stale)))
    db.add(OAuthConsent(user_id=user_id, nonce_hash=_hash(nonce), query=query, expires_at=_now() + CONSENT_TTL))
    db.commit()


def take_consent(db: Session, user_id: int, nonce: str) -> str | None:
    """The authorization request this nonce was shown for, or None. One conditional DELETE ... RETURNING:
    of any number of answers with the same nonce (a replayed cookie and form, requests racing), one wins."""
    taken = db.execute(delete(OAuthConsent).where(
        OAuthConsent.nonce_hash == _hash(nonce or ""), OAuthConsent.user_id == user_id,
        OAuthConsent.expires_at > _now()).returning(OAuthConsent.query)).scalar()
    db.commit()
    return taken


# -- using a token ---------------------------------------------------------------------------

def authenticate(db: Session, bearer: str, resource: str) -> tuple[User, set[str]] | None:
    """The person and scopes behind an access token meant for ``resource``, or None (unknown,
    expired, revoked, or issued for another resource). Never includes the ``account`` power."""
    grant = db.scalar(select(OAuthGrant).where(OAuthGrant.access_hash == _hash(bearer)))
    if grant is None or _aware(grant.access_expires) < _now() or grant.resource != resource:
        return None
    now = _now()
    if grant.last_used_at is None or now - _aware(grant.last_used_at) > timedelta(minutes=5):
        if not _touch(db, OAuthGrant, grant.id, now):
            return None  # revoked meanwhile
    user = db.get(User, grant.user_id)
    return (user, set(grant.access_scopes.split())) if user else None


def revoke_token(db: Session, client_id: str, token: str) -> None:
    """RFC 7009: revoke the grant behind an access or refresh token of this client. Anything else
    is ignored (the answer is the same either way)."""
    h = _hash(token or "")
    grant = db.scalar(select(OAuthGrant).where((OAuthGrant.access_hash == h) | (OAuthGrant.refresh_hash == h)))
    if grant is not None and grant.client_id == client_id:
        revoke_grant(db, grant.id)


# -- connected apps --------------------------------------------------------------------------

IDLE_AFTER = timedelta(days=14)  # a connection nobody has used for this long is shown as idle
RECENT = timedelta(hours=1)  # an app used this recently is named in the "disconnect" confirmation


def purge_expired_grants(db: Session, user_id: int | None = None, *, commit: bool = True) -> int:
    """Delete connections that can no longer be used (this person's, or everyone's when ``user_id`` is None): the
    refresh token ran out (30 days without a refresh, each one renews it, never past the grant's 90 days). Their
    retired tokens go too."""
    query = select(OAuthGrant.id).where(OAuthGrant.refresh_expires < _now())
    if user_id is not None:
        query = query.where(OAuthGrant.user_id == user_id)
    dead = list(db.scalars(query))
    if dead:
        db.execute(delete(OAuthRetiredRefresh).where(OAuthRetiredRefresh.grant_id.in_(dead)))
        db.execute(delete(OAuthGrant).where(OAuthGrant.id.in_(dead)))
        if commit:
            db.commit()
    return len(dead)


def user_grants(db: Session, user_id: int) -> list[OAuthGrant]:
    """This person's live connections (expired ones are removed first)."""
    purge_expired_grants(db, user_id)
    return list(db.scalars(select(OAuthGrant).where(OAuthGrant.user_id == user_id).order_by(OAuthGrant.id)))


def is_idle(grant: OAuthGrant, now: datetime | None = None) -> bool:
    """Not used for 14 days (a connection never used counts from when it was made)."""
    return (now or _now()) - _aware(grant.last_used_at or grant.created_at) > IDLE_AFTER


def minutes_since_use(group: list[OAuthGrant]) -> int | None:
    """Whole minutes since this app last acted when that was within the last hour, else None."""
    used = [_aware(g.last_used_at) for g in group if g.last_used_at]
    ago = _now() - max(used) if used else None
    return ago // timedelta(minutes=1) if ago is not None and ago < RECENT else None


def app_key(grant: OAuthGrant, client: OAuthClient | None) -> tuple:
    """What makes two connections the same app. An app identified by its web address is that address.
    Apps that register themselves get a new client id every time they are added (Claude Code does), so
    they are told apart by the name they gave; they stay apart from any app with a verified address."""
    if client is not None and client.kind == "dcr":
        return ("registered", client.name)
    return ("address", grant.client_id)


def connected_apps(db: Session, user_id: int) -> list[list[OAuthGrant]]:
    """This person's live connections grouped by app, newest connection first in each group and the
    group with the newest connection first."""
    grants = user_grants(db, user_id)
    clients = {c.client_id: c for c in db.scalars(select(OAuthClient).where(
        OAuthClient.client_id.in_({g.client_id for g in grants})))} if grants else {}
    groups: dict[tuple, list[OAuthGrant]] = {}
    for g in sorted(grants, key=lambda g: -g.id):
        groups.setdefault(app_key(g, clients.get(g.client_id)), []).append(g)
    return list(groups.values())


def revoke_user_app(db: Session, user_id: int, grant_id: int) -> int | None:
    """Disconnect the app one of this person's connections belongs to: every connection of that app
    (each device) is revoked at once, in one transaction. The number revoked, or None when the id isn't theirs."""
    for group in connected_apps(db, user_id):
        if any(g.id == grant_id for g in group):
            ids = [g.id for g in group]
            db.execute(delete(OAuthRetiredRefresh).where(OAuthRetiredRefresh.grant_id.in_(ids)))
            db.execute(delete(OAuthGrant).where(OAuthGrant.id.in_(ids), OAuthGrant.user_id == user_id))
            db.commit()
            return len(ids)
    return None
