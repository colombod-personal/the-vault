"""Small, checked edits to the cards a person owns, made through their assistant (issue #83, docs/owned-cards-updates.md).

Flow: ``preview`` resolves each line (the card, its printing, copies before and after, the price change) and changes
nothing; when every line is resolved it returns a ``confirmation`` signed over (person, the exact resolved change set,
the collection version it was computed against, expiry). ``apply`` recomputes the preview from the same lines and applies
it only if the signature matches and the collection has not changed. Each applied change set is recorded like an import
(``imports.kind = "assistant"``, with its exact changes and the app that made it), so history shows it, ``undo`` can revert
it, and a later re-import can tell the person's own edits from their app's (#194).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from datetime import date

from mtg_toolkits import delta
from mtg_toolkits.models import CollectionEntry, Finish
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import catalog_queries as q
from .models import Card, Entry, Import, OraclePrice, PriceSnapshot, User

MAX_LINES = 50
MAX_REMOVED_COPIES = 25
MAX_REMOVED_SHARE = 0.10
REMOVAL_FLOOR = 5  # removing this many copies is never refused by the share cap (a small collection can still sell one)
TOKEN_SECONDS = 15 * 60
MAX_CANDIDATES = 20


class ChangeError(ValueError):
    """The change set cannot be applied as given (stale, tampered, over a cap)."""


@dataclass
class Resolved:
    """One line after resolution: ``status`` is ready, choose_printing or refused."""
    action: str
    quantity: int
    name: str
    status: str
    reason: str | None = None
    card: str | None = None
    oracle_id: str | None = None
    set: str | None = None
    number: str | None = None
    finish: str = "nonfoil"
    scryfall_id: str | None = None
    before: int = 0
    after: int = 0
    unit_price: float | None = None
    image: dict | None = None  # the chosen printing's Scryfall image, for the preview view
    candidates: list[dict] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def key(self) -> tuple:
        return delta.key_of(CollectionEntry(name=self.card or self.name, quantity=1, set_code=self.set,
                                            collector_number=self.number, finish=Finish(self.finish)), delta.BY_PRINTING)

    def signed(self) -> dict:
        return {"a": self.action, "q": self.quantity, "c": self.card, "s": self.set, "n": self.number, "f": self.finish,
                "id": self.scryfall_id, "b": self.before, "t": self.after}

    def public(self) -> dict:
        out = {"action": self.action, "quantity": self.quantity, "name": self.name, "status": self.status}
        if self.reason:
            out["reason"] = self.reason
        if self.card:
            out.update({"card": self.card, "printing": {"set": self.set, "number": self.number, "finish": self.finish}
                        if self.set else "not specified", "copies_before": self.before, "copies_after": self.after,
                        "unit_price_usd": self.unit_price})
            if self.image:
                out["image"] = self.image
        if self.candidates:
            out["choose_from"] = self.candidates
        if self.suggestions:
            out["did_you_mean"] = self.suggestions
        return out


def _owned(db: Session, user: User, card_name: str) -> dict[tuple, list[Entry]]:
    rows = db.scalars(select(Entry).where(Entry.user_id == user.id, func.lower(Entry.name) == card_name.lower(),
                                          Entry.quantity > 0)).all()
    out: dict[tuple, list[Entry]] = {}
    for r in rows:
        out.setdefault(delta.key_of(r.to_collection_entry(), delta.BY_PRINTING), []).append(r)
    return out


def _image(card: Card | None) -> dict | None:
    """A printing's Scryfall image (shown whole, artist credited), or None. Only Scryfall's image server is allowed."""
    if card is None or not (card.image_normal or "").startswith("https://cards.scryfall.io/"):
        return None
    return {"url": card.image_normal, "small": card.image_small if (card.image_small or "").startswith("https://cards.scryfall.io/")
            else None, "artist": card.artist, "scryfall_uri": card.scryfall_uri, "source": "Scryfall"}


def _card_of(db: Session, r: Entry | Card) -> Card | None:
    if isinstance(r, Card):
        return r
    if r.scryfall_id and (card := db.get(Card, r.scryfall_id)) is not None:
        return card
    if r.set_code and r.collector_number:
        return db.scalar(select(Card).where(func.lower(Card.set_code) == r.set_code.lower(),
                                            func.lower(Card.collector_number) == r.collector_number.lower()))
    return None


def _printing_label(db: Session, r: Entry | Card, quantity: int | None = None, finish: str | None = None) -> dict:
    card = _card_of(db, r)
    out = {"set": card.set_code if card else r.set_code, "set_name": (card.set_name if card else None) or r.set_name,
           "number": card.collector_number if card else r.collector_number,
           "finishes": [f for f in (card.finishes if card else []) if f in ("nonfoil", "foil", "etched")] or None}
    if finish is not None:
        out["finish"] = finish
    if quantity is not None:
        out["owned"] = quantity
    if (image := _image(card)) is not None:
        out["image"] = image
    return out


def _price(db: Session, scryfall_id: str | None, oracle_id: str | None) -> float | None:
    if scryfall_id:
        p = db.scalar(select(PriceSnapshot).where(PriceSnapshot.scryfall_id == scryfall_id).order_by(PriceSnapshot.day.desc()))
        if p and (p.usd or p.usd_foil):
            return p.usd or p.usd_foil
    if oracle_id:
        op = db.get(OraclePrice, oracle_id)
        if op and op.usd:
            return op.usd
    return None


def _resolve(db: Session, user: User, line: dict, lookup_printing) -> Resolved:
    action, qty = line["action"], int(line["quantity"])
    r = Resolved(action=action, quantity=qty, name=line["name"].strip(), status="ready", finish=line.get("finish") or "nonfoil")
    if action in ("add", "remove") and qty < 1:
        r.status, r.reason = "refused", "quantity must be at least 1 to add or remove"
        return r
    card, similar = q.find_card(db, name=r.name)
    if card is None:
        r.status, r.reason = "refused", "no card with that name"
        r.suggestions = [c.name for c in similar]
        return r
    r.card, r.oracle_id = card.name, card.oracle_id
    owned = _owned(db, user, card.name)
    if line.get("set") and line.get("number"):
        known = db.scalar(select(Card).where(func.lower(Card.set_code) == line["set"].lower(),
                                             func.lower(Card.collector_number) == line["number"].lower()))
        found = known or lookup_printing(line["set"], line["number"])
        if found is None or (found.oracle_id and found.oracle_id != card.oracle_id and found.name.split(" // ")[0].lower()
                                                                                       != card.name.split(" // ")[0].lower()):
            r.status, r.reason = "refused", f"{card.name} has no printing {line['set'].upper()} {line['number']}"
            return r
        r.set, r.number, r.scryfall_id = found.set_code.lower(), found.collector_number, found.scryfall_id
    elif line.get("printing_unknown"):
        if action != "add" and not line.get("_undo"):  # only the server's own undo sets a name-only line
            r.status, r.reason = "refused", "say which printing to remove or change"
            return r
    else:
        mine = sorted(owned.items(), key=lambda kv: -sum(e.quantity for e in kv[1]))
        if action != "add" and len(mine) == 1:  # they own one printing of it: that is the one
            e = mine[0][1][0]
            r.set, r.number, r.finish, r.scryfall_id = e.set_code, e.collector_number, e.finish, e.scryfall_id
        else:
            r.status = "choose_printing"
            r.reason = ("ask the person which printing they mean (or, for an add, whether they do not know: then "
                        "send printing_unknown)")
            seen = set()
            for key, entries in mine:
                e = entries[0]
                label = _printing_label(db, e, sum(x.quantity for x in entries), e.finish)
                r.candidates.append(label)
                seen.add((str(label["set"]).lower(), str(label["number"]).lower()))  # Scryfall's codes when known
            if action == "add":
                for c in db.scalars(select(Card).where(Card.oracle_id == card.oracle_id).order_by(Card.set_code).limit(MAX_CANDIDATES)):
                    if (c.set_code.lower(), c.collector_number.lower()) not in seen:
                        r.candidates.append(_printing_label(db, c))
            return r
    current = owned.get(r.key(), [])
    r.before = sum(e.quantity for e in current)
    r.after = r.before + qty if action == "add" else r.before - qty if action == "remove" else qty
    if r.after < 0:
        r.status, r.reason = "refused", f"they own {r.before} of that printing, not {qty}"
    r.unit_price = _price(db, r.scryfall_id, r.oracle_id)
    if r.scryfall_id:
        r.image = _image(db.get(Card, r.scryfall_id))
    return r


def owned_printings(db: Session, user: User, name: str) -> dict:
    """The printings of a card the person owns, most copies first, each with its Scryfall image (read-only)."""
    card, similar = q.find_card(db, name=name)
    if card is None:
        return {"card": None, "did_you_mean": [c.name for c in similar], "printings": []}
    mine = sorted(_owned(db, user, card.name).values(), key=lambda entries: -sum(e.quantity for e in entries))
    printings = [_printing_label(db, entries[0], sum(e.quantity for e in entries), entries[0].finish) for entries in mine]
    return {"card": card.name, "printings": printings, "copies": sum(p["owned"] for p in printings)}


def _token(secret: str, user: User, version: int, lines: list[Resolved], expires: int, undo: bool) -> str:
    body = json.dumps({"u": user.id, "v": version, "x": expires, "undo": undo, "l": [r.signed() for r in lines]},
                      sort_keys=True, separators=(",", ":"))
    sig = hmac.new(secret.encode(), b"owned-changes:" + body.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{expires}.{sig}".encode()).decode()


def preview(db: Session, user: User, lines: list[dict], secret: str, lookup_printing, *, undo: bool = False,
            now: float | None = None) -> dict:
    if not lines or len(lines) > MAX_LINES:
        raise ChangeError(f"Send 1 to {MAX_LINES} lines; larger changes go through an import")
    version = db.scalar(select(User.collection_version).where(User.id == user.id)) or 0
    resolved = [_resolve(db, user, line, lookup_printing) for line in lines]
    removed = sum(max(0, r.before - r.after) for r in resolved if r.status == "ready")
    added = sum(max(0, r.after - r.before) for r in resolved if r.status == "ready")
    total = db.scalar(select(func.coalesce(func.sum(Entry.quantity), 0)).where(Entry.user_id == user.id)) or 0
    refusal = None
    if not undo and (removed > MAX_REMOVED_COPIES
                     or (removed > REMOVAL_FLOOR and total and removed > MAX_REMOVED_SHARE * total)):
        refusal = (f"This removes {removed} copies at once; the limit is {MAX_REMOVED_COPIES} copies, or 10% of the collection "
                   f"once more than {REMOVAL_FLOOR} are removed. "
                   "Larger removals go through an import or a reset, which have their own previews.")
    value = sum(((r.after - r.before) * r.unit_price) for r in resolved if r.status == "ready" and r.unit_price)
    ready = refusal is None and all(r.status == "ready" for r in resolved)
    out = {"lines": [r.public() for r in resolved], "copies_added": added, "copies_removed": removed,
           "value_change_usd": round(value, 2), "collection_version": version, "ready": ready,
           "note": "Nothing has changed yet. Show this to the person; apply it only after they say yes. Re-importing a "
                   "file later replaces the whole collection with that file, so an edit made here is lost unless their app "
                   "has it too (a re-import that keeps these edits is planned, #194)."}
    if refusal:
        out["refused"] = refusal
    if ready:
        expires = int((now or time.time()) + TOKEN_SECONDS)
        out["confirmation"] = _token(secret, user, version, resolved, expires, undo)
        out["expires_in_seconds"] = TOKEN_SECONDS
    out["_resolved"] = resolved  # for apply; removed before the answer leaves the server
    return out


def apply(db: Session, user: User, lines: list[dict], confirmation: str, secret: str, lookup_printing, app_label: str,
          *, undo_of: Import | None = None, now: float | None = None) -> Import:
    try:
        expires_s, sig = base64.urlsafe_b64decode(confirmation.encode()).decode().split(".", 1)
        expires = int(expires_s)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ChangeError("That confirmation is not valid: preview again") from exc
    if expires < (now or time.time()):
        raise ChangeError("That confirmation has expired: preview again and ask the person")
    seen = preview(db, user, lines, secret, lookup_printing, undo=undo_of is not None, now=now)
    if not seen["ready"]:
        raise ChangeError("These changes are not ready to apply: preview again")
    expected = _token(secret, user, seen["collection_version"], seen["_resolved"], expires, undo_of is not None)
    if not hmac.compare_digest(expected.encode(), confirmation.encode()):
        raise ChangeError("These are not the changes the person saw, or the collection changed since: preview again")
    version = seen["collection_version"]
    claimed = db.execute(update(User).where(User.id == user.id, User.collection_version == version)
                         .values(collection_version=version + 1).execution_options(synchronize_session=False)).rowcount
    if not claimed:
        raise ChangeError("The collection changed meanwhile: preview again")
    total_after = (db.scalar(select(func.coalesce(func.sum(Entry.quantity), 0)).where(Entry.user_id == user.id)) or 0) \
        + seen["copies_added"] - seen["copies_removed"]
    imp = Import(user_id=user.id, filename=f"Changes by {app_label}"[:255], source="assistant", rows=len(seen["_resolved"]),
                 copies=total_after, kind="undo" if undo_of else "assistant", app=app_label[:200])
    db.add(imp)
    db.flush()
    position = (db.scalar(select(func.max(Entry.position)).where(Entry.user_id == user.id)) or 0) + 1
    lines = []
    for r in seen["_resolved"]:
        # which folders (the future buckets, docs/collections.md) the copies went into or came out of
        folders = _set_quantity(db, user, r, imp, position)
        lines.append({"card": r.card, "set": r.set, "number": r.number, "finish": r.finish, "scryfall_id": r.scryfall_id,
                        "before": r.before, "after": r.after, "folders": folders})
        position += 1
    # the summary has an import's shape (delta.CollectionDiff.summary), so history reads both the same way
    imp.summary = {"added": sum(1 for r in lines if r["before"] == 0 and r["after"] > 0),
                   "removed": sum(1 for r in lines if r["before"] > 0 and r["after"] == 0),
                   "increased": sum(1 for r in lines if 0 < r["before"] < r["after"]),
                   "decreased": sum(1 for r in lines if 0 < r["after"] < r["before"]),
                   "unchanged": 0, "copies_in": seen["copies_added"], "copies_out": seen["copies_removed"]}
    imp.changes = {"lines": lines, "value_change_usd": seen["value_change_usd"], "version_after": version + 1,
                   **({"undoes": undo_of.id} if undo_of else {})}
    if undo_of is not None:
        undo_of.changes = {**(undo_of.changes or {}), "undone_by": imp.id}
    db.flush()
    from .prices import compute_values  # today's value, as an import does
    compute_values(db, date.today(), user.id, commit=False)
    return imp


def _set_quantity(db: Session, user: User, r: Resolved, imp: Import, position: int) -> list[dict]:
    """Bring the printing to ``r.after`` copies; returns the folders changed: ``[{"folder", "copies"}]`` (+ added, - removed)."""
    current = _owned(db, user, r.card).get(r.key(), [])
    diff = r.after - sum(e.quantity for e in current)
    moved: list[dict] = []
    if diff > 0:
        target = next((e for e in current if e.condition == "near_mint" and e.language == "en"), None)
        if target is not None:
            target.quantity += diff
            moved.append({"folder": target.folder, "copies": diff})
        else:
            db.add(Entry(user_id=user.id, import_id=imp.id, position=position, name=r.card, set_code=r.set,
                         collector_number=r.number, finish=r.finish, quantity=diff, scryfall_id=r.scryfall_id,
                         match_method="id" if r.scryfall_id else None))
            moved.append({"folder": None, "copies": diff})
    elif diff < 0:
        for e in sorted(current, key=lambda e: e.quantity):
            take = min(e.quantity, -diff)
            e.quantity -= take
            diff += take
            moved.append({"folder": e.folder, "copies": -take})
            if e.quantity == 0:
                db.delete(e)
            if diff == 0:
                break
    return moved


def last_undoable(db: Session, user: User) -> Import | None:
    """The latest assistant change set, if nothing changed the collection since and it was not undone."""
    imp = db.scalar(select(Import).where(Import.user_id == user.id).order_by(Import.id.desc()))
    if imp is None or imp.kind != "assistant" or (imp.changes or {}).get("undone_by"):
        return None
    version = db.scalar(select(User.collection_version).where(User.id == user.id))
    return imp if (imp.changes or {}).get("version_after") == version else None


def undo_lines(imp: Import) -> list[dict]:
    """The lines that put every changed printing back to what it was before ``imp``."""
    out = []
    for c in (imp.changes or {}).get("lines", []):
        line = {"action": "set", "name": c["card"], "quantity": c["before"], "finish": c["finish"]}
        if c.get("set"):
            line.update({"set": c["set"], "number": c["number"]})
        else:
            line.update({"printing_unknown": True, "_undo": True})
        out.append(line)
    return out
