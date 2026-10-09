"""Tags on cards (#127, docs/collections.md section 2): a person's own labels, keyed by the card's oracle id.

A tag is on the card, not on a copy or a printing, so it survives every import and stays (shown as "not owned") when the last
copy leaves. ``source`` says who wrote it: the person, an assistant (with the app's name, never shown as the person's own) or
the system. Limits keep every answer bounded: 50 tags on a card, 500 different tags per person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import tokens
from .collection_view import CollectionView
from .models import Card, Entry, TagAssignment, User

TAG_RE = re.compile(r"^[a-z0-9:-]{1,40}$")  # the same pattern as the database check
MAX_PER_CARD = 50
MAX_TAGS = 500
CONFIRM_ABOVE = 25  # cards: tagging or untagging more than this at once is shown first and applied only with confirm


class TagError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def normalize(tag: str) -> str:
    """The tag as stored: trimmed, lower case. Letters, digits, ``-`` and ``:`` (``deck:sliver``, ``trade:sell``), 1 to 40."""
    text = (tag or "").strip().lower()
    if not TAG_RE.match(text):
        raise TagError("A tag is 1 to 40 characters: lower case letters, digits, '-' and ':' (for example trade or deck:sliver)")
    return text


@dataclass(frozen=True)
class Writer:
    source: str  # person | assistant
    detail: str | None  # which app, for an assistant


def writer_of(db: Session, bearer: str | None) -> Writer:
    """Who is writing: an OAuth app or a personal access token is an assistant (named); the web and native apps are the person."""
    label = tokens.app_label(db, bearer)
    if bearer and label not in ("the Vault web app", "the Vault app"):
        return Writer("assistant", label[:200])
    return Writer("person", None)


def resolve(db: Session, user: User, card_ids: list[str]) -> list[tuple[str, str, str]]:
    """``(card id, oracle id, name)`` for ids from search_cards (a printing group of the person's collection). An unknown id is
    404; a copy the importer could not match to a card has no oracle id and can't take a tag (422): it is shown as a status."""
    view = CollectionView(db, user)
    wanted = list(dict.fromkeys(card_ids))
    groups = []
    for card_id in wanted:
        g = view.by_id.get(card_id)
        if g is None:
            raise TagError(f"Card {card_id!r} is not in your collection (ids come from search_cards)", 404)
        groups.append(g)
    oracle = {sid: oid for sid, oid in db.execute(select(Card.scryfall_id, Card.oracle_id).where(
        Card.scryfall_id.in_({g.scryfall_id for g in groups if g.scryfall_id})))}
    out = []
    for card_id, g in zip(wanted, groups):
        if not g.scryfall_id or not oracle.get(g.scryfall_id):
            raise TagError(f"{g.name} is not matched to a card yet, so it can't take a tag")
        out.append((card_id, oracle[g.scryfall_id], g.name))
    return out


def oracle_ids_for(db: Session, user: User, tag: str) -> set[str]:
    return set(db.scalars(select(TagAssignment.oracle_id).where(TagAssignment.user_id == user.id, TagAssignment.tag == tag)))


def tags_of(db: Session, user: User, oracle_ids: set[str]) -> dict[str, list[str]]:
    """The person's tags on these cards, in name order."""
    if not oracle_ids:
        return {}
    out: dict[str, list[str]] = {}
    for oracle_id, tag in db.execute(select(TagAssignment.oracle_id, TagAssignment.tag).where(
            TagAssignment.user_id == user.id, TagAssignment.oracle_id.in_(oracle_ids)).order_by(TagAssignment.tag)):
        out.setdefault(oracle_id, []).append(tag)
    return out


def stamp(db: Session, user: User) -> str:
    """Changes whenever the person's tags do (a tag added, removed or renamed): part of the ETag of answers that show tags."""
    count, newest, names = db.execute(select(func.count(), func.coalesce(func.max(TagAssignment.id), 0),
                                             func.coalesce(func.sum(func.hashtext(TagAssignment.tag)), 0))
                                      .where(TagAssignment.user_id == user.id)).one()
    return f"t{count}.{newest}.{names}"


def assign(db: Session, user: User, tag: str, cards: list[tuple[str, str, str]], writer: Writer) -> tuple[int, int, int]:
    """Put ``tag`` on these cards; returns ``(added, already there, accepted)``. Enforces the limits where they are written.
    When the person tags a card whose tag an assistant wrote, they accept it: the assignment becomes theirs (docs/collections.md)."""
    have = oracle_ids_for(db, user, tag)
    cards = list({c[1]: c for c in cards}.values())  # two printings of one card are one card
    new = [c for c in cards if c[1] not in have]
    if new and not have:
        distinct = db.scalar(select(func.count(func.distinct(TagAssignment.tag))).where(TagAssignment.user_id == user.id))
        if distinct >= MAX_TAGS:
            raise TagError(f"You have {MAX_TAGS} different tags already; delete one first", 409)
    if new:
        counts = dict(db.execute(select(TagAssignment.oracle_id, func.count()).where(
            TagAssignment.user_id == user.id, TagAssignment.oracle_id.in_([c[1] for c in new])).group_by(TagAssignment.oracle_id)).all())
        full = [name for _, oracle_id, name in new if counts.get(oracle_id, 0) >= MAX_PER_CARD]
        if full:
            raise TagError(f"{full[0]} has {MAX_PER_CARD} tags already (the most a card can have); remove one first", 409)
    for _, oracle_id, _ in new:
        db.add(TagAssignment(user_id=user.id, oracle_id=oracle_id, tag=tag, source=writer.source, source_detail=writer.detail))
    accepted = 0
    if writer.source == "person" and len(new) < len(cards):
        accepted = db.execute(update(TagAssignment).where(
            TagAssignment.user_id == user.id, TagAssignment.tag == tag, TagAssignment.source == "assistant",
            TagAssignment.oracle_id.in_([c[1] for c in cards if c[1] in have])).values(source="person", source_detail=None)).rowcount or 0
    db.flush()
    return len(new), len(cards) - len(new), accepted


def remove(db: Session, user: User, tag: str, oracle_ids: list[str]) -> int:
    """Take ``tag`` off these cards; returns how many had it."""
    rows = list(db.scalars(select(TagAssignment).where(TagAssignment.user_id == user.id, TagAssignment.tag == tag,
                                                       TagAssignment.oracle_id.in_(oracle_ids))))
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)


def rename(db: Session, user: User, old: str, new: str) -> int:
    """Rename a tag on every card. A card that already has the new tag keeps that one and loses the old (the two merge)."""
    rows = list(db.scalars(select(TagAssignment).where(TagAssignment.user_id == user.id, TagAssignment.tag == old)))
    if not rows:
        raise TagError("Tag not found", 404)
    has_new = oracle_ids_for(db, user, new)
    for row in rows:
        if row.oracle_id in has_new:
            db.delete(row)
        else:
            row.tag = new
    db.flush()
    return len(rows)


def owned_count(db: Session, user: User, oracle_ids: set[str]) -> int:
    """How many of these cards the person has a copy of now (the others are tagged but not owned)."""
    if not oracle_ids:
        return 0
    return db.scalar(select(func.count(func.distinct(Card.oracle_id))).select_from(Card).join(Entry, Entry.scryfall_id == Card.scryfall_id)
                     .where(Entry.user_id == user.id, Entry.quantity > 0, Card.oracle_id.in_(oracle_ids))) or 0
