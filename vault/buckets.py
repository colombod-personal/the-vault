"""Buckets: where copies live (docs/collections.md, #118 and #121).

A bucket per distinct Dragon Shield folder, and "Unsorted" for copies with no folder. Names are unique per person and compare
case-insensitively, so ``Box`` and ``box`` share one bucket while ``entries.folder`` keeps each spelling for the CSV round-trip.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Bucket

DEFAULT = "Unsorted"


def bucket_name(folder: str | None) -> str:
    """The bucket name of a folder as written in the file: trimmed, and "Unsorted" when there is none."""
    return (folder or "").strip() or DEFAULT


def ensure(session: Session, user_id: int, names: dict[str, str]) -> dict[str, int]:
    """The ids of one person's buckets for ``{lower-cased name: name to create it with}``, creating the missing ones.
    A concurrent import creating the same bucket is not an error: the unique index decides and both read the one row."""
    def found() -> dict[str, int]:
        rows = session.execute(select(func.lower(Bucket.name), Bucket.id)
                               .where(Bucket.user_id == user_id, func.lower(Bucket.name).in_(list(names)))).all()
        return {key: bucket_id for key, bucket_id in rows}

    have = found()
    missing = [key for key in names if key not in have]
    if missing:
        base = session.scalar(select(func.coalesce(func.max(Bucket.position), -1)).where(Bucket.user_id == user_id)) + 1
        session.execute(insert(Bucket).values([
            {"user_id": user_id, "name": names[key], "kind": "default" if key == DEFAULT.lower() else "folder",
             "position": base + i, "vault_metadata": {"version": 1}} for i, key in enumerate(missing)]).on_conflict_do_nothing())
        have = found()
    return have


def assign(session: Session, entries: list) -> None:
    """Give each new entry the bucket of its folder: one lookup per person and distinct folder, not one per row."""
    wanted: dict[int, dict[str, str]] = {}
    for entry in entries:
        name = bucket_name(entry.folder)
        wanted.setdefault(entry.user_id, {}).setdefault(name.lower(), name)
    ids = {user_id: ensure(session, user_id, names) for user_id, names in wanted.items()}
    for entry in entries:
        entry.bucket_id = ids[entry.user_id][bucket_name(entry.folder).lower()]
