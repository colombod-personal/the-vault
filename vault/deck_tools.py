"""Deck analysis from the catalog: counts, roles, legality, upgrade candidates, and a validator.

The rule for all of it: **code counts, searches, prices and validates; a model only reasons over the
results.** Nothing here asks a model anything. A budget or a legality verdict is computed here, from
the stored Scryfall data, so it is a guarantee and not a hope. What it does *not* check is said in
every answer (``not_checked``), and every answer carries ``provenance`` built by the caller.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from mtg_toolkits import decklist
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session

from . import brackets, deck_text, role_rules
from .card_faces import all_text, front_mana_cost
from .catalog_queries import card_priority
from .models import OracleCard, OraclePrice, OracleTag, OracleTagLink
from .prices import plausible_price

FORMATS = ("commander", "standard", "pioneer", "modern", "legacy", "vintage", "pauper", "brawl", "standardbrawl",
           "historic", "timeless", "oathbreaker", "paupercommander", "premodern", "penny", "duel", "predh",
           "oldschool", "gladiator", "alchemy")
SINGLETON = {"commander", "duel", "predh", "paupercommander", "brawl", "standardbrawl", "oathbreaker", "gladiator"}
COMMANDER_STYLE = {"commander", "duel", "predh", "paupercommander", "brawl", "standardbrawl", "oathbreaker"}  # color identity applies
SIZE_100 = {"commander", "duel", "predh", "paupercommander"}
LEGAL = ("legal", "restricted")
COLORS = ("W", "U", "B", "R", "G")

# Roles are Scryfall Tagger tags (a community's opinion, with weights), rolled up through the tag tree.
ROLE_TAGS = {
    "ramp": ["ramp"], "draw": ["draw-engine", "repeatable-pure-draw", "card-advantage"],
    "removal": ["removal", "spot-removal"], "sweeper": ["sweeper"], "counterspell": ["counterspell"],
    "tutor": ["tutor"], "recursion": ["recursion", "reanimate"], "sacrifice_outlet": ["sacrifice-outlet"],
}
# A common community guideline for 100-card singleton decks. It is NOT a rule and says nothing about power.
COMMANDER_TARGETS = {"ramp": 10, "draw": 10, "removal": 8, "sweeper": 2}
MAX_LISTED = 30  # cards named per role in a stats answer
MAX_CANDIDATES = 15
MAX_DECK_CARDS = 500
PLAYED = decklist.PLAYED_SECTIONS


class DeckError(ValueError):
    """The deck cannot be analysed as given (bad format name, too big, nothing parsed)."""


@dataclass
class Entry:
    line: decklist.DeckLine
    card: OracleCard | None

    @property
    def name(self) -> str:
        return self.card.name if self.card else self.line.name


@dataclass
class Resolved:
    entries: list[Entry] = field(default_factory=list)

    @property
    def unmatched(self) -> list[str]:
        return sorted({e.line.name for e in self.entries if e.card is None})

    def played(self) -> list[Entry]:
        return [e for e in self.entries if e.line.section in PLAYED]

    def section(self, name: str) -> list[Entry]:
        return [e for e in self.entries if e.line.section == name]


def loose_name(name: str) -> str:
    """A card name with what differs between sources removed: the back face, an Alchemy "A-" prefix,
    accents, case, punctuation and spaces ("Lim-Dûl's Vault" and "Lim-Dul's Vault" both become
    "limdulsvault"). For pointing out near matches, never for counting a card as owned."""
    front = name.split(" // ")[0].strip()
    front = re.sub(r"^a-(?=\S)", "", front, flags=re.IGNORECASE)
    ascii_name = unicodedata.normalize("NFKD", front).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


def parse(text: str) -> decklist.Decklist:
    try:
        deck = deck_text.parse_text(text)
    except (ValueError, OverflowError) as exc:
        raise DeckError(f"The decklist could not be read: {exc}") from exc
    if not deck.lines:
        raise DeckError("No cards found in the decklist")
    if len(deck.lines) > MAX_DECK_CARDS:
        raise DeckError(f"At most {MAX_DECK_CARDS} lines are analysed at once")
    return deck


def check_format(fmt: str) -> str:
    fmt = (fmt or "").lower()
    if fmt not in FORMATS:
        raise DeckError(f"Unknown format {fmt!r}; use one of: {', '.join(FORMATS)}")
    return fmt


def resolve(db: Session, deck: decklist.Decklist) -> Resolved:
    """Match each line to an Oracle card by exact name (front face is enough for double-faced cards)."""
    names = {l.name.strip().lower() for l in deck.lines}
    by_name: dict[str, OracleCard] = {}

    def keep(key: str, card: OracleCard) -> None:  # several cards can share a name (a token); take the playable one
        if key not in by_name or card_priority(card) < card_priority(by_name[key]):
            by_name[key] = card

    for card in db.scalars(select(OracleCard).where(func.lower(OracleCard.name).in_(names))):
        keep(card.name.lower(), card)
    missing = names - by_name.keys()
    if missing:
        for card in db.scalars(select(OracleCard).where(or_(*[func.lower(OracleCard.name).like(m.replace("%", r"\%").replace("_", r"\_") + " // %", escape="\\")
                                                              for m in missing]))):
            keep(card.name.lower().split(" // ")[0], card)
    return Resolved([Entry(l, by_name.get(l.name.strip().lower())) for l in deck.lines])


def _types(card: OracleCard) -> list[str]:
    line = (card.type_line or "").split("—")[0].split("//")[0]
    return [t for t in re.split(r"\s+", line.replace("Legendary", "").replace("Basic", "").replace("Snow", "").strip()) if t]


def is_land(card: OracleCard | None) -> bool:
    return bool(card and "Land" in (card.type_line or "").split("//")[0])


def hidden_tags() -> set[str]:
    """Tag ids or slugs switched off (``HIDDEN_TAGS``, comma separated). Tags are community-maintained; Scryfall asks
    downstream apps to be able to hide one temporarily (scryfall.com/docs/api/tags). A hidden tag and its children
    give no roles."""
    return {t.strip() for t in os.environ.get("HIDDEN_TAGS", "").split(",") if t.strip()}


def role_tag_ids(db: Session) -> dict[str, set[str]]:
    """Tag ids behind each role, with all descendants (``removal`` has none of its own links)."""
    hidden = hidden_tags()
    tags = [t for t in db.execute(select(OracleTag.id, OracleTag.slug, OracleTag.child_ids)).all()
            if t.id not in hidden and t.slug not in hidden]
    by_id = {t.id: t for t in tags}
    by_slug = {t.slug: t for t in tags}
    out: dict[str, set[str]] = {}
    for role, slugs in ROLE_TAGS.items():
        keep: set[str] = set()
        stack = [by_slug[s].id for s in slugs if s in by_slug]
        while stack:
            tid = stack.pop()
            if tid not in keep:
                keep.add(tid)
                stack.extend(c for c in (by_id[tid].child_ids or []) if c in by_id)
        out[role] = keep
    return out


def roles_of(db: Session, oracle_ids: list[str]) -> dict[str, dict[str, str | None]]:
    """For each card: the roles it has, with the best Tagger weight (strong > median > weak)."""
    if not oracle_ids:
        return {}
    rank = {"very_strong": 4, "very strong": 4, "strong": 3, "median": 2, "weak": 1, "very_weak": 0, "very weak": 0}
    wanted = role_tag_ids(db)
    tag_role = {tid: role for role, tids in wanted.items() for tid in tids}
    out: dict[str, dict[str, str | None]] = defaultdict(dict)
    rows = db.execute(select(OracleTagLink.oracle_id, OracleTagLink.tag_id, OracleTagLink.weight)
                      .where(OracleTagLink.oracle_id.in_(oracle_ids), OracleTagLink.tag_id.in_(list(tag_role)))).all()
    for oid, tid, weight in rows:
        role = tag_role[tid]
        best = out[oid].get(role, "")
        if role not in out[oid] or rank.get(weight or "", 2) > rank.get(best or "", -1):
            out[oid][role] = weight
    return out


def role_entries(db: Session, cards: list[OracleCard]) -> dict[str, dict[str, dict]]:
    """For each card: the roles it has and where each came from. Scryfall's Tagger tags win; for a role where the card has **no**
    Tagger tag, a rule over its Oracle text (``vault.role_rules``) may add it, always marked ``basis: "computed"`` with the rule's id
    (docs/card-roles-design.md). ``{oracle_id: {role: {"basis", "tag_weight", "rule"}}}``."""
    tagged = roles_of(db, list({c.oracle_id for c in cards}))
    out: dict[str, dict[str, dict]] = {}
    for card in cards:
        if card.oracle_id in out:
            continue
        entry = {role: {"basis": "scryfall_tagger", "tag_weight": weight, "rule": None}
                 for role, weight in tagged.get(card.oracle_id, {}).items()}
        for role, rule in role_rules.derive(card).items():
            entry.setdefault(role, {"basis": "computed", "tag_weight": None, "rule": rule})
        out[card.oracle_id] = entry
    return out


def prices_of(db: Session, oracle_ids: list[str]) -> dict[str, OraclePrice]:
    if not oracle_ids:
        return {}
    return {p.oracle_id: p for p in db.scalars(select(OraclePrice).where(OraclePrice.oracle_id.in_(oracle_ids)))}


def identity(entries: list[Entry]) -> list[str]:
    found = {c for e in entries if e.card for c in e.card.color_identity or []}
    return [c for c in COLORS if c in found]


# -- stats --------------------------------------------------------------------------------------

def stats(db: Session, resolved: Resolved, combos: dict | None = None) -> dict:
    """Counts, curve, roles, cost and the Commander Bracket hint. ``combos``: the two-card combos found by Commander Spellbook
    (``{"checked": True, "combos": [...]}``), or ``{"checked": False, "reason": ...}``; None when they were not asked for."""
    played = resolved.played()
    known = [e for e in played if e.card]
    oids = list({e.card.oracle_id for e in known})
    roles = role_entries(db, [e.card for e in known])
    prices = prices_of(db, oids)
    total = sum(e.line.quantity for e in played)
    lands = sum(e.line.quantity for e in known if is_land(e.card))
    nonland = [e for e in known if not is_land(e.card)]
    curve = Counter()
    for e in nonland:
        curve[min(int(e.card.cmc or 0), 7)] += e.line.quantity
    kinds = Counter()
    for e in known:
        for t in _types(e.card):
            if t in ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Land", "Battle"):
                kinds[t] += e.line.quantity
    role_cards: dict[str, list[dict]] = {r: [] for r in ROLE_TAGS}
    for e in known:
        for role, found in roles.get(e.card.oracle_id, {}).items():
            role_cards[role].append({"name": e.card.name, "quantity": e.line.quantity, "tag_weight": found["tag_weight"],
                                     "basis": found["basis"], **({"rule": found["rule"]} if found["rule"] else {})})
    cost = sum((prices[e.card.oracle_id].usd or 0) * e.line.quantity for e in known if e.card.oracle_id in prices)
    priced = sum(e.line.quantity for e in known if e.card.oracle_id in prices and prices[e.card.oracle_id].usd is not None)
    cmcs = [(e.card.cmc or 0, e.line.quantity) for e in nonland]
    avg = sum(c * q for c, q in cmcs) / max(1, sum(q for _, q in cmcs))
    changers = [{"name": e.card.name, "quantity": e.line.quantity} for e in known if e.card.game_changer]
    n_changers = sum(c["quantity"] for c in changers)
    signalled = {"mass_land_denial": [], "extra_turn": []}
    for e in known:
        for signal, rule in role_rules.signals(e.card).items():
            signalled[signal].append({"name": e.card.name, "quantity": e.line.quantity, "basis": "computed", "rule": rule})
    tutors = [{"name": c["name"], "quantity": c["quantity"], "basis": c["basis"]} for c in role_cards["tutor"]]
    bracket = brackets.hint(changers, signalled["mass_land_denial"], signalled["extra_turn"], tutors, combos)
    return {
        "cards": total, "unique": len(played), "by_section": dict(Counter(e.line.section for e in resolved.entries for _ in range(e.line.quantity))),
        "lands": lands, "nonland": total - lands, "types": dict(kinds), "average_mana_value_nonland": round(avg, 2),
        "curve": {("7+" if k == 7 else str(k)): curve.get(k, 0) for k in range(8)},
        "color_identity": identity(known),
        "roles": {r: {"count": sum(c["quantity"] for c in cards),
                      "from_tagger": sum(c["quantity"] for c in cards if c["basis"] == "scryfall_tagger"),
                      "computed": sum(c["quantity"] for c in cards if c["basis"] == "computed"), "cards": cards[:MAX_LISTED]}
                  for r, cards in role_cards.items()},
        "estimated_cost_usd": round(cost, 2), "priced_cards": priced, "unpriced_cards": total - priced,
        "unmatched": resolved.unmatched,
        "game_changers": {
            "count": n_changers, "cards": changers,
            "bracket_floor": 4 if n_changers > 3 else 3 if n_changers else None,
            "note": "Scryfall marks Wizards' Game Changers. Brackets 1 and 2 allow none, Bracket 3 up to three (Wizards' Commander format "
                    "page). This count alone gives `bracket_floor`; the full hint, with every input Wizards' rules name, is `bracket`.",
        },
        "bracket": bracket,
        "price_note": "Cheapest priced paper printing of each card, from Scryfall; see provenance for the date.",
        "role_note": "Roles are Scryfall Tagger tags, a community's opinion with weights, not rules. Where a card has no Tagger tag for a role, "
                     "the Vault may add the role from rules over its Oracle text: those entries say basis 'computed' and name the rule "
                     "(docs/card-roles-design.md), and are counted separately (from_tagger, computed).",
    }


# -- legality -----------------------------------------------------------------------------------

def legality(resolved: Resolved, fmt: str) -> dict:
    fmt = check_format(fmt)
    issues: list[dict] = []
    played = resolved.played()
    for e in played:
        if e.card is None:
            issues.append({"kind": "unknown_card", "card": e.line.name, "detail": "not found in the card catalog (misspelled, or not paper Magic)"})
        elif e.card.legalities.get(fmt) not in LEGAL:
            issues.append({"kind": "not_legal", "card": e.card.name, "detail": f"{e.card.legalities.get(fmt, 'unknown')} in {fmt}"})
    counts = Counter()
    for e in played:
        counts[e.name] += e.line.quantity
    for e in played:
        if e.card is None or counts[e.name] == 0:
            continue
        name, n = e.card.name, counts[e.card.name]
        counts[e.card.name] = 0
        free = "Basic Land" in (e.card.type_line or "") or "A deck can have any number of cards named" in all_text(e.card)
        limit = 1 if fmt in SINGLETON or e.card.legalities.get(fmt) == "restricted" else 4
        if n > limit and not free:
            issues.append({"kind": "too_many_copies", "card": name, "detail": f"{n} copies, at most {limit} allowed in {fmt}"})
    total = sum(e.line.quantity for e in played)
    if fmt in SIZE_100 and total != 100:
        issues.append({"kind": "deck_size", "card": None, "detail": f"{total} cards; {fmt} decks have exactly 100 (commander included)"})
    elif fmt not in SIZE_100 and fmt not in COMMANDER_STYLE and total < 60 and fmt not in ("gladiator",):
        issues.append({"kind": "deck_size", "card": None, "detail": f"{total} cards; {fmt} main decks have at least 60"})
    side = sum(e.line.quantity for e in resolved.section("sideboard"))
    if fmt not in COMMANDER_STYLE and side > 15:
        issues.append({"kind": "sideboard_size", "card": None, "detail": f"{side} sideboard cards; at most 15"})
    commanders = resolved.section("commander")
    ident = None
    if fmt in COMMANDER_STYLE:
        if not commanders:
            issues.append({"kind": "no_commander", "card": None, "detail": "no Commander section found, so color identity was not checked"})
        else:
            ident = set(identity(commanders))
            for e in played:
                if e.card and e.line.section != "commander" and not set(e.card.color_identity or []) <= ident:
                    issues.append({"kind": "color_identity", "card": e.card.name,
                                   "detail": f"identity {''.join(e.card.color_identity) or 'colorless'} is outside the commander's {''.join(sorted(ident, key=COLORS.index)) or 'colorless'}"})
    return {"format": fmt, "legal": not issues, "issues": issues, "cards_checked": total,
            "commander_color_identity": sorted(ident, key=COLORS.index) if ident is not None else None,
            "not_checked": ["commander eligibility (legendary creature or 'can be your commander')", "partner/companion rules",
                            "cards named 'a deck can have up to N copies'", "sideboard legality of individual cards", "format-specific rules not listed here"]}


# -- upgrades -----------------------------------------------------------------------------------

def _price_fields(p: OraclePrice | None) -> dict:
    return {"price_usd": plausible_price(p.usd) if p else None, "price_date": p.day.isoformat() if p else None, "price_source": p.source if p else None}


def find_upgrades(db: Session, resolved: Resolved, fmt: str, budget_usd: float, roles: list[str] | None = None,
                  limit: int = 10, targets: dict[str, int] | None = None, owned: dict[str, int] | None = None) -> dict:
    """Candidates to add for the roles the deck is short of, all legal, in the deck's colors, not
    already in it, and each priced within the budget. Ordered by popularity (EDHREC rank), which is
    popularity and not power. Also lists the deck's least popular untagged cards as cut candidates."""
    fmt = check_format(fmt)
    if budget_usd < 0:
        raise DeckError("budget_usd cannot be negative")
    limit = max(1, min(limit, MAX_CANDIDATES))
    st = stats(db, resolved)
    played = resolved.played()
    in_deck = {e.card.oracle_id for e in played if e.card}
    ident = set(st["color_identity"])
    commanders = resolved.section("commander")
    if fmt in COMMANDER_STYLE and commanders:
        ident = set(identity(commanders))
    guide = targets or (COMMANDER_TARGETS if fmt in SIZE_100 else {})  # the usual counts exist for 100-card decks only
    wanted = list(roles) if roles else [r for r, n in guide.items() if st["roles"][r]["count"] < n]
    unknown = [r for r in wanted if r not in ROLE_TAGS]
    if unknown:
        raise DeckError(f"Unknown role(s) {unknown}; use: {', '.join(ROLE_TAGS)}")
    tag_ids = role_tag_ids(db)
    gaps = {r: {"have": st["roles"][r]["count"], "guideline": guide.get(r)} for r in wanted}
    candidates: dict[str, list[dict]] = {}
    for role in wanted:
        if not tag_ids.get(role):
            candidates[role] = []
            continue
        not_in_identity = [c for c in COLORS if c not in ident]
        query = (select(OracleCard, OraclePrice, func.count(OracleTagLink.tag_id))
                 .join(OracleTagLink, OracleTagLink.oracle_id == OracleCard.oracle_id)
                 .outerjoin(OraclePrice, OraclePrice.oracle_id == OracleCard.oracle_id)
                 .where(OracleTagLink.tag_id.in_(list(tag_ids[role])), OracleCard.digital.is_(False),
                        OracleCard.legalities[fmt].astext.in_(LEGAL),
                        # within the budget, or already in your collection (free, whatever its price)
                        or_(and_(OraclePrice.usd.is_not(None), OraclePrice.usd <= budget_usd),
                            OracleCard.oracle_id.in_(list(owned)) if owned else false()),
                        OracleCard.oracle_id.not_in(in_deck or [""]),
                        *[~OracleCard.color_identity.contains([c]) for c in not_in_identity])
                 .group_by(OracleCard.oracle_id, OraclePrice.oracle_id)
                 .order_by(*([OracleCard.oracle_id.in_(list(owned)).desc()] if owned else []),
                           OracleCard.edhrec_rank.asc().nulls_last(), OraclePrice.usd, OracleCard.name).limit(limit))
        candidates[role] = [{"name": c.name, "oracle_id": c.oracle_id, "type_line": c.type_line, "mana_cost": front_mana_cost(c),
                             "edhrec_rank": c.edhrec_rank, **_price_fields(p),
                             **({"owned_copies": owned.get(c.oracle_id, 0)} if owned is not None else {}),
                             "why": f"tagged {role} by Scryfall Tagger; legal in {fmt}; within the deck's colors; "
                                    + ("already in your collection" if owned and c.oracle_id in owned else "priced within the budget")}
                            for c, p, _ in db.execute(query).all()]
    oids = [e.card.oracle_id for e in played if e.card and not is_land(e.card)]
    roles_map = roles_of(db, oids)
    cut_prices = prices_of(db, oids)
    cuts = sorted(({"name": e.card.name, "edhrec_rank": e.card.edhrec_rank, "quantity": e.line.quantity,
                    **_price_fields(cut_prices.get(e.card.oracle_id))}  # what the swap saves: the price delta is shown against it
                   for e in played if e.card and not is_land(e.card) and e.line.section == "main" and not roles_map.get(e.card.oracle_id)),
                  key=lambda c: (c["edhrec_rank"] is not None, -(c["edhrec_rank"] or 0)))[:limit]
    return {"format": fmt, "budget_usd": budget_usd, "color_identity": sorted(ident, key=COLORS.index), "gaps": gaps,
            "deck_cost_usd": st["estimated_cost_usd"],  # the deck's estimated cost now, to compare a plan's change with
            "candidates": candidates, "cut_candidates": cuts,
            "notes": ["Candidates are ordered by popularity (EDHREC rank, lower is more played). Popularity is not power or fit.",
                      "Role guidelines (ramp 10, draw 10, removal 8, sweepers 2 for 100-card decks) are a common community habit, not a rule.",
                      "Cut candidates are the deck's least-played cards without a role tag: a starting point, not a verdict.",
                      "Call validate_deck_changes with the final cuts and adds before presenting them as a plan."]}


# -- validator ----------------------------------------------------------------------------------

def card_names(items: list[str]) -> list[str]:
    """The cards a list of cuts or adds names, one entry per copy: ``Mind Stone``, ``1 Mind Stone``, ``2x Mind Stone`` and
    ``Sol Ring (C21) 263`` are all read as a decklist line is (a quantity repeats the card, a set and number are not part of
    the name). A string that is not one line stays as it was written, so it fails as an unknown card, not silently (#342)."""
    out: list[str] = []
    for item in items:
        try:
            lines = deck_text.parse_text(item if re.match(r"\s*\d+\s*x?\s", item, re.I) else f"1 {item}").lines
        except (ValueError, OverflowError):
            lines = []
        if len(lines) == 1 and 0 < lines[0].quantity <= 100 and lines[0].name.strip():
            out += [lines[0].name.strip()] * lines[0].quantity
        else:
            out.append(item.strip())
    return out


def validate_changes(db: Session, text: str, fmt: str, adds: list[str], cuts: list[str], budget_usd: float | None) -> dict:
    """Apply cuts and adds to a deck and check the result: every named card exists and is in the
    deck (cuts), is legal and in the deck's colors (adds), the resulting deck is still legal, and
    the adds' total price is within budget. A card with no price makes the budget unverifiable, so
    it is reported as an issue rather than assumed free."""
    fmt = check_format(fmt)
    adds, cuts = card_names(adds), card_names(cuts)
    deck = parse(text)
    before = resolve(db, deck)
    played = before.played()
    present = Counter()
    for e in played:
        present[e.name.lower()] += e.line.quantity
    issues: list[dict] = []
    cut_names = []
    for name in cuts:
        key = name.strip().lower()
        match = next((n for n in present if n == key or n.split(" // ")[0] == key), None)
        if match is None or present[match] <= 0:
            issues.append({"kind": "cut_not_in_deck", "card": name, "detail": "not in the deck (or already cut)"})
        else:
            present[match] -= 1
            cut_names.append(match)
    after_lines = []
    removed = Counter(cut_names)
    for l in deck.lines:
        l2 = decklist.DeckLine(l.quantity, l.name, l.set_code, l.collector_number, l.finish, l.section, l.categories)
        key = l.name.strip().lower()
        if l.section in PLAYED:
            hit = next((n for n in removed if removed[n] and (n == key or n.split(" // ")[0] == key)), None)
            if hit:
                take = min(removed[hit], l2.quantity)
                l2.quantity -= take
                removed[hit] -= take
        if l2.quantity > 0:
            after_lines.append(l2)
    add_lines = [decklist.DeckLine(1, n.strip()) for n in adds]
    resolved_adds = resolve(db, decklist.Decklist(add_lines))
    prices = prices_of(db, [e.card.oracle_id for e in resolved_adds.entries if e.card])
    base_ident = set(identity([e for e in resolve(db, decklist.Decklist(deck.section("commander"))).entries if e.card])) if fmt in COMMANDER_STYLE and deck.section("commander") \
        else set(identity([e for e in played if e.card]))
    total, unpriced = 0.0, []
    for e in resolved_adds.entries:
        if e.card is None:
            issues.append({"kind": "unknown_card", "card": e.line.name, "detail": "not found in the card catalog"})
            continue
        if e.card.legalities.get(fmt) not in LEGAL:
            issues.append({"kind": "not_legal", "card": e.card.name, "detail": f"{e.card.legalities.get(fmt, 'unknown')} in {fmt}"})
        if not set(e.card.color_identity or []) <= base_ident and fmt in COMMANDER_STYLE:
            issues.append({"kind": "color_identity", "card": e.card.name, "detail": "outside the deck's color identity"})
        price = prices.get(e.card.oracle_id)
        if price is None or price.usd is None:
            unpriced.append(e.card.name)
        else:
            total += price.usd
    for name in unpriced:
        issues.append({"kind": "no_price", "card": name, "detail": "no price is known, so the budget cannot be verified"})
    by_name = {}
    for e in played:
        if e.card:
            by_name[e.name.lower()] = e.card
    cut_cards = [by_name[n] for n in cut_names if n in by_name]
    cut_prices = prices_of(db, [c.oracle_id for c in cut_cards])
    cut_value = round(sum(cut_prices[c.oracle_id].usd for c in cut_cards if c.oracle_id in cut_prices and cut_prices[c.oracle_id].usd is not None), 2)
    cut_unpriced = [c.name for c in cut_cards if cut_prices.get(c.oracle_id) is None or cut_prices[c.oracle_id].usd is None]
    deck_oids = list({e.card.oracle_id for e in played if e.card})
    deck_prices = prices_of(db, deck_oids)
    cost_before = round(sum((deck_prices[e.card.oracle_id].usd or 0) * e.line.quantity for e in played if e.card and e.card.oracle_id in deck_prices), 2)
    if budget_usd is not None and total > budget_usd + 1e-9:
        issues.append({"kind": "over_budget", "card": None, "detail": f"adds cost ${total:.2f}, budget is ${budget_usd:.2f}"})
    result = Resolved(resolve(db, decklist.Decklist(after_lines)).entries + [Entry(l, e.card) for l, e in zip(add_lines, resolved_adds.entries)])
    outcome = legality(result, fmt)
    # Problems the deck already had are reported apart: they do not make *this plan* invalid (found with a real deck
    # that had one off-color card: every plan was rejected for it). A problem the plan creates or leaves unsolved
    # where it matters (size, copies) still counts.
    had = {(i["kind"], i["card"]) for i in legality(before, fmt)["issues"]}
    existing = [i for i in outcome["issues"] if (i["kind"], i["card"]) in had and i["kind"] in ("unknown_card", "not_legal", "color_identity")]
    introduced = [i for i in outcome["issues"] if i not in existing]
    issues += [{**i, "kind": "result_" + i["kind"]} for i in introduced
               if i["kind"] not in ("unknown_card", "not_legal", "color_identity") or i["card"] not in {x.get("card") for x in issues}]
    return {"valid": not issues, "issues": issues, "existing_issues": existing, "format": fmt, "adds": len(adds), "cuts": len(cut_names),
            "cards_after": outcome["cards_checked"], "added_cost_usd": round(total, 2), "budget_usd": budget_usd,
            "cuts_not_refunded": True,
            # What the plan does to the deck's price: the adds, the cuts (each with its own price) and the deck as a whole.
            # The budget above still counts only the adds; these figures are for comparing, not for the budget check.
            "added_cards": [{"name": e.card.name, "price_usd": plausible_price(prices[e.card.oracle_id].usd) if e.card.oracle_id in prices else None}
                            for e in resolved_adds.entries if e.card],
            "cut_cards": [{"name": c.name, "price_usd": plausible_price(cut_prices[c.oracle_id].usd) if c.oracle_id in cut_prices else None} for c in cut_cards],
            "cut_value_usd": cut_value, "cut_unpriced": cut_unpriced, "net_change_usd": round(total - cut_value, 2),
            "deck_cost_before_usd": cost_before, "deck_cost_after_usd": round(cost_before - cut_value + total, 2),
            "not_checked": outcome["not_checked"]}
