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

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .importer import export_collection
from .sharing import display_name
from .models import AccessToken, ApiSession, Bucket, BucketBaseline, CardAnnotation, TagAssignment, OAuthClient, OAuthCode, OAuthConsent, OAuthGrant, OAuthRetiredRefresh, RetiredRefreshToken, Passkey, IdempotentRequest, AuthCode, CollectionBaseline, CollectionValue, Deck, DeckVersion, Entry, Identity, Import, Share, StagedUpload, User
from .prices import history
from .collection_view import CollectionView

README = """\
The Vault: your data export
===========================

Created {created} for account #{user_id}.

account.json         your profile and the sign-in methods linked to it
collection.csv       your collection in Dragon Shield's CSV format (re-importable)
collection-moxfield.csv   the same, ready for Moxfield (Collection → More → Import CSV)
collection-generic.csv    the same with every field and Scryfall ids, for any other app
collection.json      your collection as the app shows it, with current prices
imports.json         every CSV you imported, with what changed each time (and which edits made in the Vault were kept)
last_import_cards.json  the cards of the last file you imported, as they were: what the next import is compared with
value_history.json   your collection's daily market value and cost
decks.json           your saved decks with their earlier versions (each deck also as a .txt file in decks/)
shares.json          who you have given access to, and what others have shared with you
app_sessions.json    apps signed in to your account (tokens are never exported)
passkeys.json        passkeys that can sign in to your account (names and dates; keys stay on your devices)
access_tokens.json   personal access tokens you created for agents and scripts (names and dates only)
connected_apps.json  apps you connected with OAuth, such as ChatGPT or Claude (name, what you allowed, dates; never tokens)
buckets.json         the places your copies are grouped in (one per folder of your file, and Unsorted), with how many copies each holds
tags.json            the tags you (or an assistant you allowed) put on cards, and who wrote each
card_annotations.json   notes about cards written by you or an assistant

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
    """Other people in your export are named as sharing names them: never by e-mail address."""
    return display_name(user) if user else None


def _buckets(db: Session, user: User) -> list[dict]:
    copies = dict(db.execute(select(Entry.bucket_id, func.coalesce(func.sum(Entry.quantity), 0))
                             .where(Entry.user_id == user.id).group_by(Entry.bucket_id)).all())
    return [{"name": b.name, "kind": b.kind, "position": b.position, "copies": int(copies.get(b.id, 0)),
             "metadata": b.vault_metadata, "created_at": b.created_at}
            for b in db.scalars(select(Bucket).where(Bucket.user_id == user.id).order_by(Bucket.position, Bucket.id))]


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
        z.writestr("collection.csv", export_collection(db, user, "dragonshield"))
        # the same collection in formats other apps import, so you can take it anywhere (Art. 20)
        z.writestr("collection-moxfield.csv", export_collection(db, user, "moxfield"))
        z.writestr("collection-generic.csv", export_collection(db, user, "csv"))
        view = CollectionView(db, user)
        z.writestr("collection.json", _json({"summary": view.summary(), "sets": view.sets(),
                                             "cards": [view.item(g) for g in view.groups]}))
        z.writestr("imports.json", _json([
            {"filename": i.filename, "imported_at": i.created_at, "rows": i.rows, "copies": i.copies,
             "changes": i.summary, **({"merge": i.changes["merge"]} if (i.changes or {}).get("merge") else {})}
            for i in imports
        ]))
        baseline = db.get(CollectionBaseline, user.id)
        z.writestr("last_import_cards.json", _json({
            "imported_at": baseline.created_at if baseline else None,
            "cards": [{"name": c["n"], "set": c["s"], "number": c["c"], "finish": c["f"], "copies": c["q"], "rows": c["r"]}
                      for c in (baseline.cards if baseline else [])],
            "columns_of_rows": ["condition", "language", "folder", "price_paid", "date_paid", "quantity", "trade_quantity"],
            # the last file imported into one bucket (#124), for each bucket that had one
            "by_bucket": [{"bucket": name, "imported_at": b.created_at,
                           "cards": [{"name": c["n"], "set": c["s"], "number": c["c"], "finish": c["f"], "copies": c["q"],
                                      "rows": c["r"]} for c in b.cards]}
                          for b, name in db.execute(select(BucketBaseline, Bucket.name).join(Bucket, Bucket.id == BucketBaseline.bucket_id)
                                                    .where(BucketBaseline.user_id == user.id).order_by(Bucket.position, Bucket.id))]}))
        z.writestr("value_history.json", _json(history(db, user)))
        z.writestr("buckets.json", _json(_buckets(db, user)))
        z.writestr("tags.json", _json([{"card": t.oracle_id, "tag": t.tag, "source": t.source, "source_detail": t.source_detail,
                                        "metadata": t.vault_metadata, "created_at": t.created_at}
                                       for t in db.scalars(select(TagAssignment).where(TagAssignment.user_id == user.id)
                                                           .order_by(TagAssignment.id))]))
        z.writestr("card_annotations.json", _json([{"card": a.oracle_id, "source": a.source, "source_detail": a.source_detail,
                                                    "metadata": a.vault_metadata, "created_at": a.created_at,
                                                    "updated_at": a.updated_at}
                                                   for a in db.scalars(select(CardAnnotation).where(CardAnnotation.user_id == user.id)
                                                                       .order_by(CardAnnotation.id))]))
        z.writestr("decks.json", _json([
            {"id": d.id, "name": d.name, "source_url": d.source_url, "source_author": d.source_author,
             "source_fetched_at": d.source_fetched_at, "created_at": d.created_at,
             "updated_at": d.updated_at, "text": d.text,
             "versions": [{"id": v.id, "created_at": v.created_at, "source": v.source, "text": v.text}
                          for v in db.scalars(select(DeckVersion).where(DeckVersion.deck_id == d.id).order_by(DeckVersion.id))]}
            for d in decks
        ]))
        for d in decks:
            safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in d.name).strip() or "deck"
            z.writestr(f"decks/{d.id}-{safe[:60]}.txt", d.text)
        z.writestr("shares.json", _json(shares))
        z.writestr("passkeys.json", _json([
            {"name": p.name, "synced": p.backed_up, "created_at": p.created_at.isoformat(),
             "last_used_at": p.last_used_at and p.last_used_at.isoformat()}
            for p in db.scalars(select(Passkey).where(Passkey.user_id == user.id))
        ]))
        z.writestr("access_tokens.json", _json([
            {"name": t.name, "prefix": t.prefix, "scopes": t.scopes.split(), "created_at": t.created_at.isoformat(),
             "expires_at": t.expires_at.isoformat(), "last_used_at": t.last_used_at and t.last_used_at.isoformat()}
            for t in db.scalars(select(AccessToken).where(AccessToken.user_id == user.id))
        ]))
        z.writestr("connected_apps.json", _json(_connected_apps(db, user)))
        z.writestr("app_sessions.json", _json([
            {"client": a.client, "device_name": a.device_name, "created_at": a.created_at.isoformat(),
             "last_used_at": a.last_used_at and a.last_used_at.isoformat()}
            for a in db.scalars(select(ApiSession).where(ApiSession.user_id == user.id))
        ]))
    return buf.getvalue()


def _connected_apps(db: Session, user: User) -> list[dict]:
    names = {c.client_id: c.name for c in db.scalars(select(OAuthClient))}
    return [
        {"app": names.get(g.client_id, g.client_id), "client_id": g.client_id, "allowed": g.scopes.split(),
         "resource": g.resource, "connected_at": g.created_at.isoformat(),
         "last_used_at": g.last_used_at and g.last_used_at.isoformat()}
        for g in db.scalars(select(OAuthGrant).where(OAuthGrant.user_id == user.id).order_by(OAuthGrant.id))
    ]


def personal_data(user_id: int) -> dict:
    """DELETE statements for every table holding this user's data, in a safe order.

    tests/test_privacy.py checks that every table referencing ``users`` is listed
    here, so a new table can't be forgotten on account deletion.
    """
    return {
        "retired_refresh_tokens": delete(RetiredRefreshToken).where(RetiredRefreshToken.user_id == user_id),
        "oauth_retired_refresh_tokens": delete(OAuthRetiredRefresh).where(OAuthRetiredRefresh.user_id == user_id),
        "oauth_consents": delete(OAuthConsent).where(OAuthConsent.user_id == user_id),
        "staged_uploads": delete(StagedUpload).where(StagedUpload.user_id == user_id),  # a file not yet imported
        "oauth_codes": delete(OAuthCode).where(OAuthCode.user_id == user_id),
        "oauth_grants": delete(OAuthGrant).where(OAuthGrant.user_id == user_id),
        "api_sessions": delete(ApiSession).where(ApiSession.user_id == user_id),
        "access_tokens": delete(AccessToken).where(AccessToken.user_id == user_id),
        "passkeys": delete(Passkey).where(Passkey.user_id == user_id),
        "idempotent_requests": delete(IdempotentRequest).where(IdempotentRequest.user_id == user_id),
        "auth_codes": delete(AuthCode).where(AuthCode.user_id == user_id),
        "shares": delete(Share).where(or_(Share.owner_id == user_id, Share.grantee_id == user_id)),
        "decks": delete(Deck).where(Deck.user_id == user_id),
        "tag_assignments": delete(TagAssignment).where(TagAssignment.user_id == user_id),
        "card_annotations": delete(CardAnnotation).where(CardAnnotation.user_id == user_id),
        "entries": delete(Entry).where(Entry.user_id == user_id),
        "bucket_baselines": delete(BucketBaseline).where(BucketBaseline.user_id == user_id),  # before the buckets they belong to
        "buckets": delete(Bucket).where(Bucket.user_id == user_id),  # after the entries: a bucket with copies can't be deleted
        "collection_baselines": delete(CollectionBaseline).where(CollectionBaseline.user_id == user_id),
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
