"""A Commander Bracket hint computed from the published bracket rules (#171).

**What it is.** The lowest of Wizards' five Commander Brackets a deck's contents allow, worked out by the Vault from the things
Wizards' published rules name, each listed with its inputs and the rule behind it. It is a **floor**, not a placement and not a
power score: Wizards says the brackets are about what players intend and expect, "a tool to guide pregame conversations", and a
deck can sit above its floor. The answer is labelled ``computed`` (by the Vault) and carries Wizards' pages as its sources.

**The rules used (read on 2026-10-07; the current text is on the pages in ``SOURCES``, checked every night by a conformance test)**

* Game Changers (Wizards' list, flagged by Scryfall): none in Brackets 1 and 2, up to three in Bracket 3, unlimited in 4 and 5.
  One to three set a floor of 3, more than three a floor of 4.
* Mass land denial (Wizards, Feb 2025: cards that destroy, exile or bounce several lands, keep lands tapped or change the mana
  several lands make; "you should not expect to see these cards anywhere in Brackets 1 to 3"): any such card sets a floor of 4.
  Found by the text rules in ``vault.role_rules``; Wizards publishes examples and a description, not a list.
* Extra turns (Wizards, Feb 2025: none in Bracket 1; in Brackets 2 and 3 only in low quantities, never chained or looped): any
  extra-turn card sets a floor of 2. Wizards gives no number and chains cannot be seen from card counts, so the count is
  shown and nothing more is claimed.
* Two-card combos (Wizards, Feb 2025: no intentional two-card infinite combos in Brackets 1 and 2, none that come together early
  and cheaply in Bracket 3; Oct 2025: expect to play at least six turns before winning or losing in Bracket 3): a two-card combo
  that Commander Spellbook says is infinite or wins the game sets a floor of 3. Whether it is "early" is not computed.
* Tutors: **no floor.** Wizards removed the tutor limits from the brackets in Oct 2025 ("rely on Game Changers to catch the most
  efficient tutors"). Tutors are listed for information only; the efficient ones are already counted as Game Changers.

What it cannot see is listed in ``NOT_COMPUTED`` and repeated in every answer.
"""

from __future__ import annotations

from datetime import date

RULES_READ = date(2026, 10, 7)
SOURCES = [
    {"source": "Wizards of the Coast", "what": "Commander format page: the five brackets and the Game Changers list and limits",
     "url": "https://magic.wizards.com/en/formats/commander"},
    {"source": "Wizards of the Coast", "what": "Introducing Commander Brackets Beta (Feb 2025): mass land denial, extra turns, two-card combos by bracket",
     "url": "https://magic.wizards.com/en/news/announcements/introducing-commander-brackets-beta"},
    {"source": "Wizards of the Coast", "what": "Commander Brackets Beta Update (Oct 2025): expectations per bracket, tutor limits removed",
     "url": "https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-october-21-2025"},
]
NOT_COMPUTED = [
    "intent: the brackets are about the experience the table expects, and a deck can sit above its floor",
    "chained or looped extra turns (only the number of extra-turn cards is counted)",
    "how early a two-card combo comes together, and any combo Commander Spellbook does not list",
    "mass land denial worded in a way the Vault's text rules do not recognise (Wizards publishes examples, not a list)",
    "Bracket 5 (cEDH) versus Bracket 4: nothing in the card list separates them",
]
LABEL = ("Computed by the Vault from Wizards' published Commander Bracket rules: the lowest bracket this deck's contents allow. "
         "A floor, not a placement and not a power score.")
FLOOR_MEANS = {1: "nothing found rules out Bracket 1 (Exhibition), which is about theme and intent more than contents",
               2: "not Bracket 1 (Exhibition); at least Bracket 2 (Core)",
               3: "at least Bracket 3 (Upgraded)",
               4: "at least Bracket 4 (Optimized): Bracket 3 and below do not allow this"}
RULE = {
    "game_changers": "Wizards: Brackets 1 and 2 exclude Game Changers, Bracket 3 allows up to three, Brackets 4 and 5 allow any number",
    "mass_land_denial": "Wizards: mass land denial is not expected anywhere in Brackets 1 to 3",
    "extra_turns": "Wizards: no extra-turn cards in Bracket 1; only in low quantities and never chained or looped in Brackets 2 and 3",
    "two_card_combos": "Wizards: no intentional two-card infinite combos in Brackets 1 and 2, no early ones in Bracket 3",
    "tutors": "Wizards removed tutor limits from the brackets in Oct 2025; shown for information, no floor",
}


def _group(rule: str, cards: list[dict], floor: int | None) -> dict:
    return {"count": sum(c.get("quantity", 1) for c in cards), "cards": cards, "floor": floor, "rule": rule}


def hint(game_changers: list[dict], land_denial: list[dict], extra_turns: list[dict], tutors: list[dict], combos: dict | None) -> dict:
    """The hint from the inputs: each a list of ``{"name", "quantity", ...}`` cards; ``combos`` is the two-card combos found
    (``{"checked": True, "combos": [...]}``), or ``{"checked": False, "reason": ...}`` / None when they were not asked for."""
    n_gc = sum(c["quantity"] for c in game_changers)
    gc_floor = 4 if n_gc > 3 else 3 if n_gc else None
    inputs = {
        "game_changers": _group(RULE["game_changers"], game_changers, gc_floor),
        "mass_land_denial": _group(RULE["mass_land_denial"], land_denial, 4 if land_denial else None),
        "extra_turns": _group(RULE["extra_turns"], extra_turns, 2 if extra_turns else None),
        "tutors": _group(RULE["tutors"], tutors, None),
    }
    if combos and combos.get("checked"):
        found = combos["combos"]
        inputs["two_card_combos"] = {"checked": True, "count": len(found), "combos": found, "floor": 3 if found else None,
                                     "rule": RULE["two_card_combos"],
                                     "note": "Two-card combos Commander Spellbook says are infinite or win the game; its own tag on each is shown as given."}
    else:
        reason = (combos or {}).get("reason") or "not asked for: pass include_combos to send the deck to Commander Spellbook"
        inputs["two_card_combos"] = {"checked": False, "count": None, "combos": [], "floor": None, "rule": RULE["two_card_combos"], "reason": reason}
    floors = [(i["floor"], name) for name, i in inputs.items() if i["floor"]]
    floor = max((f for f, _ in floors), default=1)
    why = [f"{name.replace('_', ' ')} ({inputs[name]['count']}) sets a floor of {f}" for f, name in sorted(floors, reverse=True)]
    if not inputs["two_card_combos"]["checked"]:
        why.append("two-card combos were not checked, so the floor may be higher")
    return {"label": LABEL, "floor": floor, "floor_means": FLOOR_MEANS[floor], "why": why or ["no checked input sets a floor above 1"],
            "inputs": inputs, "rules_read_on": RULES_READ.isoformat(), "sources": SOURCES, "not_computed": NOT_COMPUTED,
            "applies_to": "Commander decks (the brackets are Wizards' for Commander)"}
