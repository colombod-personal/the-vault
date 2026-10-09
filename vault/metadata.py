"""``vault_metadata``: free-form JSON that assistants, connectors and the person keep on cards and buckets (#127, #119,
docs/collections.md section 7). The Vault never reads it for its own logic.

One document per thing, ``{"version": 1, "user": {...}, "ai.<app>": {...}, "system": {...}, "written": {...}}``:

* **Namespaces isolate writers.** A caller writes only its own: the person (the web and native apps) writes ``user``; an
  assistant writes ``ai.<its app>`` (named after the app that is signed in, so one app cannot overwrite another's data or the
  person's). ``system`` is the Vault's, written by nobody through the API. Everyone reads every namespace: it is the person's data.
* **Who and when** is kept in ``written`` (namespace -> ``{at, by}``), set by the Vault, never by the caller.
* **A version** says which shape the document has. A document of an older version is upgraded on read by the upgraders below
  (one per version step); a newer one than this code knows is refused, never guessed at.
* **Limits**: the whole document is at most 8 KB (the database checks it too) and 6 levels deep. A document over a limit is
  refused (413), never truncated.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone

CURRENT = 1
MAX_BYTES = 8192  # the same as the database check on every vault_metadata column
MAX_DEPTH = 6
KEPT = {"version", "written"}  # keys of the document that are not a namespace
UPGRADERS: dict[int, Callable[[dict], dict]] = {}  # version n -> a function that returns the same document at version n + 1


class MetadataError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def namespace_for(source: str, detail: str | None) -> str:
    """The namespace a writer owns: ``user`` for the person, ``ai.<app>`` for an assistant (the app's host, or the personal
    token's name, in lower case letters, digits, dots and dashes)."""
    if source != "assistant":
        return "user"
    slug = re.sub(r"[^a-z0-9.]+", "-", (detail or "").lower()).strip("-.")[:48]
    return f"ai.{slug or 'app'}"


def upgraded(doc: dict) -> dict:
    """The document at the current version. Older ones go through their upgraders; one newer than this code is refused."""
    version = doc.get("version")
    if not isinstance(version, int) or version < 1:
        raise MetadataError("The stored metadata has no valid version", 500)
    if version > CURRENT:
        raise MetadataError(f"This metadata is version {version}; this Vault understands up to {CURRENT}", 409)
    while version < CURRENT:
        step = UPGRADERS.get(version)
        if step is None:
            raise MetadataError(f"No upgrade from metadata version {version}", 500)
        doc = {**step(doc), "version": version + 1}
        version += 1
    return doc


def _depth(value, level: int = 1) -> int:
    if isinstance(value, dict):
        return max([level] + [_depth(v, level + 1) for v in value.values()])
    if isinstance(value, list):
        return max([level] + [_depth(v, level + 1) for v in value])
    return level


def size(doc: dict) -> int:
    return len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))  # the same separators as Postgres' jsonb text


def view(doc: dict) -> dict:
    """What a reader sees: the version, every namespace, and who wrote each and when."""
    doc = upgraded(doc)
    return {"version": doc["version"], "namespaces": {k: v for k, v in doc.items() if k not in KEPT},
            "written": doc.get("written", {})}


def write(doc: dict, namespace: str, data: dict, *, by: str, now: datetime | None = None) -> dict:
    """The document with ``namespace`` replaced by ``data`` (an empty object removes it). Only the writer's own namespace changes;
    the others, and ``system``, are carried over untouched. Raises :class:`MetadataError` over a limit."""
    if namespace == "system":
        raise MetadataError("The system namespace is the Vault's own; it can't be written", 403)
    if not isinstance(data, dict):
        raise MetadataError("The data is a JSON object")
    if _depth(data) > MAX_DEPTH:
        raise MetadataError(f"The data is nested more than {MAX_DEPTH} levels deep", 413)
    out = dict(upgraded(doc))
    written = dict(out.get("written", {}))
    if data:
        out[namespace] = data
        written[namespace] = {"at": (now or datetime.now(timezone.utc)).isoformat(), "by": by[:200]}
    else:
        out.pop(namespace, None)
        written.pop(namespace, None)
    if written:
        out["written"] = written
    else:
        out.pop("written", None)
    if size(out) > MAX_BYTES:
        raise MetadataError(f"The metadata of one thing is at most {MAX_BYTES} bytes in all (every writer's together)", 413)
    return out
