"""The account lock: one way to take it, for everything that must be ordered against "Sign out the other browsers" (#347).

Step 1 of Sign out everywhere, every removal of a sign-in method and every route that mints something that outlives a
browser session (an app session, a personal access token, a code, a passkey) first lock the account's ``users`` row, then read
the session key, then insert or delete. Two details make that safe and live:

* **FOR NO KEY UPDATE, not FOR UPDATE.** A foreign-key insert (a refresh token, a consent, a retired token) takes ``FOR KEY SHARE``
  on the user row. ``FOR UPDATE`` conflicts with it, so a refresh holding an ``api_sessions`` row lock and waiting for the user row
  could deadlock with step 1, which holds the user row and deletes that ``api_sessions`` row. ``FOR NO KEY UPDATE`` still
  serialises everything that takes the same lock (they all do) and does not conflict with ``KEY SHARE``.
* **A short lock timeout.** Waiting for the lock can only mean another request of the same person is mid-way; a few seconds is
  plenty. Past that the caller gets the Vault's usual 503 with ``Retry-After`` (``OperationalError`` is answered that way) rather
  than a hung worker. ``SET LOCAL`` ends with the transaction.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import User

LOCK_TIMEOUT = "5s"


def lock_accounts(db: Session, user_ids: list[int]) -> None:
    """Lock several user rows at once, in id order (two links the other way round can't deadlock), with the same mode and the same
    timeout as :func:`lock_account`: the timeout is set BEFORE the first lock is asked for, and ``FOR NO KEY UPDATE`` does not
    conflict with the ``FOR KEY SHARE`` a refresh or a consent takes on the user row. (A claim that goes on to delete the other
    account asks for the stronger lock only at that DELETE, after everything else it holds is a plain row lock.)"""
    db.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    db.execute(select(User.id).where(User.id.in_(user_ids)).order_by(User.id).with_for_update(key_share=True))


def lock_account(db: Session, user_id: int) -> str | None:
    """Lock the user row (FOR NO KEY UPDATE, until the transaction ends) and return its session key (None: no such account or no
    key). Raises the database's ``LockNotAvailable`` (an ``OperationalError``, answered 503 with Retry-After) after the timeout."""
    db.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    return db.scalar(select(User.session_key).where(User.id == user_id).with_for_update(key_share=True))
