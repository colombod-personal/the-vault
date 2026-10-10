"""A recent sign-in for the serious account actions (#347), and the e-mailed code that can supply one.

**The claim.** ``sign_in`` (vault.auth) writes ``auth_at`` (seconds since the epoch, the server's clock) into the signed session
cookie when someone proves control of a sign-in method the account already had, and :func:`mark_recent` writes it when an
e-mailed code or link is confirmed. A provider sign-in that *links a new method* keeps the old value: a copied session that links
its own Google account is not made fresh by it. A cookie from before this release has no ``auth_at`` and is stale. An app
session (a bearer token the Vault app holds) is as old as its ``ApiSession`` row: signing in again makes a new one.
:func:`require_recent` answers 403 with the stable code ``recent_sign_in_required`` when the window (``RECENT_SIGNIN_SECONDS``,
ten minutes) has passed. Personal access tokens and connected apps never reach it: they have no account powers at all.

**The e-mailed code** (``/api/auth/recent/email/*``). The person asks for a code; the Vault mails a six-digit code and a link to the
address on the account. The code is typed into the browser that asked, or the link is opened anywhere and *approved with a
button* (opening it changes nothing, so a mail scanner or a link preview cannot spend it). Both end the same way: the asking
browser's cookie gets ``auth_at``. What makes it safe:

- the code and the link are bound to the account and to the browser session that asked (a keyed hash of the account's session
  key and that browser's request id), so a code someone else requested cannot be used by this session, nor the other way round
  without reading the mailbox;
- only keyed hashes are stored; the code lives ten minutes, works once and dies after five tries (counted before it is compared,
  so parallel guesses cannot exceed five); compared in constant time;
- at most three mails an hour for an account and :attr:`Settings.email_daily_cap` a day for all of them, then 429;
- the answers say only that a code went "to the address on this account" with the address masked (``***@e***.com``); the address is
  :func:`mail_address` (a provider-verified address that has been on the account for a day, never ``users.email``); an account with
  none, or a Vault with no sender configured, says so and offers the passkey and provider paths;
- the mail says who asked (browser and system, and when) and to ignore it if that was not the person.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import math
import re
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from . import tokens
from .config import Settings
from .email import EmailError, EmailMessage, mask_address, usable_address
from . import locks
from .models import ApiSession, EmailCode, Identity, User, utcnow
from .ratelimit import limited

log = logging.getLogger(__name__)

CODE = "recent_sign_in_required"
CODE_SECONDS = 600  # how long an e-mailed code or link works
MAX_TRIES = 5
SENDS_PER_HOUR = 3  # per account
SENDS_PER_DAY = 10  # per account, beside the Vault-wide EMAIL_DAILY_CAP
FAILED_TRIES_PER_HOUR = 10  # per account, summed over its codes: past this only the passkey and provider paths remain
RELAY_SUFFIX = "@privaterelay.appleid.com"
CAP_LOCK = 347_000_347  # pg_advisory_xact_lock key that makes the send caps exact when two accounts ask at once


def now_ts() -> float:
    """The clock ``auth_at`` is written and judged with (tests move it here)."""
    return time.time()


class CodedError(HTTPException):
    """An error with a stable ``code`` (and optional extra fields) in the problem document: the web app acts on the code."""

    def __init__(self, status: int, code: str, detail: str, headers: dict | None = None, **extra):
        super().__init__(status, detail, headers={"X-Error": code, **(headers or {})})
        self.code = code
        self.extra = extra


# -- the claim ---------------------------------------------------------------------------------

def mark_recent(request: Request) -> None:
    request.session["auth_at"] = int(now_ts())


def seconds_since_sign_in(db: Session, request: Request) -> float | None:
    """How long ago this caller signed in, or None when it never did in a way that counts (a cookie from before this release)."""
    bearer = getattr(request.state, "bearer", None)
    if bearer is not None:
        made = db.scalar(select(ApiSession.created_at).where(ApiSession.access_hash == tokens._hash(bearer)))
        return None if made is None else (utcnow() - made).total_seconds()
    at = request.session.get("auth_at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    age = now_ts() - at
    return age if age >= -60 else None  # a time in the future is not a sign-in


def seconds_left(db: Session, request: Request, settings: Settings) -> int:
    age = seconds_since_sign_in(db, request)
    return 0 if age is None else max(0, int(settings.recent_signin_seconds - age))


def is_fresh(db: Session, request: Request, settings: Settings) -> bool:
    return seconds_left(db, request, settings) > 0


def stale_error(settings: Settings | None = None) -> CodedError:
    seconds = settings.recent_signin_seconds if settings is not None else 600
    return CodedError(403, CODE, f"This needs a sign-in from the last {round(seconds / 60)} minutes. Confirm it's you: sign in again with "
                                 "a passkey or a linked sign-in, or ask for a code by e-mail.", window_seconds=seconds)


def require_recent(db: Session, request: Request, settings: Settings) -> None:
    if not is_fresh(db, request, settings):
        raise stale_error(settings)


def mail_address(db: Session, user_id: int) -> str | None:
    """The address a confirmation code goes to: the e-mail of the OLDEST linked provider that has one a provider vouches for
    (``Identity.email_verified``: Google and Apple say so in the ID token; Microsoft's and Facebook's addresses are never taken)
    and that has been on the account for more than 24 hours. Not ``users.email``, which is whatever the first provider said, unverified,
    and which a copied session could set on a passkey-only account by linking its own Google in a fresh window (found by review).
    A new account has none for a day (it confirms with a passkey or provider); an address leaves with the identity that gave it."""
    from .auth import RECENT_SIGN_IN_METHOD_HOURS

    since = utcnow() - timedelta(hours=RECENT_SIGN_IN_METHOD_HOURS)
    for address in db.scalars(select(Identity.email).where(
            Identity.user_id == user_id, Identity.provider != "passkey", Identity.email_verified.is_(True),
            Identity.email.is_not(None), Identity.created_at < since).order_by(Identity.created_at, Identity.id)):
        if usable_address(address):
            return address
    return None


def unregistered_relay(address: str, settings: Settings) -> bool:
    """An Apple private-relay address while the Vault's sender is not registered with Apple (APPLE_RELAY_REGISTERED): Apple would drop the
    mail, so it is not sent and the person is not told it was."""
    return address.lower().endswith(RELAY_SUFFIX) and not settings.apple_relay_registered


def failed_tries(db: Session, user_id: int, now) -> int:
    """Tries made at this account's codes in the last hour (every attempt counts, the one that worked too)."""
    return db.scalar(select(func.coalesce(func.sum(EmailCode.tries), 0)).where(
        EmailCode.user_id == user_id, EmailCode.created_at > now - timedelta(hours=1))) or 0


def attempts_exhausted() -> CodedError:
    return CodedError(429, "email_attempts_exhausted", "Too many wrong codes for this account in the last hour. Confirm with a passkey or "
                                                      "a sign-in method you have linked, or try again later.",
                      headers={"Retry-After": "3600"}, retry_after_seconds=3600)


def status(db: Session, request: Request, user: User, settings: Settings) -> dict:
    sender = getattr(request.app.state, "email_sender", None)
    by_app = getattr(request.state, "bearer", None) is not None
    address = mail_address(db, user.id) if sender is not None and not by_app else None
    reason = None if address else "app" if by_app else "no_sender" if sender is None else "no_address"
    if address and unregistered_relay(address, settings):
        address, reason = None, "relay_unregistered"
    return {"fresh": is_fresh(db, request, settings), "seconds_left": seconds_left(db, request, settings),
            "window_seconds": settings.recent_signin_seconds,
            "email": {"available": address is not None, "to": mask_address(address) if address else None, "reason": reason}}


# -- the code ----------------------------------------------------------------------------------

def _mac(settings: Settings, *parts: object) -> str:
    return hmac.new(settings.session_secret.encode(), ("email-code:" + "\x1f".join(str(p) for p in parts)).encode(),
                    hashlib.sha256).hexdigest()


def session_digest(settings: Settings, user: User, request: Request, rid: str | None = None) -> str | None:
    """Which browser session a code belongs to: the account's session key (so a sign-out ends it) and this browser's request id
    (so another browser on the same account is another session). None until this browser has asked for a code."""
    rid = rid or request.session.get("rid")
    return _mac(settings, "session", user.id, user.session_key, rid) if isinstance(rid, str) and rid else None


def describe_browser(user_agent: str) -> str:
    """"Chrome on Windows": what the mail says asked. Coarse on purpose; no address is stored or shown."""
    ua = user_agent or ""
    os_name = next((name for pattern, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                                              ("Mac OS X", "a Mac"), ("Macintosh", "a Mac"), ("CrOS", "ChromeOS"), ("Linux", "Linux"))
                    if pattern in ua), None)
    browser = next((name for pattern, name in (("Edg/", "Edge"), ("EdgA/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                              ("FxiOS/", "Firefox"), ("CriOS/", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
                    if pattern in ua), None)
    if browser and os_name:
        return f"{browser} on {os_name}"
    return browser or (f"a browser on {os_name}" if os_name else "an unknown browser")


def _message(settings: Settings, to: str, code: str, token: str, asked_from: str, when: str) -> EmailMessage:
    link = f"{settings.base_url}/api/auth/recent/email/link?token={token}"
    minutes = round(CODE_SECONDS / 60)
    text = (f"Someone asked to confirm it's you on your Vault account.\n"
            f"Asked from: {asked_from}, {when}.\n\n"
            f"Your code: {code}\n"
            f"Type it into the page that asked. It works once and expires in {minutes} minutes.\n\n"
            f"Or open this link on any device and press Approve:\n{link}\n\n"
            "If this was not you, ignore this e-mail: nothing happens unless the code or the link is used. "
            "If you keep getting these, sign out everywhere under Account in the Vault.\n")
    page = (f"<p>Someone asked to confirm it's you on your Vault account.<br>Asked from: <strong>{html.escape(asked_from)}</strong>, "
            f"{html.escape(when)}.</p><p>Your code: <strong style=\"font-size:20px;letter-spacing:2px\">{code}</strong><br>"
            f"Type it into the page that asked. It works once and expires in {minutes} minutes.</p>"
            f"<p>Or <a href=\"{html.escape(link)}\">open this link</a> on any device and press Approve.</p>"
            "<p>If this was not you, ignore this e-mail: nothing happens unless the code or the link is used. "
            "If you keep getting these, sign out everywhere under Account in the Vault.</p>")
    return EmailMessage(to=to, subject="Confirm it's you on The Vault", text=text, html=page)


LINK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<meta name="referrer" content="same-origin"><title>Confirm it's you</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem;color:#1b1b1b;background:#fff}}
button{{font:inherit;min-height:44px;padding:.6rem 1.2rem;margin:.25rem .5rem 0 0;border-radius:.5rem;border:1px solid #555;background:#fff;color:#1b1b1b;cursor:pointer}}
button[value=approve]{{background:#1b1b1b;color:#fff}}a{{color:inherit}}
@media (prefers-color-scheme:dark){{body{{background:#121212;color:#eee}}button{{background:#222;color:#eee}}button[value=approve]{{background:#eee;color:#121212}}}}</style>
</head><body><h1>{title}</h1>{body}</body></html>"""

# same-origin, not no-referrer: with no-referrer a browser sends `Origin: null` on the Approve form's POST, and the cross-site-write guard
# (vault.app.reject_cross_site_writes) would refuse it. The token in the address still never leaves this site in a Referer.
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'; default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"}


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(LINK_PAGE.format(title=html.escape(title), body=body), status_code=status_code, headers=NO_STORE)


class ConfirmBody(BaseModel):
    code: str = Field(min_length=1, max_length=20, description="The six digits from the e-mail")


def build_router(settings: Settings, get_db, account_user) -> APIRouter:
    router = APIRouter(prefix="/api/auth/recent", tags=["auth"])

    def person_only(request: Request) -> None:
        if getattr(request.state, "bearer", None) is not None:
            raise CodedError(403, "app_sign_in_required", "An app confirms by signing in again in the app; e-mailed codes are for the website.")

    def pending(db: Session, user: User, digest: str):
        return db.scalar(select(EmailCode).where(
            EmailCode.user_id == user.id, EmailCode.session_digest == digest, EmailCode.used_at.is_(None),
            EmailCode.expires_at > utcnow()).order_by(EmailCode.id.desc()).limit(1))

    def done(request: Request) -> dict:
        mark_recent(request)
        return {"fresh": True, "seconds_left": settings.recent_signin_seconds, "window_seconds": settings.recent_signin_seconds}

    @router.post("/email/start", dependencies=limited("recent-email-start", verify=True),
                 summary="E-mail a one-time code and link to the address on the account, to confirm it's you")
    def start(request: Request, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        person_only(request)
        sender = getattr(request.app.state, "email_sender", None)
        if sender is None:
            raise CodedError(409, "email_unavailable", "E-mailed codes are not available on this Vault. Confirm with a passkey or "
                                                        "a sign-in method you have linked.")
        address = mail_address(db, user.id)
        if address is None:
            raise CodedError(409, "no_email_on_account", "This account has no e-mail address a sign-in provider vouches for that has been "
                                                          "on it for a day, so a code cannot be sent. Confirm with a passkey or a sign-in "
                                                          "method you have linked.")
        if unregistered_relay(address, settings):
            raise CodedError(409, "email_relay_unregistered", "This account's address is an Apple private-relay address, and Apple only "
                                                              "forwards mail from senders registered with it, which the Vault's is not yet. "
                                                              "Nothing was sent. Confirm with a passkey or a sign-in method you have linked.")
        session_key = user.session_key
        now = utcnow()
        # The caps are counted under locks (the Vault-wide one first, then the account's), so two requests at once cannot both pass. Both
        # waits are bounded (vault.locks: 5 s, then the usual 503 with Retry-After): a lock held for long by one request cannot stall every
        # account's ask, and the account lock is the shared one (FOR NO KEY UPDATE) every other account-level route takes.
        db.execute(text(f"SET LOCAL lock_timeout = '{locks.LOCK_TIMEOUT}'"))
        db.execute(select(func.pg_advisory_xact_lock(CAP_LOCK)))
        if session_key != locks.lock_account(db, user.id):
            db.rollback()
            raise HTTPException(401, "Your session ended. Sign in again.")
        if failed_tries(db, user.id, now) >= FAILED_TRIES_PER_HOUR:
            db.rollback()
            raise attempts_exhausted()
        if db.scalar(select(func.count()).where(EmailCode.user_id == user.id, EmailCode.created_at > now - timedelta(days=1))) >= SENDS_PER_DAY:
            db.rollback()
            raise CodedError(429, "email_account_daily_limit", f"You asked for {SENDS_PER_DAY} codes in the last day. Confirm with a passkey "
                                                              "or a sign-in method you have linked, or try again tomorrow.",
                             headers={"Retry-After": "3600"}, retry_after_seconds=3600)
        hour = db.execute(select(func.count(), func.min(EmailCode.created_at)).where(
            EmailCode.user_id == user.id, EmailCode.created_at > now - timedelta(hours=1))).one()
        if hour[0] >= SENDS_PER_HOUR:
            wait = max(1, math.ceil((hour[1] + timedelta(hours=1) - now).total_seconds()))
            db.rollback()
            raise CodedError(429, "email_hourly_limit", f"You asked for {SENDS_PER_HOUR} codes in the last hour. Use the last one, "
                                                        "or confirm with a passkey or a linked sign-in.",
                             headers={"Retry-After": str(wait)}, retry_after_seconds=wait)
        if db.scalar(select(func.count()).where(EmailCode.created_at > now - timedelta(hours=24))) >= settings.email_daily_cap:
            db.rollback()
            raise CodedError(429, "email_daily_cap", "The Vault has sent all the sign-in e-mails it can for today. Confirm with a "
                                                     "passkey or a linked sign-in, or try again tomorrow.",
                             headers={"Retry-After": "3600"}, retry_after_seconds=3600)
        before = session_digest(settings, user, request)  # the codes this cookie held until now
        new_rid = secrets.token_urlsafe(16)  # a new request id on every ask: a copy of the cookie taken before no longer matches
        digest = session_digest(settings, user, request, new_rid)
        code, token = f"{secrets.randbelow(10 ** 6):06d}", secrets.token_urlsafe(32)
        asked_from = describe_browser(request.headers.get("user-agent", ""))[:80]
        row = EmailCode(user_id=user.id, session_digest=digest, code_hash=_mac(settings, "code", user.id, digest, code),
                        link_hash=_mac(settings, "link", token), asked_from=asked_from, created_at=now,
                        expires_at=now + timedelta(seconds=CODE_SECONDS))
        db.add(row)
        db.commit()  # counted from here on; deleted again below if the mail does not go
        row_id = row.id
        try:
            sender.send(_message(settings, address, code, token, asked_from, now.strftime("%H:%M UTC")))
        except EmailError as exc:
            log.warning("a sign-in code could not be sent: %s", exc.reason)
            db.execute(EmailCode.__table__.delete().where(EmailCode.id == row_id))
            db.commit()
            raise CodedError(502, "email_send_failed", "The e-mail could not be sent just now. Try again in a minute, or confirm "
                                                        "with a passkey or a linked sign-in.") from None
        # Only now do the earlier codes of this browser stop working: a failed send leaves the last good one alone.
        request.session["rid"] = new_rid  # only now: a send that failed leaves this browser's last good code usable
        if before is not None:  # the codes asked for from this browser before (and from any copy of its old cookie) stop here
            db.execute(update(EmailCode).where(EmailCode.user_id == user.id, EmailCode.session_digest == before,
                                               EmailCode.used_at.is_(None)).values(used_at=utcnow()))
        db.commit()
        log.info("a sign-in code was sent")  # never the address or the code
        return {"sent": True, "to": mask_address(address), "expires_in": CODE_SECONDS}

    @router.post("/email/confirm", dependencies=limited("recent-email-confirm", verify=True),
                 summary="Confirm it's you with the six-digit code from the e-mail")
    def confirm(body: ConfirmBody, request: Request, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        person_only(request)
        typed = re.sub(r"[\s-]", "", body.code)
        if not re.fullmatch(r"\d{6}", typed):
            raise CodedError(400, "invalid_code", "The code is six digits.")
        digest = session_digest(settings, user, request)
        row = pending(db, user, digest) if digest else None
        if row is None:
            raise CodedError(400, "no_code", "No code is waiting for this browser, or it expired. Ask for a new one.")
        if failed_tries(db, user.id, utcnow()) >= FAILED_TRIES_PER_HOUR:  # the account's budget, over all its codes and sessions
            raise attempts_exhausted()
        # The try is counted first and in the UPDATE itself: five guesses at once still make five.
        tries = db.execute(update(EmailCode).where(EmailCode.id == row.id, EmailCode.used_at.is_(None), EmailCode.tries < MAX_TRIES,
                                                   EmailCode.expires_at > utcnow()).values(tries=EmailCode.tries + 1)
                           .returning(EmailCode.tries)).scalar()
        db.commit()
        if tries is None:
            raise CodedError(400, "code_dead", "That code has been tried too often or has expired. Ask for a new one.")
        if not hmac.compare_digest(row.code_hash, _mac(settings, "code", user.id, digest, typed)):
            left = MAX_TRIES - tries
            raise CodedError(400, "wrong_code", ("That code is not right. " + (f"{left} tr{'y' if left == 1 else 'ies'} left."
                                                 if left else "That was the last try: ask for a new code.")), tries_left=left)
        spent = db.execute(update(EmailCode).where(EmailCode.id == row.id, EmailCode.used_at.is_(None)).values(used_at=utcnow())
                           .returning(EmailCode.id)).scalar()
        db.commit()
        if spent is None:  # the link was used meanwhile, or another request spent it
            raise CodedError(400, "no_code", "No code is waiting for this browser, or it expired. Ask for a new one.")
        return done(request)

    @router.post("/email/poll", dependencies=limited("recent-email-poll", setting="auth_rate_limit"),
                 summary="Has the link in the e-mail been approved? If so, this browser is confirmed")
    def poll(request: Request, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        person_only(request)
        digest = session_digest(settings, user, request)
        if digest is None:
            return {"fresh": False, "waiting": False}
        spent = db.execute(update(EmailCode).where(
            EmailCode.user_id == user.id, EmailCode.session_digest == digest, EmailCode.approved_at.is_not(None),
            EmailCode.used_at.is_(None), EmailCode.expires_at > utcnow()).values(used_at=utcnow()).returning(EmailCode.id)).first()
        db.commit()
        if spent is not None:
            return done(request)
        return {"fresh": False, "waiting": pending(db, user, digest) is not None}

    # -- the link: opening it shows a page; only the button changes anything ------------------------
    def find_link(db: Session, token: str) -> EmailCode | None:
        row = db.scalar(select(EmailCode).where(EmailCode.link_hash == _mac(settings, "link", token)))
        if row is None or row.used_at is not None or row.expires_at <= utcnow():
            return None
        return row

    gone = lambda: _page("This link no longer works", "<p>It was already used, or it expired (links last "  # noqa: E731
                         f"{round(CODE_SECONDS / 60)} minutes). Go back to the Vault and ask for a new code.</p>", 410)

    @router.get("/email/link", response_class=HTMLResponse, include_in_schema=False,
                dependencies=limited("recent-email-link", verify=True))
    def link_page(token: str = Query(..., max_length=100), db: Session = Depends(get_db)):
        """What the e-mail's link opens. A GET never approves anything: mail scanners and link previews open links, so the
        button (a POST) is what counts."""
        row = find_link(db, token)
        if row is None:
            return gone()
        address = mail_address(db, row.user_id)
        minutes = max(0, int((utcnow() - row.created_at).total_seconds() / 60))
        body = (f"<p>Someone asked to confirm it's you on the Vault account {html.escape(mask_address(address) if address else '')}."
                f"<br>Asked from <strong>{html.escape(row.asked_from)}</strong>, {minutes} minute{'' if minutes == 1 else 's'} ago.</p>"
                "<p>Approve only if you just asked for this. If not, press &ldquo;This wasn't me&rdquo;.</p>"
                '<form method="post" action="/api/auth/recent/email/link">'
                f'<input type="hidden" name="token" value="{html.escape(token)}">'
                '<button type="submit" name="decision" value="approve">Approve</button>'
                "<button type=\"submit\" name=\"decision\" value=\"deny\">This wasn't me</button></form>")
        return _page("Confirm it's you", body)

    @router.post("/email/link", response_class=HTMLResponse, include_in_schema=False,
                 dependencies=limited("recent-email-link", verify=True))
    def link_decide(request: Request, token: str = Form(..., max_length=100), decision: str = Form(...),
                    db: Session = Depends(get_db)):
        from .auth import session_user

        row = find_link(db, token)
        if row is None:
            return gone()
        if decision != "approve":
            db.execute(update(EmailCode).where(EmailCode.id == row.id).values(used_at=utcnow()))
            db.commit()
            return _page("Nothing was approved", "<p>The request is cancelled. If you did not ask for it, you can ignore the e-mail.</p>")
        db.execute(update(EmailCode).where(EmailCode.id == row.id, EmailCode.used_at.is_(None), EmailCode.approved_at.is_(None),
                                           EmailCode.expires_at > utcnow()).values(approved_at=utcnow()))  # twice is the same as once
        db.commit()
        if find_link(db, token) is None:
            return gone()
        # Opened in the browser that asked: confirm it now. Anywhere else: the asking browser picks it up (/email/poll).
        me = session_user(db, request)
        if me is not None and me.id == row.user_id and hmac.compare_digest(session_digest(settings, me, request) or "", row.session_digest):
            spent = db.execute(update(EmailCode).where(EmailCode.id == row.id, EmailCode.used_at.is_(None)).values(used_at=utcnow())
                               .returning(EmailCode.id)).scalar()
            db.commit()
            if spent is not None:
                mark_recent(request)
                return _page("Confirmed", '<p>You are confirmed for the next '
                                          f'{round(settings.recent_signin_seconds / 60)} minutes. <a href="/">Back to the Vault</a>.</p>')
        return _page("Approved", "<p>Go back to the page that asked: it carries on within a few seconds. "
                                 f'<a href="/">Open the Vault</a>.</p>')

    return router
