"""A saved deck's versions (issue #93): the list as it was each time its cards changed.

A version is recorded when a deck is saved, edited or refreshed from its link and its cards differ from the latest
version; a save that changes nothing (a new name, a different order, another spelling of the same cards) records nothing.
Each deck keeps at most ``KEEP`` versions, the oldest dropped first. A version is the decklist text as saved, with a
fingerprint of its cards; it belongs to the deck and goes with it (deleting the deck, or the account, deletes them).
What changed between two versions is read from their texts (``deck_refresh.diff``), never stored.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import deck_refresh
from .models import Deck, DeckVersion

KEEP = 20
SOURCES = ("saved", "edited", "imported", "refreshed")


def card_fingerprint(text: str) -> str:
    """The same for lists with the same cards in the same sections, whatever their spelling, order or comments."""
    cards = sorted((section, name, qty) for (section, name), (_, qty) in deck_refresh._cards(text).items())
    return hashlib.sha256(repr(cards).encode()).hexdigest()[:20]


def record(db: Session, deck: Deck, source: str, now: datetime | None = None) -> DeckVersion | None:
    """Record the deck's list as a new version if its cards differ from the latest one. Returns it, or None if unchanged."""
    assert source in SOURCES, source
    fingerprint = card_fingerprint(deck.text)
    latest = db.scalar(select(DeckVersion).where(DeckVersion.deck_id == deck.id).order_by(DeckVersion.id.desc()).limit(1))
    if latest is not None and latest.fingerprint == fingerprint:
        return None
    version = DeckVersion(deck_id=deck.id, text=deck.text, fingerprint=fingerprint, source=source,
                          created_at=now or datetime.now(timezone.utc))
    db.add(version)
    db.flush()
    stale = db.scalars(select(DeckVersion.id).where(DeckVersion.deck_id == deck.id).order_by(DeckVersion.id.desc()).offset(KEEP)).all()
    if stale:
        db.execute(delete(DeckVersion).where(DeckVersion.id.in_(stale)))
    return version


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.replace(tzinfo=timezone.utc).isoformat()


def versions(db: Session, deck: Deck) -> list[dict]:
    """The deck's versions, newest first, each with what changed from the one before it (the oldest kept has no ``changes``)."""
    rows = list(db.scalars(select(DeckVersion).where(DeckVersion.deck_id == deck.id).order_by(DeckVersion.id.desc())))
    out = []
    for i, v in enumerate(rows):
        entry = {"id": v.id, "created_at": _iso(v.created_at), "source": v.source,
                 "cards": sum(qty for _, qty in deck_refresh._cards(v.text).values()), "changes": None, "summary": None}
        if i + 1 < len(rows):
            entry["changes"] = deck_refresh.diff(rows[i + 1].text, v.text)
            entry["summary"] = deck_refresh.summary(entry["changes"])
        out.append(entry)
    return out


def last_change(db: Session, deck: Deck) -> dict | None:
    """What changed in the latest version against the one before it, or None while the deck has only one version."""
    rows = db.scalars(select(DeckVersion).where(DeckVersion.deck_id == deck.id).order_by(DeckVersion.id.desc()).limit(2)).all()
    if len(rows) < 2:
        return None
    changes = deck_refresh.diff(rows[1].text, rows[0].text)
    return {"at": _iso(rows[0].created_at), "version": rows[0].id, "source": rows[0].source, "changes": changes,
            "summary": deck_refresh.summary(changes),
            "note": "Between the previous saved version of this deck and the current list."}


def get(db: Session, deck: Deck, version_id: int) -> DeckVersion | None:
    return db.scalar(select(DeckVersion).where(DeckVersion.deck_id == deck.id, DeckVersion.id == version_id))
