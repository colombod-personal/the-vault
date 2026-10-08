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
LAST_CHANGE_LISTED = 40  # changes listed in get_deck's last_change; the summary keeps the true totals (#327)
SOURCES = ("saved", "edited", "imported", "refreshed", "opened")


def card_fingerprint(text: str) -> str:
    """The same for lists with the same cards in the same sections, whatever their spelling, order or comments."""
    cards = sorted((section, name, qty) for (section, name), (_, qty) in deck_refresh._cards(text).items())
    return hashlib.sha256(repr(cards).encode()).hexdigest()[:20]


def record(db: Session, deck: Deck, source: str, now: datetime | None = None) -> DeckVersion | None:
    """Record the deck's list as a new version if its cards differ from the latest one. Returns it, or None if unchanged."""
    return record_text(db, deck.id, deck.text, source, now)


def record_text(db: Session, deck_id: int, text: str, source: str, now: datetime | None = None) -> DeckVersion | None:
    assert source in SOURCES, source
    fingerprint = card_fingerprint(text)
    latest = db.scalar(select(DeckVersion).where(DeckVersion.deck_id == deck_id).order_by(DeckVersion.id.desc()).limit(1))
    if latest is not None and latest.fingerprint == fingerprint:
        return None
    version = DeckVersion(deck_id=deck_id, text=text, fingerprint=fingerprint, source=source,
                          created_at=now or datetime.now(timezone.utc))
    db.add(version)
    db.flush()
    stale = db.scalars(select(DeckVersion.id).where(DeckVersion.deck_id == deck_id).order_by(DeckVersion.id.desc()).offset(KEEP)).all()
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
    out = {"at": _iso(rows[0].created_at), "version": rows[0].id, "source": rows[0].source,
           "changes": changes[:LAST_CHANGE_LISTED], "summary": deck_refresh.summary(changes),
           "note": "Between the previous saved version of this deck and the current list."}
    if len(changes) > LAST_CHANGE_LISTED:  # a big edit must not make the deck answer huge: the web app's History lists them all
        out["more_changes"] = len(changes) - LAST_CHANGE_LISTED
        out["note"] += f" The first {LAST_CHANGE_LISTED} of {len(changes)} changes are listed; summary has the totals."
    return out


def seen(db: Session, deck: Deck, text: str | None = None, now: datetime | None = None) -> dict:
    """The person opened the deck page. ``text`` is the list the page showed (for a deck from a link: the source's current
    list): if its cards differ from the latest version it is recorded (``opened``). Answers what changed since they last
    looked (the latest list against the version they had seen; ``None`` the first time or when that one was dropped), then
    marks the latest as seen, so the next open reports only what is new."""
    recorded = None
    if text is not None:
        recorded = record_text(db, deck.id, text, "opened", now)
    latest = db.scalar(select(DeckVersion).where(DeckVersion.deck_id == deck.id).order_by(DeckVersion.id.desc()).limit(1))
    since = None
    before = db.get(DeckVersion, deck.viewed_version_id) if deck.viewed_version_id else None
    if before is not None and latest is not None and before.id != latest.id:
        changes = deck_refresh.diff(before.text, latest.text)
        if changes:
            since = {"since": _iso(before.created_at), "version": latest.id, "source": latest.source, "changes": changes,
                     "summary": deck_refresh.summary(changes)}
    if latest is not None and deck.viewed_version_id != latest.id:
        deck.viewed_version_id = latest.id
    return {"recorded": recorded is not None, "since_last_looked": since}


def get(db: Session, deck: Deck, version_id: int) -> DeckVersion | None:
    return db.scalar(select(DeckVersion).where(DeckVersion.deck_id == deck.id, DeckVersion.id == version_id))
