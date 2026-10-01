"""Passkeys (WebAuthn): sign in with Face ID, Touch ID, Windows Hello, a phone or a security key.

This needs no third-party console or account. Creating a passkey is also how someone makes a
Vault account without Google, Microsoft, Apple or Facebook.

Each flow is two steps: ``…/options`` returns the WebAuthn options, and ``…/verify`` checks the
browser's answer with py_webauthn. The challenge is kept on the server (``passkey_challenges``,
valid for five minutes) and the session cookie only names it. Verifying claims it with a
conditional DELETE, so it is used once, even by a replayed cookie or two racing requests.

- ``signup``: new account, identified only by the passkey
- ``register``: add a passkey to the signed-in account
- ``login``: discoverable credentials; the browser offers the passkeys it has for this site

The relying party is ``BASE_URL``'s host, so passkeys work on the production domain and on
localhost. They don't work on preview URLs, which have other hosts.
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse, InvalidJSONStructure
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .auth import Profile
from .config import Settings
from .models import Identity, Passkey, PasskeyChallenge, User

RP_NAME = "The Vault"
CHALLENGE_TTL = 300
SESSION_KEY = "passkey"
PROVIDER = "passkey"
WEBAUTHN_ERRORS = (InvalidRegistrationResponse, InvalidAuthenticationResponse, InvalidJSONStructure, ValueError, KeyError)


def relying_party(settings: Settings) -> tuple[str, str]:
    """(rp_id, origin) from BASE_URL."""
    parts = urlsplit(settings.base_url)
    return parts.hostname or "localhost", f"{parts.scheme}://{parts.netloc}"


def enabled(settings: Settings) -> bool:
    """WebAuthn needs a secure context: https, or localhost during development."""
    rp_id, origin = relying_party(settings)
    return origin.startswith("https://") or rp_id == "localhost"


class Signup(BaseModel):
    name: str | None = Field(None, max_length=200, description="Display name for the new account")


class Verify(BaseModel):
    credential: dict = Field(description="The PublicKeyCredential from the browser, as JSON (credential.toJSON())")
    name: str | None = Field(None, max_length=80, description="A label for this passkey, e.g. 'MacBook'")


def _stash(request: Request, db: Session, kind: str, challenge: bytes, **extra) -> None:
    now = time.time()
    db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires < now))  # tidy up old ones
    ceremony = secrets.token_urlsafe(32)
    db.add(PasskeyChallenge(id=ceremony, kind=kind, challenge=bytes_to_base64url(challenge), expires=now + CHALLENGE_TTL))
    db.commit()
    request.session[SESSION_KEY] = {"id": ceremony, "kind": kind, **extra}


def _take(request: Request, db: Session, kind: str) -> dict:
    """The pending ceremony, claimed once: of two requests with the same cookie, one wins."""
    expired = HTTPException(400, "No passkey request in progress, or it expired. Start again.")
    pending = request.session.pop(SESSION_KEY, None)
    if not pending or pending.get("kind") != kind or not pending.get("id"):
        raise expired
    row = db.get(PasskeyChallenge, pending["id"])
    if row is None:
        raise expired
    challenge = row.challenge
    claimed = db.execute(delete(PasskeyChallenge).where(
        PasskeyChallenge.id == pending["id"], PasskeyChallenge.kind == kind, PasskeyChallenge.expires >= time.time()))
    db.commit()
    if claimed.rowcount != 1:
        raise expired
    return {**pending, "challenge": challenge}


def _label(credential: dict, given: str | None) -> str:
    if given and given.strip():
        return given.strip()[:80]
    return "Passkey"


def build_router(settings: Settings, get_db, sign_in, account_user) -> APIRouter:
    router = APIRouter(prefix="/api/auth/passkey", tags=["auth"])
    rp_id, origin = relying_party(settings)

    def require_enabled() -> None:
        if not enabled(settings):
            raise HTTPException(404, "Passkeys need an https BASE_URL (or localhost)")

    def registration_options(request: Request, db: Session, kind: str, handle: bytes, user_name: str, display: str,
                             exclude: list[Passkey], **extra) -> dict:
        options = generate_registration_options(
            rp_id=rp_id, rp_name=RP_NAME, user_id=handle, user_name=user_name, user_display_name=display,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.PREFERRED),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id)) for p in exclude],
        )
        _stash(request, db, kind, options.challenge, handle=bytes_to_base64url(handle), **extra)
        return json.loads(options_to_json(options))

    def save_credential(db: Session, user: User, pending: dict, body: Verify) -> Passkey:
        try:
            verified = verify_registration_response(
                credential=body.credential, expected_challenge=base64url_to_bytes(pending["challenge"]),
                expected_rp_id=rp_id, expected_origin=origin)
        except WEBAUTHN_ERRORS as exc:
            raise HTTPException(400, f"The passkey could not be verified: {exc}") from exc
        credential_id = bytes_to_base64url(verified.credential_id)
        if db.scalar(select(Passkey).where(Passkey.credential_id == credential_id)):
            raise HTTPException(409, "This passkey is already registered")
        if not db.scalar(select(Identity).where(Identity.user_id == user.id, Identity.provider == PROVIDER)):
            user.identities.append(Identity(provider=PROVIDER, subject=pending["handle"]))
        transports = (body.credential.get("response") or {}).get("transports") or []
        passkey = Passkey(user_id=user.id, credential_id=credential_id, public_key=verified.credential_public_key,
                          sign_count=verified.sign_count, transports=list(transports)[:8],
                          name=_label(body.credential, body.name), aaguid=verified.aaguid,
                          backed_up=verified.credential_backed_up)
        db.add(passkey)
        try:
            db.commit()
        except IntegrityError:  # raced: the same credential, or another device's first passkey (another handle)
            db.rollback()
            if db.scalar(select(Passkey.id).where(Passkey.credential_id == credential_id)):
                raise HTTPException(409, "This passkey is already registered") from None
            raise HTTPException(409, "A passkey was just added to this account from another device. Please try again.") from None
        return passkey

    # -- new account --------------------------------------------------------------------------
    @router.post("/signup/options", summary="Start creating an account with a passkey")
    def signup_options(body: Signup, request: Request, db: Session = Depends(get_db)) -> dict:
        require_enabled()
        name = (body.name or "").strip()[:200] or None
        handle = secrets.token_bytes(32)
        return registration_options(request, db, "signup", handle, name or "Vault account", name or "Vault account", [],
                                    name=name)

    @router.post("/signup/verify", summary="Finish creating an account with a passkey (signs you in)")
    def signup_verify(body: Verify, request: Request, db: Session = Depends(get_db)) -> dict:
        require_enabled()
        pending = _take(request, db, "signup")
        user = User(name=pending.get("name"))
        db.add(user)
        db.flush()
        passkey = save_credential(db, user, pending, body)
        sign_in(db, request, _profile(pending["handle"]), link=False)
        return {"signed_in": True, "user_id": user.id, "passkey_id": passkey.id}

    # -- another passkey for the signed-in account --------------------------------------------
    @router.post("/register/options", summary="Start adding a passkey to your account")
    def register_options(request: Request, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        require_enabled()
        identity = db.scalar(select(Identity).where(Identity.user_id == user.id, Identity.provider == PROVIDER))
        handle = base64url_to_bytes(identity.subject) if identity else secrets.token_bytes(32)
        existing = list(db.scalars(select(Passkey).where(Passkey.user_id == user.id)))
        label = user.email or user.name or "Vault account"
        return registration_options(request, db, "register", handle, label, user.name or label, existing, uid=user.id)

    @router.post("/register/verify", summary="Finish adding a passkey to your account")
    def register_verify(body: Verify, request: Request, user: User = Depends(account_user),
                        db: Session = Depends(get_db)) -> dict:
        require_enabled()
        pending = _take(request, db, "register")
        if pending.get("uid") != user.id:
            raise HTTPException(400, "This passkey request belongs to another session")
        # The account has one WebAuthn user handle. If another device added the first passkey
        # while this request was open, its handle won: a passkey made with ours could never sign
        # in, so start again (the account row is locked so two of these can't both decide).
        db.execute(select(User.id).where(User.id == user.id).with_for_update())
        identity = db.scalar(select(Identity).where(Identity.user_id == user.id, Identity.provider == PROVIDER))
        if identity is not None and identity.subject != pending["handle"]:
            raise HTTPException(409, "A passkey was just added to this account from another device. Please try again.")
        passkey = save_credential(db, user, pending, body)
        return {"added": True, "passkey_id": passkey.id}

    # -- sign in ------------------------------------------------------------------------------
    @router.post("/login/options", summary="Start signing in with a passkey")
    def login_options(request: Request, db: Session = Depends(get_db)) -> dict:
        require_enabled()
        options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.PREFERRED)
        _stash(request, db, "login", options.challenge)
        return json.loads(options_to_json(options))

    @router.post("/login/verify", summary="Finish signing in with a passkey")
    def login_verify(body: Verify, request: Request, db: Session = Depends(get_db)) -> dict:
        require_enabled()
        pending = _take(request, db, "login")
        credential_id = str(body.credential.get("id") or body.credential.get("rawId") or "")
        # Locked until commit: two sign-ins with one key take turns, so each is checked against
        # the count the other saved and the stored count never goes backwards.
        passkey = db.scalar(select(Passkey).where(Passkey.credential_id == credential_id).with_for_update())
        if passkey is None:
            raise HTTPException(401, "This passkey isn't registered with the Vault (it may have been removed)")
        try:
            verified = verify_authentication_response(
                credential=body.credential, expected_challenge=base64url_to_bytes(pending["challenge"]),
                expected_rp_id=rp_id, expected_origin=origin, credential_public_key=passkey.public_key,
                credential_current_sign_count=passkey.sign_count)
        except WEBAUTHN_ERRORS as exc:
            raise HTTPException(401, f"The passkey could not be verified: {exc}") from exc
        identity = db.scalar(select(Identity).where(Identity.user_id == passkey.user_id, Identity.provider == PROVIDER))
        handle = (body.credential.get("response") or {}).get("userHandle")
        if identity is None or (handle and handle != identity.subject):
            raise HTTPException(401, "This passkey belongs to a different account")
        # Saved only if the count is still the one checked above (SQLite ignores the row lock):
        # a count another sign-in saved meanwhile is never lowered; this one is refused instead.
        saved = db.execute(update(Passkey).where(Passkey.id == passkey.id, Passkey.sign_count == passkey.sign_count)
                           .values(sign_count=verified.new_sign_count, last_used_at=_now())
                           .execution_options(synchronize_session=False)).rowcount
        if not saved:
            db.rollback()
            raise HTTPException(401, "This passkey was just used for another sign-in; try again")
        db.commit()
        sign_in(db, request, _profile(identity.subject), link=False)
        return {"signed_in": True}

    return router


def remove_passkey(db: Session, user_id: int, passkey_id: int) -> None:
    """Delete one of the account's passkeys, unless it is the last way to sign in (409).

    Two removals running at the same time must not both pass the "another way to sign in is
    left" check. On Postgres the account row is locked first (``SELECT … FOR UPDATE``), so they
    take turns. SQLite ignores that lock, so the check is also part of the DELETE itself: one
    statement that removes the passkey only if another passkey or sign-in remains, evaluated
    when it runs (SQLite runs writers one at a time). The caller commits."""
    db.execute(select(User.id).where(User.id == user_id).with_for_update())
    passkey = db.get(Passkey, passkey_id, populate_existing=True)
    if passkey is None or passkey.user_id != user_id:
        raise HTTPException(404, "Passkey not found")
    db.expunge(passkey)
    passkeys = select(func.count(Passkey.id)).where(Passkey.user_id == user_id).scalar_subquery()
    other_sign_ins = select(func.count(Identity.id)).where(
        Identity.user_id == user_id, Identity.provider != PROVIDER).scalar_subquery()
    removed = db.execute(
        delete(Passkey).where(Passkey.id == passkey_id, Passkey.user_id == user_id, passkeys + other_sign_ins > 1)
        .execution_options(synchronize_session=False)).rowcount
    if not removed:
        raise HTTPException(409, "This is your only way to sign in. Add another passkey or sign-in method first.")
    # No passkeys left: the passkey identity goes too. Checked in the DELETE itself, so a passkey
    # registered at the same moment keeps the identity it needs to sign in.
    db.execute(delete(Identity).where(Identity.user_id == user_id, Identity.provider == PROVIDER,
                                      ~exists().where(Passkey.user_id == user_id))
               .execution_options(synchronize_session=False))
    db.flush()


def _profile(handle: str) -> Profile:
    return Profile(PROVIDER, handle, None, None)


def _now() -> datetime:
    return datetime.now(timezone.utc)
