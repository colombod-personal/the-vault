"""The deck ideas lab, server side (#163, docs/deck-ideas-lab-design.md): what the collection covers of a saved deck, what is
missing, what another deck holds, and for a missing card which owned card could stand in.

Two answers, both computed here and only shown by the web view and the assistants:

- ``ideas``: the deck's cards in role **lanes**. Every card of the deck (every copy) is in exactly one lane, chosen by a fixed
  priority over its roles; each card carries what the allocation of ``vault.deck_independence`` (#165) says about it (``gets``,
  ``not_owned``, ``held_by_other_deck``), a ``status`` computed from them, and borrowing as an independent annotation.
- ``alternatives``: for a card, the cards the person owns that do the same job in this deck: sharing a **core** role, in the
  deck's colour identity, legal in the format, with copies left under the format's copy limit. Those are filters, applied before
  ranking. Whether two cards are equivalent is decided by one small function, :func:`equivalence`, so the finer roles of #166 can
  replace it without touching the rest.

Phase 1 runs on today's eight coarse roles (``deck_tools.ROLE_TAGS``): Scryfall Tagger tags, and for a role where a card has no
tag the Vault's Oracle-text rules (``role_rules``), always marked as such. Candidates for alternatives are found through tags only
(a database query that rules over text cannot replace); the target card's own roles include the rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from mtg_toolkits import delta
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import analytics
from . import deck_independence as di
from . import deck_tools as dt
from .card_faces import all_text, front_mana_cost
from .catalog_queries import NON_PLAYABLE_LAYOUTS
from .models import Entry, OracleCard, OracleTagLink

OTHER, LANDS = "other", "lands"
LANE_ROLES = tuple(dt.ROLE_TAGS)  # the priority: a card goes to the first of these it has
LANES = LANE_ROLES + (OTHER, LANDS)
LABELS = {"ramp": "Ramp", "draw": "Draw", "removal": "Removal", "sweeper": "Sweepers", "counterspell": "Counterspells",
          "tutor": "Tutors", "recursion": "Recursion", "sacrifice_outlet": "Sacrifice outlets", OTHER: "Other", LANDS: "Lands"}
LANE_PAGE = 25  # cards a lane shows on a page unless asked for another size
COMBO_LINES = 30  # combos listed (the view draws a few dozen lines at most)
STATUS_ORDER = {"missing": 0, "partial": 1, "owned": 2}  # what needs a decision comes first in a lane
ROLES_NOTE = ("Roles are the Vault's eight coarse roles (ramp, draw, removal, sweeper, counterspell, tutor, recursion, sacrifice "
              "outlet): Scryfall Tagger tags, a community's opinion, and where a card has no tag for a role, a rule over its Oracle text "
              "(basis 'computed'). A match on a coarse role is a hint, not proof that two cards play alike.")
LANE_NOTE = ("Every copy is in exactly one lane. A card goes to the first of ramp, draw, removal, sweeper, counterspell, tutor, "
             "recursion, sacrifice outlet that it has a role for (its other roles are its tags); a card with no role goes to Other, "
             "and every land to Lands. Basic lands are never short: the allocation leaves them out, as deck independence does.")
BORROW_NOTE = ("`gets` is the copies this deck holds when every saved deck takes the copies it needs, the one closest to complete "
               "first (the rule of get_deck_overlap). `not_owned` copies must be bought; `held_by_other_deck` copies exist and "
               "another deck holds them, so they can be moved. `borrowed` is an annotation of its own: true when this deck lacks a "
               "copy another deck holds, or holds a copy another deck also wants.")


def key_of(name: str) -> tuple:
    """The key the allocation counts a card by (front face, case-folded)."""
    return (name.split(" // ")[0].strip().casefold(),)


# -- roles: strength and equivalence (the part #166 replaces) -----------------------------------------------------------

WEAK = {"weak", "very_weak", "very weak"}


def role_strength(entry: dict) -> str:
    """``core`` (the card exists to do this) or ``incidental`` (does it on the side) for one role of a card, from
    ``deck_tools.role_entries``: a Tagger tag weighted weak is incidental, any other weight core, and a role found by a rule over
    the Oracle text is core (the rules are narrow on purpose). Replaced when the finer roles of #166 exist."""
    return "incidental" if entry.get("basis") != "computed" and entry.get("tag_weight") in WEAK else "core"


def equivalence(target: dict[str, str], candidate: dict[str, str]) -> dict | None:
    """Whether ``candidate`` can stand in for ``target``, given each card's roles as ``{role: "core" | "incidental"}``. The one
    decision #166's ``equivalents`` will replace: today two cards are equivalent when they share at least one **core** role, and
    the more core roles they share the better. Returns ``None`` (not equivalent) or ``{"roles": [...shared core roles...]}``."""
    shared = [role for role, strength in target.items() if strength == "core" and candidate.get(role) == "core"]
    return {"roles": shared} if shared else None


# -- the deck's cards ---------------------------------------------------------------------------------------------------

@dataclass
class Line:
    """One card of the deck, every copy of it (a card listed twice is one line)."""
    key: tuple
    name: str
    need: int
    section: str
    entry: dt.Entry  # the first listing: its Oracle card, when the catalog knows it


def deck_lines(resolved: dt.Resolved) -> list[Line]:
    out: dict[tuple, Line] = {}
    for entry in resolved.played():
        key = key_of(entry.line.name)
        if key in out:
            out[key].need += entry.line.quantity
        else:
            out[key] = Line(key, entry.name, entry.line.quantity, entry.line.section, entry)
    return list(out.values())


def is_basic(card: OracleCard | None, name: str) -> bool:
    return name.strip().lower() in di.BASICS or bool(card and "Basic Land" in (card.type_line or ""))


def lane_of(card: OracleCard | None, roles: dict) -> str:
    """The one lane a card is in: lands first, then the first coarse role in the fixed priority, else Other."""
    if card is not None and dt.is_land(card):
        return LANDS
    return next((r for r in LANE_ROLES if r in roles), OTHER)


def state(alloc: di.Allocation, deck, line_key: tuple, need: int, basic: bool) -> dict:
    """What the allocation says about one card of this deck. Counted by name, any printing owned counts."""
    c = alloc.cards.get(line_key)
    if basic or c is None:  # basic lands are not allocated (never short, as in deck independence)
        have = alloc.have.get(line_key, 0)
        return {"have": have, "gets": need, "lacking": 0, "not_owned": 0, "held_by_other_deck": 0, "status": "owned",
                "borrowed": False, "holders": [], "wanted_by": []}
    gets = c.gets.get(deck.id, 0)
    lacking = need - gets
    not_owned = max(0, need - c.have)
    held = lacking - not_owned
    others = [r for r, _ in c.wants if r.deck.id != deck.id]
    holders = [r for r in others if c.gets.get(r.deck.id, 0) > 0] if held > 0 else []
    wanted_by = others if gets > 0 and c.contested else []
    return {"have": c.have, "gets": gets, "lacking": lacking, "not_owned": not_owned, "held_by_other_deck": held,
            "status": "owned" if lacking == 0 else "missing" if gets == 0 else "partial",
            "borrowed": held > 0 or bool(wanted_by), "holders": holders, "wanted_by": wanted_by}


def _deck_ref(r) -> dict:
    return {"id": r.deck.id, "name": r.deck.name}


def prices_for(db: Session, user_id: int, wanted: list[tuple[str, int]]) -> dict[tuple, tuple]:
    """Scryfall's cheapest known price and its day for each ``(name, copies)``: the figure ``shopping_list`` gives."""
    if not wanted:
        return {}
    lines = analytics.price_coverages(db, user_id, [{"cards": [{"name": n, "missing": max(1, q)} for n, q in wanted]}],
                                      owned_printings=False)[0]["cards"]
    return {key_of(n): (line["unit_price"], line["price_date"]) for (n, _), line in zip(wanted, lines)}


def _buy(quantity: int, price: tuple | None) -> dict:
    unit, day = price or (None, None)
    return {"quantity": quantity, "unit_price": unit, "price_date": day,
            "cost": None if unit is None else round(unit * quantity, 2), "price_status": "priced" if unit is not None else "unpriced"}


def _move(holders: list, quantity: int) -> dict | None:
    """Take the copy from the last deck in the allocation order that holds one (the donor ``deck_independence`` picks)."""
    return {"kind": "move", "from_deck": _deck_ref(holders[-1]), "quantity": quantity} if holders else None


def _alt_link(deck_id: int, name: str) -> str:
    return f"/api/v1/decks/{deck_id}/ideas/alternatives?" + urlencode({"card": name})


def ideas(db: Session, user_id: int, deck, decks: list, resolved: dt.Resolved, combos: dict | None = None) -> dict:
    """The deck's lanes and header counts, every item (the caller pages each lane). ``resolved`` is the deck's list matched to
    the catalog; ``decks`` all of the person's saved decks (the allocation is computed across them)."""
    alloc = di.allocate(decks, di.owned_counts(db, user_id))
    lines = deck_lines(resolved)
    cards = [l.entry.card for l in lines if l.entry.card is not None]
    roles = dt.role_entries(db, cards)
    rows, wanted = [], []
    for l in lines:
        card = l.entry.card
        basic = is_basic(card, l.name)
        s = state(alloc, deck, l.key, l.need, basic)
        found = roles.get(card.oracle_id, {}) if card else {}
        lane = lane_of(card, found)
        row = {"card": l.name, "oracle_id": card.oracle_id if card else None, "known": card is not None, "section": l.section,
               "lane": lane, "need": l.need, "have": s["have"], "gets": s["gets"], "not_owned": s["not_owned"],
               "held_by_other_deck": s["held_by_other_deck"], "status": s["status"], "basic": basic, "borrowed": s["borrowed"],
               "borrowed_from": s["holders"][0].deck.name if s["holders"] else None,
               "borrowed_from_deck_id": s["holders"][0].deck.id if s["holders"] else None,
               "also_wanted_by": [r.deck.name for r in s["wanted_by"]][:di.PREVIEW_DECKS],
               "type_line": card.type_line if card else None, "mana_cost": front_mana_cost(card) if card else None,
               "mana_value": card.cmc if card else None,
               "roles": [{"role": r, "strength": role_strength(e), "basis": e["basis"]} for r, e in found.items()],
               "tags": [r for r in found if r != lane], "move": _move(s["holders"], s["held_by_other_deck"]) if s["held_by_other_deck"] else None,
               "buy": None, "_links": {}}
        if s["lacking"]:
            row["_links"]["alternatives"] = {"href": _alt_link(deck.id, l.name)}
        if s["not_owned"]:
            wanted.append((l.name, s["not_owned"]))
        rows.append(row)
    priced = prices_for(db, user_id, wanted)
    dates = []
    for row in rows:
        if row["not_owned"]:
            row["buy"] = _buy(row["not_owned"], priced.get(key_of(row["card"])))
            if row["buy"]["price_date"]:
                dates.append(row["buy"]["price_date"])
    lanes = []
    for lane in LANES:
        in_lane = [r for r in rows if r["lane"] == lane]
        in_lane.sort(key=lambda r: (STATUS_ORDER[r["status"]], r["card"].lower()))
        lanes.append({"lane": lane, "label": LABELS[lane], "kind": "role" if lane in LANE_ROLES else lane,
                      "copies": sum(r["need"] for r in in_lane), "cards": len(in_lane),
                      "covered": sum(r["gets"] for r in in_lane),
                      "missing": sum(1 for r in in_lane if r["not_owned"] > 0),
                      "borrowed": sum(1 for r in in_lane if r["held_by_other_deck"] > 0), "all": in_lane})
    copies = sum(r["need"] for r in rows)
    covered = sum(r["gets"] for r in rows)
    summary = {"copies": copies, "covered": covered, "lacking": copies - covered, "cards": len(rows),
               "missing": sum(1 for r in rows if r["not_owned"] > 0), "borrowed": sum(1 for r in rows if r["held_by_other_deck"] > 0),
               "partial": sum(1 for r in rows if r["status"] == "partial"),
               "complete": copies == covered, "unknown_cards": sum(1 for r in rows if not r["known"])}
    rule = di.DEFAULT_RULE
    return {"lanes": lanes, "summary": summary,
            "allocation": {"rule": rule, "priority_applied": False, "description": di.allocation_note(rule), "limits": di.LIMITS},
            "prices_date": max(dates) if dates else None, "rows": rows}


def combo_edges(found: dict, rows: list[dict]) -> dict:
    """The combos Commander Spellbook knows in this deck (``combos.ask`` results), as lines between cards of the deck. ``owned``:
    every card of the combo is fully held by this deck. Their descriptions are theirs; each links to its page."""
    from . import combos as cs
    by = {r["card"].lower(): r for r in rows}
    out = []
    for v in found.get("included") or []:
        names = list(dict.fromkeys(u["card"]["name"] for u in v.get("uses", []) if u.get("card")))
        mine = [by.get(n.lower()) for n in names]
        if len(names) < 2 or any(m is None for m in mine):
            continue
        out.append({"cards": names, "owned": all(m["status"] == "owned" for m in mine), "url": cs.PAGE_URL + str(v["id"]),
                    "produces": [p["feature"]["name"] for p in v.get("produces", []) if p.get("feature")][:4],
                    "source": "Commander Spellbook"})
    out.sort(key=lambda c: (not c["owned"], c["cards"]))
    return {"checked": True, "total": len(out), "combos": out[:COMBO_LINES]}


# -- alternatives -------------------------------------------------------------------------------------------------------

def copy_limit(card: OracleCard, fmt: str) -> int | None:
    """Copies of this card a deck may hold in ``fmt``; ``None`` for any number (basic lands, 'a deck can have any number of cards
    named'). The same rule ``deck_tools.legality`` applies."""
    if "Basic Land" in (card.type_line or "") or "A deck can have any number of cards named" in all_text(card):
        return None
    return 1 if fmt in dt.SINGLETON or card.legalities.get(fmt) == "restricted" else 4


def deck_identity(resolved: dt.Resolved, fmt: str) -> list[str]:
    """The colours a candidate must stay within: the commander's identity in a Commander-style format with a commander, else the
    colours of the deck's own cards (what ``find_upgrades`` does)."""
    known = [e for e in resolved.played() if e.card]
    colours = set(dt.identity(known))
    commanders = resolved.section("commander")
    if fmt in dt.COMMANDER_STYLE and commanders:
        colours = set(dt.identity(commanders))
    return [c for c in dt.COLORS if c in colours]


def _image(card: OracleCard) -> dict | None:
    return {"normal": card.image_normal, "artist": card.artist} if card.image_normal else None


def alternatives(db: Session, user_id: int, deck, decks: list, resolved: dt.Resolved, target: OracleCard, fmt: str) -> dict:
    """Owned cards that can stand in for ``target`` in this deck, best first (all of them: the caller pages). No price is looked
    up here; the caller prices the page it shows."""
    fmt = dt.check_format(fmt)
    alloc = di.allocate(decks, di.owned_counts(db, user_id))
    lines = {l.key: l for l in deck_lines(resolved)}
    mine = lines.get(key_of(target.name))
    found_roles = dt.role_entries(db, [target])[target.oracle_id]
    target_roles = {r: role_strength(e) for r, e in found_roles.items()}
    core = [r for r, s in target_roles.items() if s == "core"]
    colours = deck_identity(resolved, fmt)
    s = state(alloc, deck, key_of(target.name), mine.need, is_basic(target, target.name)) if mine else None
    out = {"format": fmt, "color_identity": colours, "rows": [], "reason": None,
           "target": {"card": target.name, "oracle_id": target.oracle_id, "in_deck": mine.need if mine else 0,
                      "status": s["status"] if s else None, "need": mine.need if mine else None, "gets": s["gets"] if s else None,
                      "not_owned": s["not_owned"] if s else None, "held_by_other_deck": s["held_by_other_deck"] if s else None,
                      "borrowed_from": s["holders"][0].deck.name if s and s["holders"] else None,
                      "roles": [{"role": r, "strength": role_strength(e), "basis": e["basis"]} for r, e in found_roles.items()],
                      "core_roles": core, "type_line": target.type_line, "mana_cost": front_mana_cost(target),
                      "mana_value": target.cmc, "image": _image(target), "scryfall_uri": target.scryfall_uri}}
    if not core:
        out["reason"] = "no_role"
        return out
    tag_ids = {t for r in core for t in dt.role_tag_ids(db)[r]}
    front = func.lower(func.split_part(Entry.name, " // ", 1))  # one expression, so the select and the group by share its parameters
    owned = select(front).where(Entry.user_id == user_id).group_by(front).having(func.sum(Entry.quantity) > 0)
    query = (select(OracleCard)
             .where(OracleCard.oracle_id.in_(select(OracleTagLink.oracle_id).where(OracleTagLink.tag_id.in_(list(tag_ids)))),
                    OracleCard.oracle_id != target.oracle_id, OracleCard.digital.is_(False),
                    OracleCard.layout.notin_(list(NON_PLAYABLE_LAYOUTS)), OracleCard.legalities[fmt].astext.in_(dt.LEGAL),
                    func.lower(func.split_part(OracleCard.name, " // ", 1)).in_(owned),
                    *[~OracleCard.color_identity.contains([c]) for c in dt.COLORS if c not in colours])
             .order_by(OracleCard.name).limit(2000))
    found = list(db.scalars(query))
    seen: set[tuple] = {key_of(target.name)}
    candidates = []
    for card in found:
        key = key_of(card.name)
        if key in seen or is_basic(card, card.name):
            continue
        seen.add(key)
        candidates.append((key, card))
    tagged = dt.roles_of(db, [c.oracle_id for _, c in candidates])
    for key, card in candidates:
        theirs = {r: role_strength({"basis": "scryfall_tagger", "tag_weight": w}) for r, w in tagged.get(card.oracle_id, {}).items()}
        match = equivalence(target_roles, theirs)
        if match is None:
            continue
        in_deck = lines[key].need if key in lines else 0
        limit = copy_limit(card, fmt)
        room = None if limit is None else limit - in_deck
        if room is not None and room <= 0:  # the deck already holds as many as the format allows (an owned Sol Ring in a deck that runs it)
            continue
        c = alloc.cards.get(key)
        have = alloc.have.get(key, 0)
        spare = have - (c.need_all if c else 0)  # copies no saved deck needs
        holders = [r for r, _ in (c.wants if c else []) if r.deck.id != deck.id and c.gets.get(r.deck.id, 0) > 0]
        if spare <= 0 and not holders:  # every copy owned is this deck's own: there is nothing to offer
            continue
        borrowed = spare <= 0
        mv = card.cmc or 0.0
        diff = abs(mv - (target.cmc or 0.0))
        out["rows"].append({
            "card": card.name, "oracle_id": card.oracle_id, "type_line": card.type_line, "mana_cost": front_mana_cost(card),
            "mana_value": mv, "image": _image(card), "scryfall_uri": card.scryfall_uri, "copies_owned": have,
            "copies_free": max(0, spare), "borrowed": borrowed,
            "borrowed_from": holders[0].deck.name if borrowed else None,
            "borrowed_from_deck_id": holders[0].deck.id if borrowed else None,
            "shared_roles": [{"role": r, "target": target_roles[r], "candidate": theirs[r]} for r in match["roles"]],
            "why": f"both: {', '.join(match['roles'])} (core, coarse role); mana value {mv:g} vs {(target.cmc or 0.0):g}",
            "mana_value_difference": round(diff, 2), "legal": True, "in_colours": True, "in_deck": in_deck,
            "remaining_allowance": room, "move": _move(holders, 1) if borrowed else None, "buy": None,
            "_sort": [1 if borrowed else 0, -len(match["roles"]), round(diff, 2), card.name.lower()]})
    if not out["rows"]:
        out["reason"] = "none_found"
    return out


def price_page(db: Session, user_id: int, rows: list[dict], target: OracleCard) -> tuple[dict, str | None]:
    """Prices for the rows shown (a copy to buy when the owned ones are another deck's) and for the target card; fills the rows'
    ``buy``. Returns the target's buy block and the newest price day used."""
    wanted = [(target.name, 1)]
    wanted += [(r["card"], 1) for r in rows if r["borrowed"]]
    priced = prices_for(db, user_id, wanted)
    dates = []
    for r in rows:
        if r["borrowed"]:
            r["buy"] = _buy(1, priced.get(key_of(r["card"])))
            dates.append(r["buy"]["price_date"])
    unit = priced.get(key_of(target.name))
    block = _buy(1, unit)
    dates.append(block["price_date"])
    dates = [d for d in dates if d]
    return block, max(dates) if dates else None
