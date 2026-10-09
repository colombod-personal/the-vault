"""Sign-in with Google, Microsoft, Apple and Facebook (OAuth 2 / OpenID Connect).

Flow: ``GET /api/auth/login/{provider}`` redirects to the provider, which sends
the user back to ``/api/auth/callback/{provider}`` (Apple POSTs the form there).
We look up the ``(provider, subject)`` identity, create the user on first
sign-in, and keep only the user id in a signed session cookie.

Accounts are never merged by e-mail address: if someone is already signed in
and signs in with another provider, that provider is *linked* to the current
account; otherwise a new account is created. A sign-in that already has its own
account moves over only when that account is empty (``account_is_empty``); the
emptied account is deleted once it has no way to sign in left. Accounts holding
data are never merged.

Provider quirks handled here:

* Microsoft (``common`` tenant): the ID token issuer contains the user's tenant
  id, so it is checked against the ``tid`` claim instead of the static metadata.
* Apple: the client secret is a short-lived ES256 JWT we sign with the .p8 key
  (regenerated automatically; no manual 6-month rotation). Apple POSTs the
  callback (``form_post``), which needs a ``SameSite=None`` session cookie, and
  only sends the user's name on the very first sign-in.
* Facebook: plain OAuth 2, profile fetched from the Graph API ``/me`` endpoint.
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.starlette_client import OAuth
from joserfc.errors import JoseError
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from joserfc import jwt
from joserfc.jwk import ECKey
from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import outbound
from .config import Settings
from .locks import lock_account, lock_accounts
from .models import Identity, User, new_session_key, utcnow
from .ratelimit import limited

log = logging.getLogger(__name__)

PROVIDERS = ("google", "microsoft", "apple", "facebook")
# Anything that can go wrong validating a provider's response: OAuth errors, bad/expired
# tokens, wrong issuer or nonce, bad signatures, missing claims, provider outages.
SIGN_IN_ERRORS = (AuthlibBaseError, JoseError, KeyError, ValueError, httpx.HTTPError)
APPLE_SECRET_TTL = 3600  # seconds; Apple allows up to 6 months


@dataclass
class Profile:
    provider: str
    subject: str
    email: str | None
    name: str | None


def microsoft_issuer_ok(claims, value: str) -> bool:
    """Multi-tenant Entra ID tokens are issued by the user's own tenant."""
    tid = claims.get("tid")
    return bool(tid) and value == f"https://login.microsoftonline.com/{tid}/v2.0"


def apple_client_secret(settings: Settings, now: int | None = None) -> str:
    now = int(now or time.time())
    key = ECKey.import_key(settings.apple_private_key)
    claims = {
        "iss": settings.apple_team_id,
        "iat": now,
        "exp": now + APPLE_SECRET_TTL,
        "aud": "https://appleid.apple.com",
        "sub": settings.apple_client_id,
    }
    return jwt.encode({"alg": "ES256", "kid": settings.apple_key_id}, claims, key)


class Auth:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        """``transport`` replaces the network for all provider calls (tests: the twin universe)."""
        self.settings = settings
        self.oauth = OAuth()
        self._apple_secret_at = 0.0
        s = settings
        if s.google_client_id:
            self.oauth.register(
                "google", client_id=s.google_client_id, client_secret=s.google_client_secret,
                server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                client_kwargs={"scope": "openid email profile"},
            )
        if s.microsoft_client_id:
            self.oauth.register(
                "microsoft", client_id=s.microsoft_client_id, client_secret=s.microsoft_client_secret,
                server_metadata_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
                client_kwargs={"scope": "openid email profile"},
            )
        if s.apple_client_id:
            self.oauth.register(
                "apple", client_id=s.apple_client_id, client_secret="",
                server_metadata_url="https://appleid.apple.com/.well-known/openid-configuration",
                # "openid" is required for an id_token (and the nonce check); without it there's no user id.
                client_kwargs={"scope": "openid name email", "token_endpoint_auth_method": "client_secret_post"},
                authorize_params={"response_mode": "form_post"},
            )
        if s.facebook_client_id:
            v = s.facebook_graph_version
            self.oauth.register(
                "facebook", client_id=s.facebook_client_id, client_secret=s.facebook_client_secret,
                authorize_url=f"https://www.facebook.com/{v}/dialog/oauth",
                access_token_url=f"https://graph.facebook.com/{v}/oauth/access_token",
                api_base_url=f"https://graph.facebook.com/{v}/",
                client_kwargs={"scope": "email public_profile"},
            )

        if transport is not None:
            for name in PROVIDERS:
                client = self.oauth.create_client(name)
                if client is not None:
                    client.client_kwargs["transport"] = transport

    @property
    def enabled(self) -> list[str]:
        return [p for p in PROVIDERS if self.oauth.create_client(p) is not None]

    @property
    def offered(self) -> list[str]:
        """The enabled providers the sign-in screen shows (AUTH_HIDDEN_PROVIDERS left out)."""
        return [p for p in self.enabled if p not in self.settings.hidden_providers]

    def client(self, provider: str):
        client = self.oauth.create_client(provider) if provider in PROVIDERS else None
        if client is None:
            raise HTTPException(404, f"Sign-in with {provider} is not configured")
        if provider == "apple" and time.time() - self._apple_secret_at > APPLE_SECRET_TTL / 2:
            client.client_secret = apple_client_secret(self.settings)
            self._apple_secret_at = time.time()
        return client

    async def profile(self, provider: str, request: Request) -> Profile:
        client = self.client(provider)
        if provider == "facebook":  # plain OAuth 2.0, no ID token
            token = await client.authorize_access_token(request)
        else:
            # Authlib checks only what claims_options asks for, and "aud" isn't checked by default.
            # OpenID Connect requires it, so check it here, along with the issuer.
            if provider == "microsoft":  # "common" issues per-tenant issuers
                iss = {"essential": True, "validate": microsoft_issuer_ok}
            else:
                iss = {"essential": True, "values": [(await client.load_server_metadata())["issuer"]]}
            options = {"iss": iss, "aud": {"essential": True, "value": client.client_id}}
            token = await client.authorize_access_token(request, claims_options=options)

        if provider == "facebook":
            me = (await client.get("me", params={"fields": "id,name,email"}, token=token)).json()
            return Profile(provider, str(me["id"]), me.get("email"), me.get("name"))

        info = token.get("userinfo") or {}
        name = info.get("name")
        if provider == "apple":  # name arrives once, as JSON in the POSTed form
            form = await request.form()
            try:
                posted = json.loads(form.get("user") or "{}")
            except (TypeError, ValueError):
                posted = None
            # Whatever was POSTed: only a {"name": {"firstName": "...", "lastName": "..."}} gives a name.
            n = posted.get("name") if isinstance(posted, dict) else None
            parts = [n.get(k) for k in ("firstName", "lastName")] if isinstance(n, dict) else []
            name = " ".join(p for p in parts if isinstance(p, str) and p) or None
        email = info.get("email") or info.get("preferred_username")
        return Profile(provider, str(info["sub"]), email, name)


PKCE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43,128}")

# Providers' names for "the user pressed Cancel", as the one code the sign-in screen knows.
CANCELLED = {"user_cancelled_authorize": "access_denied", "user_denied": "access_denied"}


class SessionEnded(HTTPException):
    """The browser's session was ended (its account's session key was replaced) while a request that was already past
    authentication was still running: it must not add a sign-in method or mint anything that outlives the session (#347).
    A 401; the routes that answer with a redirect or a page catch it and say so in their own way."""

    def __init__(self) -> None:
        super().__init__(401, "Your session ended. Sign in again.")


def session_key_matches(live: str | None, expected) -> bool:
    return bool(live) and isinstance(expected, str) and hmac.compare_digest(live, expected)


def require_live_session(db: Session, request: Request, user_id: int) -> None:
    """Hold the request to the session it was authenticated with, right before it mints something that outlives it (an app
    session, a personal access token, an authorization code, a hand-over code, a passkey) (#347).

    "Sign out the other browsers" replaces ``users.session_key`` and deletes what a copied cookie minted; a request that was
    already past authentication when that committed would otherwise mint afterwards. This takes the account lock
    (:func:`vault.locks.lock_account`) and compares the key it reads with the cookie's: on a mismatch (401, ``SessionEnded``)
    nothing is minted. An app session (a bearer token with the account scope, which a copied cookie can mint through the
    hand-over) is held to its own row instead: the ``api_sessions`` row of that token must still exist under the lock, and step 1
    deletes those rows under the same lock. The lock is held until the caller's transaction ends, so the insert that follows is
    ordered after the rotation. Call it in the same transaction as the insert, with nothing committed in between."""
    live = lock_account(db, user_id)
    bearer = getattr(request.state, "bearer", None)
    if bearer is not None:
        from . import tokens
        from .models import ApiSession

        if db.scalar(select(ApiSession.id).where(ApiSession.access_hash == tokens._hash(bearer),
                                                 ApiSession.user_id == user_id)) is None:
            db.rollback()
            raise SessionEnded()
        return
    if not session_key_matches(live, request.session.get("sk")):
        db.rollback()
        raise SessionEnded()


def hold_account(db: Session, user_id: int, request: Request | None = None) -> None:
    """The first statement of a removal or of step 1: lock the account and, for a request, hold it to its live session
    (:func:`require_live_session`), so a request authenticated before the key was replaced cannot rotate it again and re-issue
    its stale cookie as a live one. Without a request (a direct call) it only takes the lock."""
    if request is None:
        lock_account(db, user_id)
    else:
        require_live_session(db, request, user_id)


def rotate_session_key(db: Session, user_id: int, request: Request | None = None) -> None:
    """Replace the account's session key and re-issue the caller's cookie with the new one: every other session ends, the
    caller's stays. Used wherever something that could have been added by a copied session is removed (#347): a session that
    signed in *through* the removed method shares the account's key, so removing the method alone would not end it. The
    caller's transaction commits; the account row is already locked by the removal."""
    user = db.get(User, user_id, populate_existing=True)
    state = getattr(request, "state", None)
    if request is not None and getattr(state, "bearer", None) is None and not session_key_matches(user.session_key, request.session.get("sk")):
        db.rollback()  # a stale cookie must not turn the live key into one it holds (defence in depth: callers hold the account first)
        raise SessionEnded()
    user.session_key = new_session_key()
    # Only the cookie of this account is re-issued: a bearer-token caller (no session) or a cookie of another account is left alone.
    if request is not None and request.session.get("uid") == user_id and "sk" in request.session:
        request.session["sk"] = user.session_key


def end_other_sessions(db: Session, user: User, request: Request) -> dict:
    """Step 1 of "Sign out everywhere" (#347). Ends every browser session but this one (new session key, this cookie re-issued)
    and, because a copied cookie can mint more than a browser session, also: every app sign-in made with the Vault app
    (``api_sessions`` with their retired refresh tokens, and unused hand-over codes), and the personal access tokens and
    connected apps created in the last :data:`RECENT_SIGN_IN_METHOD_HOURS`. Older tokens and connected apps are the person's
    own doing and stay; the answer counts what went so the page can say so. The caller commits."""
    from .models import AccessToken, ApiSession, AuthCode, OAuthCode, OAuthConsent, OAuthGrant

    # The account row first: a request that minted something and holds this lock finishes before the deletes below look for it,
    # and one that asks for the lock afterwards reads the new key and is refused (:func:`require_live_session`).
    hold_account(db, user.id, request)  # also refuses a cookie whose key was replaced meanwhile (a late "step 1" of a copied session)
    since = utcnow() - timedelta(hours=RECENT_SIGN_IN_METHOD_HOURS)
    gone = {
        "apps_signed_out": db.execute(delete(ApiSession).where(ApiSession.user_id == user.id)).rowcount or 0,
        "tokens_removed": db.execute(delete(AccessToken).where(
            AccessToken.user_id == user.id, AccessToken.created_at >= since)).rowcount or 0,
        "connected_apps_removed": db.execute(delete(OAuthGrant).where(
            OAuthGrant.user_id == user.id, OAuthGrant.created_at >= since)).rowcount or 0,
    }
    db.execute(delete(AuthCode).where(AuthCode.user_id == user.id))
    # An authorization code minted with the copied cookie lives 60 seconds and is redeemed at /oauth/token with no cookie: it
    # would make a 30-day grant after the answer above. Unredeemed codes and unanswered consent screens go too.
    db.execute(delete(OAuthCode).where(OAuthCode.user_id == user.id, OAuthCode.used_at.is_(None)))
    db.execute(delete(OAuthConsent).where(OAuthConsent.user_id == user.id))
    user.session_key = new_session_key()
    request.session["sk"] = user.session_key
    return gone


class IdentityInUse(Exception):
    """Linking a sign-in that already belongs to another Vault account that holds data."""

    def __init__(self) -> None:
        super().__init__(
            "This sign-in already belongs to another Vault account that holds data, so it can't be linked to "
            "this one. Sign in with it, export or delete that account (Account → Delete my account), "
            "then link it here again.")


def account_is_empty(db: Session, user_id: int) -> bool:
    """True when the account holds nothing anyone would miss, so a sign-in on it can move to the
    account that is linking it.

    Data: collection rows, imports, decks, shares given or received (pending invites too),
    unexpired personal access tokens, and value history with any value or copies. Not data, and
    removed with the account: its profile (name, e-mail), web and app sessions, one-time codes,
    stored idempotent answers, expired tokens and all-zero value rows. Its sign-in methods
    (identities, passkeys) are handled by :func:`_claim_identity`."""
    from .models import AccessToken, CollectionValue, Deck, Entry, Import, Share

    held = (
        select(Entry.id).where(Entry.user_id == user_id),
        select(Import.id).where(Import.user_id == user_id),
        select(Deck.id).where(Deck.user_id == user_id),
        select(Share.id).where(or_(Share.owner_id == user_id, Share.grantee_id == user_id)),
        select(AccessToken.id).where(AccessToken.user_id == user_id, AccessToken.expires_at > func.now()),
        select(CollectionValue.user_id).where(
            CollectionValue.user_id == user_id,
            or_(CollectionValue.copies != 0, CollectionValue.market_usd != 0, CollectionValue.cost_usd != 0)),
    )
    return not any(db.scalar(q.limit(1)) is not None for q in held)


def _claim_identity(db: Session, identity: Identity, current: User, hold: Request | None = None) -> None:
    """Move ``identity`` from the (empty) account that owns it to ``current``, in the caller's
    transaction. Raises IdentityInUse, with nothing changed, when that account holds data.

    Only this identity moves. The other account loses its sessions; if this was its last way to
    sign in (no identity or passkey left), it is deleted, with everything
    :func:`vault.privacy.personal_data` lists. Two non-empty accounts are never merged."""
    from .models import Passkey
    from .privacy import personal_data

    other_id = identity.user_id
    # Both accounts locked (in id order, so two links the other way round can't deadlock): an
    # import into the other account waits, and then sees it gone or no longer owning this sign-in.
    lock_accounts(db, [other_id, current.id])  # (bounded by the lock timeout, and the shared NO KEY mode: see vault/locks.py)
    if hold is not None:  # the caller's session was ended while this request ran: it may not take a sign-in method (#347)
        require_live_session(db, hold, current.id)
    identity_id = identity.id
    db.expire(identity)
    identity = db.get(Identity, identity_id, populate_existing=True)
    if identity is None:  # removed meanwhile (an unlink holding the other account's lock): nothing to move
        db.rollback()
        raise IdentityInUse()
    if identity.user_id == current.id:  # a concurrent link already moved it
        return
    if identity.user_id != other_id or not account_is_empty(db, identity.user_id):
        db.rollback()
        raise IdentityInUse()
    other = db.get(User, other_id)
    identity.user = current
    # It is added to *this* account now, whenever it was made on the other one: "sign-in methods added in
    # the last 24 hours" (#347) must show a method someone linked with a copied session, even if its
    # own empty account is older.
    identity.created_at = utcnow()
    db.flush()
    left = db.scalar(select(func.count(Identity.id)).where(Identity.user_id == other_id)) + db.scalar(
        select(func.count(Passkey.id)).where(Passkey.user_id == other_id))
    if left:
        other.session_key = new_session_key()  # signs out every browser on it
    else:
        db.expunge(other)
        for stmt in personal_data(other_id).values():
            db.execute(stmt.execution_options(synchronize_session=False))
    # No audit table: a log line without personal data.
    log.info("linked a %s sign-in from an empty account (%s)", identity.provider,
             "kept: it has other sign-in methods" if left else "deleted")


RECENT_SIGN_IN_METHOD_HOURS = 24  # "Sign out everywhere" lists the sign-in methods added in this window (#347)
PASSKEY_IDENTITY = "passkey"  # the account's WebAuthn user handle: not a method of its own (each passkey is listed)
PROVIDER_LABELS = {"google": "Google", "microsoft": "Microsoft", "apple": "Apple", "facebook": "Facebook",
                   "dev": "Local dev sign-in"}


def sign_in_methods(db: Session, user_id: int) -> list[dict]:
    """Every way to sign in to this account: its linked providers and its passkeys, each with when it was added
    (``recently_added`` is true inside the last :data:`RECENT_SIGN_IN_METHOD_HOURS`). Scoped to ``user_id``: nobody
    else's methods are ever read. The passkey identity row is the WebAuthn handle, not a method: the passkeys
    themselves are listed instead."""
    from .models import Passkey

    now = utcnow()
    since = now - timedelta(hours=RECENT_SIGN_IN_METHOD_HOURS)
    rows = [{"kind": "provider", "id": i.id, "provider": i.provider, "name": PROVIDER_LABELS.get(i.provider, i.provider),
             "created_at": i.created_at, "last_used_at": None}
            for i in db.scalars(select(Identity).where(Identity.user_id == user_id, Identity.provider != PASSKEY_IDENTITY))]
    rows += [{"kind": "passkey", "id": p.id, "provider": PASSKEY_IDENTITY, "name": p.name,
              "created_at": p.created_at, "last_used_at": p.last_used_at}
             for p in db.scalars(select(Passkey).where(Passkey.user_id == user_id))]
    for r in rows:
        r["recently_added"] = r["created_at"] >= since
        r["added_minutes_ago"] = max(0, int((now - r["created_at"]).total_seconds() / 60))
    return rows


def established_methods(user_id: int, since, *, skip_identity: int | None = None, skip_passkey: int | None = None):
    """A scalar subquery: how many sign-in methods of the account are older than ``since`` (a provider or a passkey; the
    passkey identity row is the WebAuthn handle, not a method), not counting the one being removed. Every removal
    (:func:`remove_identity`, :func:`vault.passkeys.remove_passkey`, :func:`remove_recent_methods`) needs this to be above zero
    inside its own DELETE: in an account whose methods are all new, nothing tells the owner's methods from those a copied
    session added, so none may be removed (#347)."""
    from .models import Passkey

    providers = select(func.count(Identity.id)).where(
        Identity.user_id == user_id, Identity.provider != PASSKEY_IDENTITY, Identity.created_at < since)
    passkeys = select(func.count(Passkey.id)).where(Passkey.user_id == user_id, Passkey.created_at < since)
    if skip_identity is not None:
        providers = providers.where(Identity.id != skip_identity)
    if skip_passkey is not None:
        passkeys = passkeys.where(Passkey.id != skip_passkey)
    return providers.scalar_subquery() + passkeys.scalar_subquery()


def why_not_removable(method: dict, methods: list[dict]) -> str | None:
    """Why ``method`` (one of ``methods``, from :func:`sign_in_methods`) would be refused if the person tried to remove it, or
    None. The same rules the removal routes apply, so the page shows a Remove button only where the route will say yes:
    ``only_method`` (the last way to sign in), ``provider_too_old`` (a provider linked more than the window ago is not unlinked
    here), ``needs_older_method`` (no OTHER method older than the window would remain)."""
    if len(methods) <= 1:
        return "only_method"
    if method["kind"] == "provider" and not method["recently_added"]:
        return "provider_too_old"
    if all(o["recently_added"] for o in methods if o is not method):
        return "needs_older_method"
    return None


def remove_recent_methods(db: Session, user_id: int, request: Request | None = None) -> int:
    """Remove every sign-in method added in the last :data:`RECENT_SIGN_IN_METHOD_HOURS` (providers and passkeys), for the
    person who finds a flood of them after "Sign out everywhere" (#347). 409 unless a method older than the window stays, so
    the account is never left with none and a young account (all methods new) removes nothing. Returns how many went. The
    account row is locked first, as in :func:`remove_identity`; the caller commits."""
    from .models import Passkey

    hold_account(db, user_id, request)
    since = utcnow() - timedelta(hours=RECENT_SIGN_IN_METHOD_HOURS)
    if not db.scalar(select(established_methods(user_id, since))):
        raise HTTPException(409, f"This account has no sign-in method older than {RECENT_SIGN_IN_METHOD_HOURS} hours, so "
                                 "nothing can be removed yet: the new ones could not be told apart. Sign out other browsers first.")
    gone = db.execute(delete(Passkey).where(Passkey.user_id == user_id, Passkey.created_at >= since)
                      .execution_options(synchronize_session=False)).rowcount
    gone += db.execute(delete(Identity).where(Identity.user_id == user_id, Identity.provider != PASSKEY_IDENTITY,
                                              Identity.created_at >= since)
                       .execution_options(synchronize_session=False)).rowcount
    # No passkeys left: the passkey identity (the WebAuthn handle) goes too, as when the last passkey is removed.
    db.execute(delete(Identity).where(Identity.user_id == user_id, Identity.provider == PASSKEY_IDENTITY,
                                      ~exists().where(Passkey.user_id == user_id))
               .execution_options(synchronize_session=False))
    if gone:
        rotate_session_key(db, user_id, request)  # a session that signed in through a removed method ends too
    db.flush()
    return gone


def remove_identity(db: Session, user_id: int, identity_id: int, request: Request | None = None) -> None:
    """Unlink one of the account's providers (Google, Microsoft, Apple, Facebook) that was added in the last
    :data:`RECENT_SIGN_IN_METHOD_HOURS` (409 for an older one), unless it is the last way to sign in (409). Another
    person's identity, a passkey's handle and an unknown id are all a 404.

    Only a recent one, on purpose (#347): this route exists for "Sign out everywhere", to remove what a copied session
    linked. Unlinking any provider would let that same copied session (while the owner has no recent-sign-in check yet)
    link its own sign-in and then unlink every one the owner uses, which is a takeover, not a nuisance. For the same reason
    a method used for longer than that window must remain after the removal (409 otherwise): in an account whose methods
    are all new, a copied session could add a passkey and unlink the owner's only provider, and nothing tells whose is whose.
    Widening it is part of the owner's decision on a recent sign-in for account-level actions (docs/mcp-oauth-threat-model.md).

    Same shape as :func:`vault.passkeys.remove_passkey`: the account row is locked, so two removals take turns,
    and the "another way to sign in is left" check is part of the DELETE itself. The caller commits."""
    from .models import Passkey

    hold_account(db, user_id, request)
    identity = db.get(Identity, identity_id, populate_existing=True)
    if identity is None or identity.user_id != user_id or identity.provider == PASSKEY_IDENTITY:
        raise HTTPException(404, "Sign-in method not found")
    db.expunge(identity)
    since = utcnow() - timedelta(hours=RECENT_SIGN_IN_METHOD_HOURS)
    if identity.created_at < since:
        raise HTTPException(409, f"Only a sign-in linked in the last {RECENT_SIGN_IN_METHOD_HOURS} hours can be unlinked here "
                                 "(Account, Sign out everywhere).")
    passkeys = select(func.count(Passkey.id)).where(Passkey.user_id == user_id).scalar_subquery()
    providers = select(func.count(Identity.id)).where(
        Identity.user_id == user_id, Identity.provider != PASSKEY_IDENTITY).scalar_subquery()
    established = established_methods(user_id, since, skip_identity=identity_id)
    removed = db.execute(
        delete(Identity).where(Identity.id == identity_id, Identity.user_id == user_id,
                               Identity.provider != PASSKEY_IDENTITY, Identity.created_at >= since,
                               passkeys + providers > 1, established > 0)
        .execution_options(synchronize_session=False)).rowcount
    if not removed:
        if db.scalar(select(passkeys + providers)) <= 1:
            raise HTTPException(409, "This is your only way to sign in. Add a passkey or link another sign-in first.")
        raise HTTPException(409, f"This account has no sign-in method older than {RECENT_SIGN_IN_METHOD_HOURS} hours besides "
                                 "the new ones, so nothing can be unlinked yet: it could not be told whose is whose. "
                                 "Sign out everywhere still works.")
    rotate_session_key(db, user_id, request)  # a session that signed in through the removed provider ends too (#347)
    db.flush()


def find_or_create(db: Session, profile: Profile, current: User | None = None, hold: Request | None = None) -> User:
    """The user owning ``profile``'s identity. A new identity is linked to ``current`` when
    someone is already signed in; otherwise it gets a new account. Never merged by e-mail.

    When ``current`` is set and the identity belongs to another account, it moves to ``current``
    if that account is empty (:func:`account_is_empty`, :func:`_claim_identity`); otherwise
    IdentityInUse is raised and nothing changes. Linking never switches accounts.

    Two first sign-ins with one identity at the same time both try to create it; the unique
    (provider, subject) index lets one win, and the other then signs in to the winner's account."""
    if profile.email and len(profile.email) > 320:  # no real address is this long (users.email is String(320))
        profile = Profile(profile.provider, profile.subject, None, profile.name)
    key = () if hold is None else (hold,)  # (only passed when there is a request to hold to its live session)
    try:
        return _find_or_create(db, profile, current, *key)
    except IntegrityError:
        db.rollback()
        return _find_or_create(db, profile, current, *key)


def _find_or_create(db: Session, profile: Profile, current: User | None, hold: Request | None = None) -> User:
    name = (profile.name or "")[:200] or None  # users.name is String(200), whatever the provider sent
    identity = db.scalar(
        select(Identity).where(Identity.provider == profile.provider, Identity.subject == profile.subject)
    )
    if identity:
        if current is not None and identity.user_id != current.id:
            _claim_identity(db, identity, current, hold)
        user = identity.user
        identity.email = profile.email or identity.email
    else:
        user = current or User(email=profile.email, name=name)
        if current is None:
            db.add(user)
        elif hold is not None:  # a new identity joins the account: only while the caller's session (cookie or app) is still live
            require_live_session(db, hold, current.id)
        user.identities.append(Identity(provider=profile.provider, subject=profile.subject, email=profile.email))
    if name and not user.name:
        user.name = name
    if profile.email and not user.email:
        user.email = profile.email
    db.commit()
    return user


def sign_in(db: Session, request: Request, profile: Profile, link: bool = True) -> User:
    """Find or create the user for ``profile`` and put them in the browser session.

    With ``link`` (provider sign-ins), a new identity joins the account already signed in here.
    Without it (passkey sign-in and sign-up, dev login) the browser simply switches accounts."""
    # A reviewer's demo session never takes a sign-in method of its own (#345): signing in with a provider switches accounts.
    current = session_user(db, request) if link and "rv" not in request.session else None
    user = find_or_create(db, profile, current, request if current is not None else None)
    if not user.session_key:
        user.session_key = new_session_key()
        db.commit()
    carried = {k: request.session[k] for k in ("app_flow", "oauth_pending") if k in request.session}
    request.session.clear()
    request.session["uid"] = user.id
    request.session["sk"] = user.session_key
    request.session.update(carried)
    return user


def session_user(db: Session, request: Request) -> User | None:
    """The user this browser's session cookie signs in, if it is still valid: the cookie's key
    must match the account's (see ``User.session_key``)."""
    uid, key = request.session.get("uid"), request.session.get("sk")
    user = db.get(User, uid) if isinstance(uid, int) and isinstance(key, str) else None
    if user is None or not user.session_key or not hmac.compare_digest(user.session_key, key):
        return None
    if "rv" in request.session:  # a store reviewer's demo session (vault.reviewer): only while its passphrase is still set
        from . import reviewer

        settings = request.app.state.settings
        if not reviewer.stamp_ok(request.session["rv"], settings.session_secret, settings.reviewer_passphrase):
            return None
    return user


APP_FLOW_KEYS = ("app_redirect_uri", "code_challenge")
OAUTH_PENDING_SECONDS = 600  # how long an MCP client's authorization request waits for sign-in

HANDOFF_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in to the Vault app</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:28rem;margin:4rem auto;padding:0 1rem;color:#1b1b1b;background:#fff}}
button{{font:inherit;padding:.6rem 1.2rem;margin:.25rem .5rem 0 0;border-radius:.5rem;border:1px solid #555;background:#fff;cursor:pointer}}
button[value=continue]{{background:#1b1b1b;color:#fff}}
@media (prefers-color-scheme:dark){{body{{background:#121212;color:#eee}}button{{background:#222;color:#eee}}button[value=continue]{{background:#eee;color:#121212}}}}</style>
</head><body><h1>Sign in to the Vault app?</h1>
<p>You are signed in as <strong>{who}</strong>. Continue only if you just asked the Vault app on this device to sign in.</p>
<form method="post" action="/api/auth/app-handoff">
<button type="submit" name="decision" value="continue" autofocus>Continue to the app</button>
<button type="submit" name="decision" value="cancel">Cancel</button></form></body></html>"""


def build_router(auth: Auth, get_db) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/providers")
    def providers() -> dict:
        from .passkeys import enabled as passkeys_enabled

        return {"providers": auth.offered, "dev_login": auth.settings.dev_login,
                "passkeys": passkeys_enabled(auth.settings)}

    @router.get("/login/{provider}", dependencies=limited("oauth-login"))
    async def login(provider: str, request: Request, app_redirect_uri: str | None = None,
                    code_challenge: str | None = None, code_challenge_method: str | None = None,
                    continue_: str | None = Query(None, alias="continue")):
        """Browser sign-in. Native apps open this in ASWebAuthenticationSession with
        ``app_redirect_uri`` (one of APP_REDIRECT_URIS) and a PKCE ``code_challenge`` (S256);
        they get ``<app_redirect_uri>?code=...`` back and redeem it at POST /api/v1/auth/token."""
        request.session.pop("app_flow", None)
        if continue_ != "oauth":  # only the OAuth consent page's sign-in resumes a pending request
            request.session.pop("oauth_pending", None)
        if app_redirect_uri is not None:
            if app_redirect_uri not in auth.settings.app_redirect_uris:
                raise HTTPException(400, "app_redirect_uri is not allowed")
            # RFC 7636: an S256 challenge is 43-128 base64url characters (also the column's limit).
            if (not code_challenge or (code_challenge_method or "S256") != "S256"
                    or not PKCE_CHALLENGE.fullmatch(code_challenge)):
                raise HTTPException(400, "A PKCE code_challenge (S256, 43-128 base64url characters) is required")
            request.session["app_flow"] = {"app_redirect_uri": app_redirect_uri, "code_challenge": code_challenge}
        redirect_uri = f"{auth.settings.base_url}/api/auth/callback/{provider}"
        client = auth.client(provider)
        try:
            response = await client.authorize_redirect(request, redirect_uri)
        except SIGN_IN_ERRORS as exc:  # e.g. the provider's discovery document is unreachable
            log.warning("sign-in with %s could not start: %s", provider, exc)
            app_flow = request.session.pop("app_flow", None)
            target = app_flow["app_redirect_uri"] if app_flow else "/"
            key = "error" if app_flow else "signin_error"
            query = {key: "temporarily_unavailable", **({} if app_flow else {"provider": provider})}
            return RedirectResponse(f"{target}?{urlencode(query)}", status_code=303)
        if auth.settings.twins_url:  # local development: the browser goes to the twin, not the real provider
            response.headers["location"] = outbound.browser_url(auth.settings, response.headers["location"])
        return response

    @router.api_route("/callback/{provider}", methods=["GET", "POST"], dependencies=limited("oauth-callback"))
    async def callback(provider: str, request: Request, db: Session = Depends(get_db)):
        app_flow = request.session.get("app_flow")
        try:
            profile = await auth.profile(provider, request)
        except SIGN_IN_ERRORS as exc:
            code = getattr(exc, "error", None) or "invalid_response"
            if code == "missing_code":  # the provider answered with its own error instead of a code
                answer = request.query_params if request.method == "GET" else await request.form()
                code = str(answer.get("error") or code)
            code = CANCELLED.get(code, code)
            log.warning("sign-in with %s failed: %s", provider, exc)
            if app_flow:
                request.session.pop("app_flow", None)
                return RedirectResponse(f"{app_flow['app_redirect_uri']}?{urlencode({'error': code})}", status_code=303)
            return RedirectResponse(f"/?{urlencode({'signin_error': code, 'provider': provider})}", status_code=303)
        def finish():
            """The database work, in a worker thread: it can wait for the account lock, which an async handler must not do on the
            event loop (#347)."""
            current = session_user(db, request)
            owner = db.scalar(select(Identity.user_id).where(
                Identity.provider == profile.provider, Identity.subject == profile.subject)) if current else None
            try:
                user = sign_in(db, request, profile)
            except SessionEnded:
                request.session.pop("app_flow", None)
                return RedirectResponse(f"/?{urlencode({'signin_error': 'session_ended', 'provider': provider})}", status_code=303)
            except IdentityInUse:
                if app_flow:
                    request.session.pop("app_flow", None)
                    return RedirectResponse(f"{app_flow['app_redirect_uri']}?{urlencode({'error': 'identity_in_use'})}",
                                            status_code=303)
                return RedirectResponse(f"/?{urlencode({'link_error': 'identity_in_use', 'provider': provider})}",
                                        status_code=303)
            # Linked while signed in: the web app says so once. When the sign-in came from another
            # (empty) account, it also says whether that account was removed or kept.
            linked = {}
            if current is not None and owner != current.id:
                linked = {"linked": provider}
                if owner is not None:
                    linked["empty_account"] = "removed" if db.get(User, owner) is None else "kept"
            if app_flow:
                # The code goes to the app only after the person confirms here. Anyone can start this
                # flow with their own PKCE challenge and send the link to someone; a silent sign-in
                # must not then hand that person's code to whichever app claims the custom scheme.
                request.session.pop("app_flow", None)
                request.session["app_handoff"] = {**app_flow, "uid": user.id}
                return RedirectResponse("/api/auth/app-handoff", status_code=303)
            pending = request.session.pop("oauth_pending", None)
            if isinstance(pending, dict) and time.time() - pending.get("t", 0) < OAUTH_PENDING_SECONDS:
                return RedirectResponse("/oauth/authorize?" + str(pending.get("q", "")), status_code=303)
            return RedirectResponse(f"/?{urlencode(linked)}" if linked else "/", status_code=303)

        return await run_in_threadpool(finish)

    @router.get("/app-handoff", response_class=HTMLResponse)
    def app_handoff_page(request: Request, db: Session = Depends(get_db)):
        handoff, user = request.session.get("app_handoff"), session_user(db, request)
        if not handoff or user is None or user.id != handoff.get("uid"):
            raise HTTPException(404, "No app sign-in is waiting")
        who = html.escape(user.name or user.email or "your Vault account")
        return HTMLResponse(HANDOFF_PAGE.format(who=who), headers={"Cache-Control": "no-store"})

    @router.post("/app-handoff")
    def app_handoff(request: Request, decision: str = Form(...), db: Session = Depends(get_db)):
        """The person's answer: hand a one-time code to the app, or tell it they declined."""
        handoff, user = request.session.pop("app_handoff", None), session_user(db, request)
        if not handoff or user is None or user.id != handoff.get("uid"):
            raise HTTPException(404, "No app sign-in is waiting")
        target = handoff["app_redirect_uri"]
        if decision != "continue":
            return RedirectResponse(f"{target}?{urlencode({'error': 'access_denied'})}", status_code=303)
        from . import tokens

        try:
            require_live_session(db, request, user.id)  # the session may have been ended while this request ran (#347)
        except SessionEnded:
            return RedirectResponse(f"{target}?{urlencode({'error': 'access_denied'})}", status_code=303)
        code = tokens.create_code(db, user, handoff["code_challenge"], target)
        return RedirectResponse(f"{target}?{urlencode({'code': code})}", status_code=303)

    @router.post("/logout")
    def logout(request: Request, everywhere: bool = False, db: Session = Depends(get_db)) -> dict:
        """Sign this browser out; with ``?everywhere=true``, every browser signed in to the account
        (a copied session cookie stops working too). Apps are signed out under /me/sessions."""
        user = session_user(db, request) if everywhere else None
        if user is not None:
            user.session_key = new_session_key()
            db.commit()
        request.session.clear()
        return {"ok": True}

    @router.post("/sign-out-others")
    def sign_out_others(request: Request, db: Session = Depends(get_db)) -> dict:
        """End every other session and keep this one (#347): the first step of Account, Sign out everywhere. The account's session
        key is replaced and this browser's cookie is re-issued, so a copied cookie is dead from now on; every app signed in with
        the Vault app is signed out (a copied cookie can mint one); personal access tokens and connected apps created in the last
        24 hours are removed (older ones are the person's own doing: they are listed under Account). The answer counts each.
        A reviewer's demo session may not (the demo account is shared)."""
        user = session_user(db, request)
        if user is None:
            raise HTTPException(401, "Sign in required")
        if "rv" in request.session:
            raise HTTPException(403, "The demo account's sessions can't be ended from here")
        gone = end_other_sessions(db, user, request)
        db.commit()
        return {"ok": True, **gone}

    @router.post("/dev-login")
    def dev_login(request: Request, db: Session = Depends(get_db),
                  email: str = Query("dev@localhost", min_length=1, max_length=255)) -> dict:
        if not auth.settings.dev_login:
            raise HTTPException(404)
        user = sign_in(db, request, Profile("dev", email, email, "Local developer"), link=False)
        return {"id": user.id}

    return router
