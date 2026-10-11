"""Lookouts: what in a deck can turn against its owner, read from the cards' Oracle text (bead vault-85p.1).

A strong player reads a deck for the cards that hurt their own side: a lord that hands its bonus to the opponents' creatures of
the same type, a blanket shroud that stops your own Equipment and Auras, a mandatory "destroy target" trigger that eats your own
artifacts once the opponents have none left. Assistants miss these unless something tells them, so the Vault reads the deck and
returns them inside the deck answers (deck stats, the council brief), where any host sees them after a single call.

Like ``possible_loops``, this is the Vault's own reading of the text, with anchored patterns, and it under-reports: a clause a
pattern does not understand is left out rather than guessed. Every lookout names the cards, says what happens, cites the rules by
number (quote them with get_rule: never from memory) and says what to do. Pure functions over ``CardText``; no database, no network.

``plan`` reads the same patterns on the deck as it would be after a plan (cuts and adds), so a card the person is about to add
is flagged at the point of the plan, naming the deck's cards it would hurt (bead vault-064.11). Every lookout also says which
cards do it (``causes``) and which of the deck's own cards it hurts (``hurts``).

Families (first slice, Slivers first: PRODUCT.local.md section 5, CASES K4 and TR2):

- ``own_targets_blocked``: a card gives your creatures of a type shroud (702.18a), and the deck has Equipment (702.6a: equip
  targets a creature you control), creature Auras (303.4a: an Aura spell targets) or spells and abilities that target a creature you
  control. Hexproof does not count: it stops only opponents.
- ``forced_self_destruction``: a card gives every creature of a type a mandatory "When this ... enters, destroy target ..." trigger.
  A triggered ability's target is chosen when it goes on the stack and is removed only when no legal target exists (603.3d, 601.2c),
  so once the opponents have no legal target left, your own become the only ones. Worse with token makers of that type.
- ``shared_with_opponents``: "All Slivers have ..." and "All Sliver creatures get ..." apply to every Sliver on the battlefield,
  the opponents' included; and some grants restrict your own creatures (shadow, 702.28b: they can block only creatures with shadow).
"""

from __future__ import annotations

import re
from typing import Iterable

from .possible_loops import CardText

SOURCE = "the Vault's reading of card text"
NOTE = ("Read from the cards' Oracle text by the Vault, not a proof: a pattern that does not match says nothing. Quote each rule "
        "with get_rule and read each card with get_card_oracle before relying on a lookout.")

_GROUP = r"(?:All|Each)\s+(?P<type>[A-Z][a-z]+?)s?(?:\s+creatures)?"  # "All Slivers", "All Sliver creatures"
_GRANT_SHROUD = re.compile(_GROUP + r"\s+have\s+shroud\b")
_GRANT_DESTROY = re.compile(_GROUP + r"\s+have\s+\"When\s+this\s+(?:permanent|creature)\s+enters(?:\s+the\s+battlefield)?,\s+"
                            r"destroy\s+target\s+(?P<what>[a-z ,]+?)\.\"")
_GRANT_ANY = re.compile(_GROUP + r"\s+(?:have|get)\b")
_GRANT_SHADOW = re.compile(_GROUP + r"\s+have\s+shadow\b")
_TARGETS_OWN = re.compile(r"\btarget\s+(?:[A-Z][a-z]+\s+)?creature\s+you\s+control\b|\btarget\s+[A-Z][a-z]+\s+you\s+control\b")
_EQUIP = re.compile(r"^Equip\b|\nEquip\b|\bEquip\s*\{", re.M)
_ENCHANT_CREATURE = re.compile(r"^Enchant\s+creature\b", re.M)
_TOKEN_OF = r"create[s]?\s+(?:a|an|one|two|three|X|\w+)\s+(?:[0-9X]+/[0-9X]+\s+)?(?:\w+\s+)*?{type}\s+creature\s+tokens?"


def _in_group(card: CardText, kind: str) -> bool:
    return kind.lower() in card.type_line.lower()


def _what_list(what: str) -> list[str]:
    return [w.strip() for w in re.split(r",|\bor\b", what) if w.strip()]


def own_targets_blocked(cards: list[CardText]) -> list[dict]:
    out = []
    for grantor in cards:
        m = _GRANT_SHROUD.search(grantor.text)
        if not m:
            continue
        kind = m.group("type")
        equipment = [c.name for c in cards if "Equipment" in c.type_line and _EQUIP.search(c.text)]
        auras = [c.name for c in cards if "Aura" in c.type_line and _ENCHANT_CREATURE.search(c.text)]
        targeted = [c.name for c in cards if c.name != grantor.name and _TARGETS_OWN.search(c.text)]
        hit = sorted(set(equipment + auras + targeted))
        if not hit:
            continue
        rules = ["702.18a"] + (["702.6a"] if equipment else []) + (["303.4a"] if auras else [])
        out.append({
            "kind": "own_targets_blocked", "severity": "high",
            "title": f"{grantor.name} stops your own cards from targeting your {kind}s",
            "cards": [grantor.name] + hit, "causes": [grantor.name], "hurts": hit,
            "what": (f"{grantor.name} gives every {kind} shroud, yours included, so while it is on the battlefield your {kind}s can't "
                     f"be the target of your own spells or abilities: " + ", ".join(hit) + " can't be used on them."),
            "rules": rules,
            "advice": (f"Cut {grantor.name}, or play it only after your Equipment and Auras are attached; a card that gives hexproof "
                       "instead protects them from opponents only."),
        })
    return out


def forced_self_destruction(cards: list[CardText]) -> list[dict]:
    out = []
    for grantor in cards:
        m = _GRANT_DESTROY.search(grantor.text)
        if not m:
            continue
        kind, what = m.group("type"), _what_list(m.group("what"))
        victims = sorted(c.name for c in cards if any(w.capitalize() in c.type_line or w in c.type_line.lower() for w in what)
                         and not (_in_group(c, kind) and "creature" not in what))
        makers = sorted(c.name for c in cards if re.search(_TOKEN_OF.format(type=re.escape(kind)), c.text, re.I))
        if not victims:
            continue
        rate = (f" Token makers of that type make it worse: {', '.join(makers)} each bring more {kind}s, so more triggers."
                if makers else "")
        out.append({
            "kind": "forced_self_destruction", "severity": "high",
            "title": f"{grantor.name} will destroy your own {' and '.join(what)}s",
            "cards": [grantor.name] + victims + makers, "causes": [grantor.name], "hurts": victims,
            "what": (f"{grantor.name} gives every {kind} 'When this enters, destroy target {m.group('what')}'. The trigger is not "
                     f"optional: when it goes on the stack a legal target must be chosen if one exists, so once opponents have none "
                     f"left, it must hit yours: " + ", ".join(victims) + "." + rate),
            "rules": ["603.3d", "601.2c"],
            "advice": (f"Cut {grantor.name} unless the deck wants that removal, or hold it until your own key permanents are safe; "
                       "count the opponents' artifacts and enchantments before casting more " + kind + "s."),
        })
    return out


def shared_with_opponents(cards: list[CardText]) -> list[dict]:
    lords: dict[str, list[str]] = {}
    for c in cards:
        m = _GRANT_ANY.search(c.text)
        if m and _in_group(c, m.group("type")):  # a creature of the type that grants to the whole type: the Sliver pattern
            lords.setdefault(m.group("type"), []).append(c.name)
    out = []
    for kind, names in lords.items():
        if len(names) < 2:
            continue
        harmful = sorted(c.name for c in cards if (m := _GRANT_SHADOW.search(c.text)) and m.group("type") == kind)
        what = (f"{len(names)} cards grant their ability to every {kind} on the battlefield, the opponents' included ("
                + ", ".join(sorted(names)) + "): an opponent's " + kind + " gets the same bonuses, and some of these help them more"
                " than you.")
        if harmful:
            what += (" " + ", ".join(harmful) + " gives your " + kind + "s shadow, so they can block only creatures with shadow.")
        out.append({
            "kind": "shared_with_opponents", "severity": "medium" if not harmful else "high",
            "title": f"Your {kind} bonuses also work for opponents' {kind}s",
            "cards": sorted(names), "causes": sorted(names), "hurts": [],
            "what": what,
            "rules": ["702.28b"] if harmful else [],
            "advice": (f"Check which grants help you more than an opponent playing {kind}s; prefer cards worded 'you control'."
                       + (" Cut " + ", ".join(harmful) + " unless the deck never needs to block." if harmful else "")),
        })
    return out


def read(cards: Iterable[CardText]) -> dict:
    """Every lookout the patterns find in a deck's cards, worst first, with how it was found."""
    cards = list(cards)
    found = own_targets_blocked(cards) + forced_self_destruction(cards) + shared_with_opponents(cards)
    order = {"high": 0, "medium": 1, "low": 2}
    found.sort(key=lambda x: (order[x["severity"]], x["kind"]))
    return {"lookouts": found, "source": SOURCE, "note": NOTE,
            "covers": ["own_targets_blocked", "forced_self_destruction", "shared_with_opponents"]}


# -- at the point of the plan (bead vault-064.11) --------------------------------------------------------------------------------

def _key(x: dict) -> tuple[str, str]:
    return x["kind"], x["title"]


def _about(x: dict, card: str) -> dict:
    """One lookout as it concerns one card the plan brings in: whether the card does it (``cause``), is hurt by it (``hurt``) or
    only makes it worse (``worsens``: a token maker of the type), the deck's other cards it would hurt, the rules and the advice."""
    role = "cause" if card in x["causes"] else "hurt" if card in x["hurts"] else "worsens"
    return {"kind": x["kind"], "severity": x["severity"], "title": x["title"], "role": role, "causes": x["causes"],
            "hurts": [c for c in x["hurts"] if c != card], "rules": x["rules"], "what": x["what"], "advice": x["advice"]}


def sentence(card: str, found: list[dict]) -> str:
    """One plain sentence for a card the plan adds: what it does to this deck, the rules by number and what to do. Built from the
    lookouts' own words (``about`` entries), so the wording of a lookout lives in one place."""
    parts = []
    for e in found:
        lead = {"cause": f"Adding {card} would turn against this deck.",
                "hurt": f"In the deck after this change, {card} would be hurt by {', '.join(e['causes'])}.",
                "worsens": f"Adding {card} would make a problem this deck already has worse."}[e["role"]]
        rules = f" (rules {', '.join(e['rules'])})" if e["rules"] else ""
        parts.append(f"{lead} {e['what']}{rules} {e['advice']}")
    return " ".join(parts)


def plan(before: Iterable[CardText], after: Iterable[CardText], *, was: dict | None = None) -> dict:
    """What a plan (cuts and adds) does to a deck's lookouts, read on the deck as it would be AFTER the plan, not on the deck as it
    is plus the adds: a cut can take away the card a lookout hurts, or the card that does it.

    - ``lookouts``: every lookout of the deck after the plan, worst first. ``new`` is true when the plan creates it or widens it
      (a card it hurts joins); ``new_cards`` are the cards of it that the plan brings into the deck.
    - ``cleared``: lookouts the deck has now and no longer has after the plan.
    - ``by_card``: for each card the plan brings in, the new lookouts it is part of (see ``about``), and ``warnings`` one plain
      sentence for each of those cards.

    ``was`` is the ``read`` of ``before`` when the caller has it already (find_upgrades asks about many cards on one deck)."""
    before, after = list(before), list(after)
    names = {c.name for c in before}
    was = was if was is not None else read(before)
    now = read(after)
    old = {_key(x): set(x["cards"]) for x in was["lookouts"]}
    lookouts, by_card = [], {}
    for x in now["lookouts"]:
        new = _key(x) not in old or bool(set(x["cards"]) - old[_key(x)])
        brought = sorted(c for c in x["cards"] if c not in names) if new else []
        lookouts.append({**x, "new": new, "new_cards": brought})
        for c in brought:
            by_card.setdefault(c, []).append(_about(x, c))
    kept = {_key(x) for x in now["lookouts"]}
    cleared = [{"kind": x["kind"], "title": x["title"], "cards": x["cards"]} for x in was["lookouts"] if _key(x) not in kept]
    return {"lookouts": lookouts, "cleared": cleared, "by_card": by_card,
            "warnings": {c: sentence(c, found) for c, found in by_card.items()},
            "source": SOURCE, "note": NOTE, "covers": now["covers"]}
