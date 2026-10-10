"""What a card does, as a short list of points, stored with the card and used everywhere (#435, docs/functional-equivalents.md section 13).

``vault.equivalents`` reads a card's Oracle text and type line with 26 written rules and names the jobs it does in a 22-role
vocabulary. This module is the rest of the road to the person:

- **Stored when the catalog loads.** :func:`stored` is the reading that ``vault.catalog_sync`` writes into ``oracle_cards.roles``
  (a GIN-indexed JSON object) together with ``roles_version``; both are part of the row's content hash, so a change of rule
  rewrites every row once and nothing is computed per request. A card whose row has not been read yet (``roles_version`` null,
  the window between a deploy and the next catalog load) is read on the fly by :func:`hits_of`, never shown as "no roles".
- **Points.** Each role has a short sentence a player recognises ("Doubles tokens", "Counters a spell"); :func:`view` adds the rule
  that found it and a "why" line naming that rule, what it matches and what it is known to get wrong.
- **One wording and one label everywhere** (:data:`LABEL`): the roles are the Vault's reading of the Oracle text, not an official
  classification and not Scryfall's tags. Scryfall's Tagger tags (community opinion) are a separate list, labelled as such.
- **Filters** over the person's own cards (:func:`owned_printings`, :func:`owned_counts`, :func:`owned_cards`) are single queries on
  the indexed column.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from types import SimpleNamespace

from sqlalchemy import ARRAY, String, Text, cast, column, func, select, true
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm import Session

from . import equivalents as eq
from . import provenance as prov
from .models import Card, Entry, OracleCard

LABEL = "The Vault's reading of the card text, not an official classification"
LABEL_LONG = (LABEL + ". The Vault's own rules read the Oracle text (Wizards of the Coast's, via Scryfall) and name what the card does; "
              "each role says which rule found it, and a rule can miss or overreach. No role comes from Scryfall's community tags or from an assistant.")
TAGGER_LABEL = "Scryfall Tagger tags: a community's opinion, kept apart from the Vault's reading"
CORE_NOTE = "core: the card exists to do this; on the side: it does this as a by-product of another job"
VERSION = eq.RULES_VERSION
MATCH = ("all", "any")

# One short sentence per role, and (for the two roles whose Treasure or tokens may happen once or again and again) a second one.
POINTS: dict[str, str] = {
    "mana-rock": "Taps for mana (a mana rock)",
    "mana-creature": "Taps for mana (a mana creature)",
    "land-ramp": "Puts extra lands onto the battlefield",
    "treasure": "Makes Treasure",
    "mana-multiplier": "Doubles the mana you make",
    "draw-once": "Draws cards",
    "draw-engine": "Draws cards again and again",
    "tutor": "Searches your library",
    "recursion": "Gets cards back from the graveyard",
    "reanimate": "Brings creatures back from the graveyard",
    "token-maker": "Makes tokens",
    "token-doubler": "Doubles tokens",
    "counter-doubler": "Doubles counters",
    "counterspell": "Counters a spell",
    "free-counterspell": "Counters a spell for free",
    "spot-removal": "Removes one permanent",
    "bounce": "Returns permanents to hand",
    "sweeper": "Wipes the board",
    "sacrifice-outlet": "Lets you sacrifice permanents",
    "protection": "Protects your permanents",
    "lifegain": "Gains life",
    "evasion": "Makes creatures hard to block",
}
REPEATING = {"treasure": "Makes Treasure again and again", "token-maker": "Makes tokens again and again"}
ONCE = {"treasure": "Makes Treasure once", "token-maker": "Makes tokens once"}
assert set(POINTS) == set(eq.ROLES), "every role has a point"


def point_of(slug: str, repeatable: bool | None = None) -> str:
    """The short sentence for a role; a Treasure or token maker says whether it repeats."""
    if repeatable is True and slug in REPEATING:
        return REPEATING[slug]
    if repeatable is False and slug in ONCE:
        return ONCE[slug]
    return POINTS[slug]


# -- reading a card -----------------------------------------------------------------------------------------------------------------

def reader(row) -> SimpleNamespace:
    """A card as the rules read it, from a catalog row or a dict with the same fields (``oracle_card_row``)."""
    get = row.get if isinstance(row, dict) else lambda k, d=None: getattr(row, k, d)
    return SimpleNamespace(name=get("name"), type_line=get("type_line"), oracle_text=get("oracle_text"), faces=get("faces"),
                           oracle_id=None, content_hash=None)


def stored(row) -> dict[str, dict]:
    """The reading as it is stored: ``{slug: {strength, rule, repeatable}}`` in vocabulary order. Raises when the text cannot be read
    (the catalog sync catches that, stores nothing for the card and reports it)."""
    return {slug: {"strength": h.strength, "rule": h.rule, "repeatable": h.repeatable} for slug, h in eq.derive(eq.read(reader(row))).items()}


def hits_of(card: OracleCard) -> dict[str, eq.Hit]:
    """The roles of a catalog card: the stored reading when the row was read by the current rules, else read now (not stored)."""
    if card.roles_version == VERSION:
        return {s: eq.Hit(s, e["strength"], e["rule"], e.get("repeatable")) for s, e in sorted((card.roles or {}).items(), key=lambda p: eq.ORDER.get(p[0], 99))
                if s in eq.ROLES and e.get("rule") in eq.BY_ID}
    return eq.roles_of(card)


def read_row(row) -> tuple[dict[str, dict], str | None]:
    """``(roles, why)``: the stored reading of a catalog row, or ``({}, why)`` when the rules cannot read its text. The catalog sync
    stores nothing for such a card and reports it by name, the way it reports a deck-building wording the legality check does not read."""
    try:
        return stored(row), None
    except Exception as exc:  # a rule that fails on a wording nobody wrote it for must not stop the load
        return {}, f"{type(exc).__name__}: {exc}"[:200]


# -- the wording a person and an assistant read ----------------------------------------------------------------------------------------

def view(hit: eq.Hit) -> dict:
    """One role as every surface shows it: the point, the plain name, how sure (core or on the side), the rule and why."""
    role, rule = eq.ROLES[hit.role], eq.BY_ID[hit.rule]
    return {"role": hit.role, "point": point_of(hit.role, hit.repeatable), "name": role.name, "means": role.means, "family": role.family,
            "strength": hit.strength, "basis": eq.BASIS, "rule": hit.rule, "repeatable": hit.repeatable,
            "why": f"Found by the Vault's rule {hit.rule}: it matches {rule.what}. Known to get wrong: {rule.wrong}."}


def views(hits: dict[str, eq.Hit]) -> list[dict]:
    return [view(h) for h in hits.values()]


def vocabulary() -> list[dict]:
    """The 22 roles in the order the Vault ranks them, each with the rules that find it (what each matches, what it gets wrong)."""
    return [{"role": r.slug, "point": POINTS[r.slug], "name": r.name, "means": r.means, "family": r.family,
             "rules": [{"rule": u.id, "matches": u.what, "known_to_get_wrong": u.wrong} for u in eq.RULES if u.role == r.slug]}
            for r in eq.VOCABULARY]


def check_roles(slugs: Iterable[str]) -> list[str]:
    """The role slugs a request names, in the Vault's order and without repeats; raises ``ValueError`` naming an unknown one."""
    wanted = list(dict.fromkeys(s.strip().lower() for s in slugs if s and s.strip()))
    unknown = [s for s in wanted if s not in eq.ROLES]
    if unknown:
        raise ValueError(f"unknown role {unknown[0]!r}: the roles are {', '.join(eq.ROLES)}")
    return sorted(wanted, key=eq.ORDER.get)


def computed_provenance(sources: dict, db_sources: Iterable[str] = ("oracle_cards",), *, as_json: bool = False) -> list:
    """The provenance block of every answer that carries roles: computed by the Vault, from Scryfall's Oracle text, by rules of a version."""
    inputs = [prov.for_catalog(n, sources.get(n)) for n in db_sources if n in sources]
    block = prov.computed(f"{LABEL}: the Vault's own rules (version {VERSION}) over the Oracle text", inputs, as_of=date.today())
    return [block.model_dump(mode="json")] if as_json else [block]


# -- over the person's own cards ------------------------------------------------------------------------------------------------------

def _has(slugs: list[str], match: str):
    if match not in MATCH:
        raise ValueError(f"match must be one of {', '.join(MATCH)}")
    wanted = cast(array(slugs), ARRAY(Text))  # the operators ?& and ?| take a text array
    return OracleCard.roles.has_all(wanted) if match == "all" else OracleCard.roles.has_any(wanted)


def owned_printings(db: Session, user_id: int, slugs: list[str], match: str = "all") -> set[str]:
    """The ids of the printings the person owns whose card has these roles: one query, narrowed by the GIN index."""
    if not slugs:
        return set()
    return set(db.scalars(
        select(Entry.scryfall_id).join(Card, Card.scryfall_id == Entry.scryfall_id).join(OracleCard, OracleCard.oracle_id == Card.oracle_id)
        .where(Entry.user_id == user_id, Entry.quantity > 0, OracleCard.roles_version == VERSION, _has(slugs, match)).distinct()))


def owned_counts(db: Session, user_id: int) -> dict[str, dict]:
    """Per role, how many different cards the person owns that have it (``cards``) and how many of those have it as a core job
    (``core``): one query over the person's cards. A card counts once, whatever its printings."""
    mine = (select(Card.oracle_id).join(Entry, Entry.scryfall_id == Card.scryfall_id)
            .where(Entry.user_id == user_id, Entry.quantity > 0, Card.oracle_id.is_not(None)).distinct().subquery())
    keys = func.jsonb_each(OracleCard.roles).table_valued(column("key", String), column("value", JSONB))
    rows = db.execute(
        select(keys.c.key, func.count(), func.count().filter(keys.c.value["strength"].astext == "core"))
        .select_from(OracleCard).join(mine, mine.c.oracle_id == OracleCard.oracle_id).join(keys, true())
        .where(OracleCard.roles_version == VERSION).group_by(keys.c.key)).all()
    return {slug: {"cards": int(n), "core": int(core)} for slug, n, core in rows}


def owned_cards(db: Session, user_id: int, slugs: list[str], match: str = "all") -> list[dict]:
    """The person's cards (one row per card, not per printing) that have these roles, with the copies they own."""
    copies = func.sum(Entry.quantity)
    rows = db.execute(
        select(OracleCard, copies).join(Card, Card.oracle_id == OracleCard.oracle_id).join(Entry, Entry.scryfall_id == Card.scryfall_id)
        .where(Entry.user_id == user_id, Entry.quantity > 0, OracleCard.roles_version == VERSION, _has(slugs, match))
        .group_by(OracleCard.oracle_id).order_by(OracleCard.name)).all()
    return [{"card": c.name, "oracle_id": c.oracle_id, "copies": int(n), "type_line": c.type_line, "mana_cost": c.mana_cost,
             "mana_value": c.cmc, "roles": views(hits_of(c)), "scryfall_uri": c.scryfall_uri} for c, n in rows]


# -- over a deck -------------------------------------------------------------------------------------------------------------------------

def deck_roles(resolved) -> dict:
    """"What this deck does": every one of the 22 roles with the cards of the deck that have it (a role the deck lacks is there, empty,
    so a gap is visible), and the cards the rules found no role for. ``resolved`` is a ``vault.deck_tools.Resolved``; the cards played
    (main deck, commander, companion) are read, each card once with its copies."""
    cards: dict[str, dict] = {}
    for e in resolved.played():
        if e.card is None:
            continue
        mine = cards.setdefault(e.card.oracle_id, {"card": e.card, "copies": 0})
        mine["copies"] += e.line.quantity
    by_role: dict[str, list[dict]] = {slug: [] for slug in eq.ROLES}
    without, lands = [], 0
    for oracle_id, mine in sorted(cards.items(), key=lambda p: p[1]["card"].name.lower()):
        card, copies = mine["card"], mine["copies"]
        hits = hits_of(card)
        for slug, hit in hits.items():
            by_role[slug].append({"card": card.name, "oracle_id": oracle_id, "copies": copies, "strength": hit.strength, "rule": hit.rule,
                                  "point": point_of(slug, hit.repeatable), "repeatable": hit.repeatable})
        if not hits:
            if "Land" in (card.type_line or "").split("//")[0]:
                lands += copies
            else:
                without.append({"card": card.name, "oracle_id": oracle_id, "copies": copies})
    roles = []
    for r in eq.VOCABULARY:
        found = by_role[r.slug]
        roles.append({"role": r.slug, "point": POINTS[r.slug], "name": r.name, "means": r.means, "family": r.family,
                      "cards": len(found), "copies": sum(c["copies"] for c in found), "core_cards": sum(1 for c in found if c["strength"] == "core"),
                      "empty": not found, "items": found})
    return {"label": LABEL, "roles_version": VERSION, "distinct_cards": len(cards), "roles": roles, "without_role": without,
            "lands_without_role": lands,
            "note": "Each card is counted once however many copies the deck holds; copies adds them up. A card can have several roles. "
                    "A role with no cards is shown empty so a gap is visible: the Vault reads 22 common jobs and misses some wordings, so "
                    "an empty role means its rules found nothing, not that the deck cannot do it."}
