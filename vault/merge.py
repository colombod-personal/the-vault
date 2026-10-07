"""Re-import as a three-way update (issue #194, docs/owned-cards-updates.md "The rules of a re-import").

A person's collection can change in two places: in their app (Dragon Shield, Moxfield: they sell a card, buy a pack,
export again) and in the Vault (an assistant edit, ``vault.owned_changes``). Replacing the collection with the new file
would wipe the second kind. Instead an import compares three things, card by card (a card is a printing in a finish:
``delta.BY_PRINTING``):

* **base**: the last imported file as it was imported, stored per person in ``collection_baselines``;
* **theirs**: the new file;
* **ours**: the collection now, with the Vault edits.

Each card has a *state*: its copies grouped by condition, language, folder, price and date paid, with their quantities.
Two states are equal or they are not; that is all "changed" means here. The rules (:func:`make_plan`), per card:

* app unchanged, Vault untouched: the file's rows (nothing changes);
* app unchanged, Vault edited: keep the Vault's edit;
* app changed, Vault untouched: take the app's change;
* app changed, Vault edited to the same thing: take the app's rows (both agree, no conflict);
* app changed, Vault edited to something else: a conflict. The preview lists it, and the default keeps the Vault's edit.

A card the app dropped that was edited in the Vault is a conflict of kind ``removed_in_app``. Whatever the answers, the
new file becomes the new base. Without a base (a collection imported before this existed) base is taken to be the
collection now: nothing can be told apart, so the file replaces the collection, as it always did, and the preview says so.

This module is pure (no database): the importer loads and stores. It is deliberately independent of how rows are
stored so that its rules can be tested directly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from mtg_toolkits import delta
from mtg_toolkits.models import CollectionEntry

MODES = ("first", "no_baseline", "merge", "replace")
LIST_LIMIT = 100  # cards listed per group in an answer; the counts are always complete
STORED_LIMIT = 200  # cards listed per group in the import's stored record
DEFAULT_ANSWER = "vault"  # owner decision 2026-10-06: conflicts keep the Vault's edit unless the person says otherwise


def key_string(entry: CollectionEntry) -> str:
    return "\x1f".join(delta.key_of(entry))


def snapshot(entries: list[CollectionEntry], keys: list[str] | None = None) -> dict[str, dict]:
    """The cards of ``entries`` by key: how many copies, and the state (``r``) of each card's rows."""
    out: dict[str, dict] = {}
    for i, e in enumerate(entries):
        ks = keys[i] if keys is not None else key_string(e)
        s = out.get(ks)
        if s is None:
            s = out[ks] = {"k": ks, "n": e.name, "s": e.set_code, "c": e.collector_number, "f": e.finish.value, "q": 0, "_r": {}}
        s["q"] += e.quantity
        detail = (e.condition.value, e.language or "", e.folder or "", e.purchase_price,
                  e.purchase_date.isoformat() if e.purchase_date else None)
        group = s["_r"].setdefault(detail, [0, 0])
        group[0] += e.quantity
        group[1] += e.trade_quantity
    for s in out.values():
        rows = s.pop("_r")
        s["r"] = sorted(([*d, q, t] for d, (q, t) in rows.items() if q or t), key=json.dumps)
    return out


def stored(snap: dict[str, dict]) -> list[dict]:
    """A snapshot as it is stored (JSON)."""
    return list(snap.values())


def loaded(rows: list[dict] | None) -> dict[str, dict] | None:
    return None if rows is None else {s["k"]: s for s in rows}


@dataclass
class Plan:
    mode: str
    take: set[str] = field(default_factory=set)  # cards whose rows come from the file
    keep: set[str] = field(default_factory=set)  # cards whose rows stay as they are (Vault edits)
    kept_edits: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    app_changes: dict = field(default_factory=dict)
    same_change: int = 0  # changed in both, to the same thing: no question to ask
    discards: int = 0  # Vault edits that replacing everything with the file would lose
    new_copies: dict[str, int] = field(default_factory=dict)  # per card, copies in the result


def conflict_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _card(*snaps: dict | None) -> dict:
    s = next(s for s in snaps if s is not None)
    return {"card": s["n"], "set": s["s"], "number": s["c"], "finish": s["f"]}


def _copies(s: dict | None) -> int:
    return s["q"] if s else 0


def _state(s: dict | None) -> list:
    return s["r"] if s else []


def _same(x: dict | None, y: dict | None) -> bool:
    """Two cards are the same when their states are; a card rebuilt from a record (state ``None``, see migration 0110)
    is known by its copies only, so it is compared by copies."""
    if any(s is not None and s["r"] is None for s in (x, y)):
        return _copies(x) == _copies(y)
    return _state(x) == _state(y)


def make_plan(base: dict | None, theirs: dict[str, dict], ours: dict[str, dict], *, entries_exist: bool,
              replace_everything: bool = False, default: str = DEFAULT_ANSWER, use_app: frozenset[str] = frozenset()) -> Plan:
    """Decide, card by card, whose rows the collection ends up with (see the module's rule table)."""
    if default not in ("vault", "app"):
        raise ValueError("conflicts: vault or app")
    mode = ("replace" if replace_everything else "first" if not entries_exist else "no_baseline" if base is None else "merge")
    plan = Plan(mode=mode)
    app = {"added": 0, "removed": 0, "increased": 0, "decreased": 0, "changed": 0, "copies_in": 0, "copies_out": 0}
    effective_base = ours if base is None else base  # nothing recorded: treat the collection as unchanged since
    for ks in theirs.keys() | ours.keys() | effective_base.keys():
        b, t, o = effective_base.get(ks), theirs.get(ks), ours.get(ks)
        app_changed, vault_edited = not _same(t, b), not _same(o, b)
        if app_changed:  # what the app itself did since the last import
            old, new = _copies(b), _copies(t)
            if new != old:
                app["added" if old <= 0 else "removed" if new <= 0 else "increased" if new > old else "decreased"] += 1
                app["copies_in" if new > old else "copies_out"] += abs(new - old)
            else:
                app["changed"] += 1
        if vault_edited and not _same(o, t):
            plan.discards += 1  # replacing everything would lose this edit
        take = True
        if mode == "replace":
            take = True
        elif vault_edited and not app_changed:
            take = False
            plan.kept_edits.append({**_card(o, b), "id": conflict_id(ks), "last_import_copies": _copies(b),
                                    "vault_copies": _copies(o)})
        elif vault_edited and app_changed and _same(o, t):
            plan.same_change += 1
        elif vault_edited and app_changed:
            cid = conflict_id(ks)
            keeps = "app" if cid in use_app else default
            kind = ("removed_in_app" if not _copies(t) else "removed_in_vault" if not _copies(o)
                    else "added_in_both" if not _copies(b) else "changed_in_both")
            plan.conflicts.append({**_card(o, t, b), "id": cid, "kind": kind, "last_import_copies": _copies(b),
                                   "app_copies": _copies(t), "vault_copies": _copies(o), "keeps": keeps,
                                   "default": DEFAULT_ANSWER})
            take = keeps == "app"
        (plan.take if take else plan.keep).add(ks)
        plan.new_copies[ks] = _copies(t) if take else _copies(o)
    plan.app_changes = app
    plan.kept_edits.sort(key=lambda c: (c["card"].lower(), str(c["set"]), str(c["number"])))
    plan.conflicts.sort(key=lambda c: (c["card"].lower(), str(c["set"]), str(c["number"])))
    return plan


def summary(old: dict[str, int], new: dict[str, int]) -> dict[str, int]:
    """What the collection's cards did, shaped like ``delta.CollectionDiff.summary()`` (the import history's summary)."""
    out = dict.fromkeys(("added", "removed", "increased", "decreased", "unchanged", "copies_in", "copies_out"), 0)
    for ks in old.keys() | new.keys():
        a, b = old.get(ks, 0), new.get(ks, 0)
        if a == b:
            out["unchanged"] += 1
        elif a <= 0:
            out["added"] += 1
        elif b <= 0:
            out["removed"] += 1
        else:
            out["increased" if b > a else "decreased"] += 1
        out["copies_in" if b > a else "copies_out"] += abs(b - a)
    return out


QUESTION = {
    "removed_in_app": "Your app no longer has this card, but it was edited here since the last import.",
    "removed_in_vault": "It was removed here since the last import, and your app changed its copies.",
    "added_in_both": "Your app and the Vault each added this card since the last import, with different copies.",
    "changed_in_both": "Your app and the Vault each changed this card since the last import.",
}


def describe(plan: Plan, baseline: dict | None, *, limit: int = LIST_LIMIT) -> dict:
    """The answer shown to the person (preview) and kept on the import (history): what the app changed and is applied,
    which Vault edits are kept, and the conflicts, each with the answer that will be used unless they say otherwise."""
    how = {
        "first": "This is the first import, so the file becomes the collection.",
        "no_baseline": "The Vault has no record of an earlier file for this collection, so this import replaces it with "
                       "the file, as imports did before. From now on re-imports keep what you change here.",
        "merge": "Only what changed in your app since the last import is applied; edits made here are kept.",
        "replace": "You chose to replace everything: the file replaces the collection and edits made here are discarded.",
    }[plan.mode]
    conflicts = [{**c, "question": QUESTION[c["kind"]]} for c in plan.conflicts[:limit]]
    return {
        "mode": plan.mode, "how": how, "baseline": baseline,
        "from_your_app": plan.app_changes,
        "kept_vault_edits": {"count": len(plan.kept_edits), "cards": plan.kept_edits[:limit]},
        "conflicts": {"count": len(plan.conflicts), "default": DEFAULT_ANSWER, "cards": conflicts,
                      "answer": "Each conflict keeps the Vault's edit unless you answer 'app' for it (or for all of "
                                "them): ask the person, one card at a time."} if plan.conflicts
        else {"count": 0, "cards": []},
        "same_change_in_both": plan.same_change,
        "replace_everything_discards_vault_edits": plan.discards,
    }
