"""GDPR: data export (access / portability) and account erasure.

* :func:`export_archive` builds a ZIP of everything held about a user
  (Art. 15 right of access, Art. 20 portability: CSV and JSON).
* :func:`purge_user` deletes every row that belongs to a user (Art. 17), explicitly,
  table by table, so it doesn't depend on the database enforcing ON DELETE CASCADE.

Shared reference data (``cards``, ``price_snapshots``) is public Scryfall data
about printings, not about people, and is not touched.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .importer import export_dragonshield
from .models import CollectionValue, Deck, Entry, Identity, Import, Share, User
from .prices import history
from .vault_json import build

README = """\
The Vault: your data export
===========================

Created {created} for account #{user_id}.

account.json         your profile and the sign-in methods linked to it
collection.csv       your collection in Dragon Shield's CSV format (re-importable)
collection.json      your collection as the app shows it, with current prices
imports.json         every CSV you imported, with what changed each time
value_history.json   your collection's daily market value and cost
decks.json           your saved decks (each also as a .txt file in decks/)
shares.json          who you have given access to, and what others have shared with you

Card data, images and prices come from Scryfall (https://scryfall.com), which sources prices
from TCGplayer and Cardmarket. They are not personal data. Thank you, Scryfall.
Full credits: /credits.html in the app.

The Vault is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed
by Wizards. Portions of the materials used are property of Wizards of the Coast. (c)Wizards of the Coast LLC.
"""


def _json(data) -> bytes:
    def default(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        raise TypeError(type(o))

    return json.dumps(data, indent=2, ensure_ascii=False, default=default).encode()


def _display(user: User | None) -> str | None:
    return (user.name or user.email) if user else None


def export_archive(db: Session, user: User) -> bytes:
    decks = list(db.scalars(select(Deck).where(Deck.user_id == user.id).order_by(Deck.id)))
    imports = db.scalars(select(Import).where(Import.user_id == user.id).order_by(Import.created_at))
    outgoing = db.scalars(select(Share).where(Share.owner_id == user.id))
    incoming = db.scalars(select(Share).where(Share.grantee_id == user.id))

    account = {
        "id": user.id, "name": user.name, "email": user.email, "created_at": user.created_at,
        "sign_in_methods": [
            {"provider": i.provider, "subject": i.subject, "email": i.email, "linked_at": i.created_at}
            for i in user.identities
        ],
    }
    shares = {
        "given": [
            {"what": s.kind, "deck_id": s.deck_id, "shows_prices_paid": s.show_costs,
             "with": _display(db.get(User, s.grantee_id)) if s.grantee_id else "(invite not accepted yet)",
             "created_at": s.created_at, "accepted_at": s.accepted_at, "expires_at": s.expires_at}
            for s in outgoing
        ],
        "received": [
            {"what": s.kind, "deck_id": s.deck_id, "from": _display(db.get(User, s.owner_id)),
             "accepted_at": s.accepted_at}
            for s in incoming
        ],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        now = datetime.now(timezone.utc)
        z.writestr("README.txt", README.format(created=now.isoformat(timespec="seconds"), user_id=user.id))
        z.writestr("account.json", _json(account))
        z.writestr("collection.csv", export_dragonshield(db, user))
        z.writestr("collection.json", _json(build(db, user)))
        z.writestr("imports.json", _json([
            {"filename": i.filename, "imported_at": i.created_at, "rows": i.rows, "copies": i.copies,
             "changes": i.summary}
            for i in imports
        ]))
        z.writestr("value_history.json", _json(history(db, user)))
        z.writestr("decks.json", _json([
            {"id": d.id, "name": d.name, "source_url": d.source_url, "created_at": d.created_at,
             "updated_at": d.updated_at, "text": d.text}
            for d in decks
        ]))
        for d in decks:
            safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in d.name).strip() or "deck"
            z.writestr(f"decks/{d.id}-{safe[:60]}.txt", d.text)
        z.writestr("shares.json", _json(shares))
    return buf.getvalue()


def personal_data(user_id: int) -> dict:
    """DELETE statements for every table holding this user's data, in a safe order.

    tests/test_privacy.py checks that every table referencing ``users`` is listed
    here, so a new table can't be forgotten on account deletion.
    """
    return {
        "shares": delete(Share).where(or_(Share.owner_id == user_id, Share.grantee_id == user_id)),
        "decks": delete(Deck).where(Deck.user_id == user_id),
        "entries": delete(Entry).where(Entry.user_id == user_id),
        "imports": delete(Import).where(Import.user_id == user_id),
        "collection_values": delete(CollectionValue).where(CollectionValue.user_id == user_id),
        "identities": delete(Identity).where(Identity.user_id == user_id),
        "users": delete(User).where(User.id == user_id),
    }


def purge_user(db: Session, user_id: int) -> dict[str, int]:
    """Delete everything that belongs to ``user_id``. Returns rows removed per table."""
    removed = {name: db.execute(stmt).rowcount or 0 for name, stmt in personal_data(user_id).items()}
    db.commit()
    return removed
