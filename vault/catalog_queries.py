"""Reading the catalog: cards, rulings, rules, citations, and the data versions behind them.

Everything returned here is Wizards' or Scryfall's material (or Scryfall Tagger opinion). Callers
attach provenance (``vault.provenance``). Results are capped: the catalog is looked up by the
card or rule asked about, never dumped (docs/compliance.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import provenance as prov
from .models import CatalogSource, OracleCard, OraclePrice, OracleTag, OracleTagLink, Rule, Ruling, RulesVersion

MAX_RULINGS = 25
MAX_RULE_RESULTS = 10
MAX_SUBRULES = 40
SUGGESTIONS = 5
SIMILARITY = 0.35  # pg_trgm: how close a misspelled name must be to be offered


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


NON_PLAYABLE_LAYOUTS = {"token", "double_faced_token", "emblem", "vanguard", "scheme", "planar", "augment", "host", "art_series",
                        "reversible_card"}


def card_priority(card: OracleCard) -> tuple:
    """Which of several cards with the same name a person means: the playable paper card, never its token
    (found with real data: 'Llanowar Elves' is also a token, which is not legal anywhere), then the most played."""
    return (card.layout in NON_PLAYABLE_LAYOUTS, bool(card.digital), card.edhrec_rank is None, card.edhrec_rank or 0, card.oracle_id)


def best_card(cards: list[OracleCard]) -> OracleCard | None:
    return min(cards, key=card_priority) if cards else None


def find_card(db: Session, *, name: str | None = None, oracle_id: str | None = None) -> tuple[OracleCard | None, list[OracleCard]]:
    """The card for an exact name (front or back face of a double-faced card) or an Oracle id, or
    close names to offer instead. Never guesses: a misspelling returns suggestions, not a card."""
    if oracle_id:
        return db.get(OracleCard, oracle_id), []
    if not name or not name.strip():
        return None, []
    wanted = _norm_name(name)
    exact = best_card(db.scalars(select(OracleCard).where(func.lower(OracleCard.name) == wanted)).all())
    if exact is None:  # "Fire" for "Fire // Ice"
        exact = best_card(db.scalars(select(OracleCard).where(
            func.lower(OracleCard.name).like(wanted.replace("%", r"\%").replace("_", r"\_") + " // %", escape="\\")
            | func.lower(OracleCard.name).like("% // " + wanted.replace("%", r"\%").replace("_", r"\_"), escape="\\")
        )).all())
    if exact is not None:
        return exact, []
    db.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :s, true)"), {"s": str(SIMILARITY)})  # this transaction only
    similar = db.scalars(select(OracleCard).where(OracleCard.name.op("%")(name.strip()))
                         .order_by(func.similarity(OracleCard.name, name.strip()).desc(), OracleCard.name)
                         .limit(SUGGESTIONS)).all()
    return None, list(similar)


def card_body(card: OracleCard) -> dict:
    return {
        "oracle_id": card.oracle_id, "name": card.name, "mana_cost": card.mana_cost, "cmc": card.cmc,
        "type_line": card.type_line, "oracle_text": card.oracle_text, "power": card.power, "toughness": card.toughness,
        "loyalty": card.loyalty, "defense": card.defense, "colors": card.colors, "color_identity": card.color_identity,
        "keywords": card.keywords, "produced_mana": card.produced_mana, "legalities": card.legalities,
        "faces": card.faces, "layout": card.layout, "scryfall_uri": card.scryfall_uri,
        "artist": card.artist, "image_normal": card.image_normal,
    }


def rulings_for(db: Session, oracle_id: str, limit: int = MAX_RULINGS) -> tuple[list[dict], int]:
    """Rulings for one card, newest first, capped. Returns (rulings, total)."""
    limit = max(1, min(limit, MAX_RULINGS))
    total = db.scalar(select(func.count()).select_from(Ruling).where(Ruling.oracle_id == oracle_id)) or 0
    rows = db.scalars(select(Ruling).where(Ruling.oracle_id == oracle_id)
                      .order_by(Ruling.published_at.desc().nulls_last(), Ruling.id).limit(limit)).all()
    return [{"published_at": r.published_at.isoformat() if r.published_at else None, "source": r.source,
             "comment": r.comment} for r in rows], total


def card_tags(db: Session, oracle_id: str) -> list[dict]:
    """The curated Tagger tags a card carries, each with its weight: Scryfall's community opinion."""
    rows = db.execute(select(OracleTag.slug, OracleTag.label, OracleTagLink.weight)
                      .join(OracleTagLink, OracleTagLink.tag_id == OracleTag.id)
                      .where(OracleTagLink.oracle_id == oracle_id).order_by(OracleTag.slug)).all()
    return [{"tag": slug, "label": label, "weight": weight} for slug, label, weight in rows]


# -- rules --------------------------------------------------------------------------------------

def latest_rules_version(db: Session) -> str | None:
    return db.scalar(select(func.max(RulesVersion.version)))


def rule_body(rule: Rule) -> dict:
    return {"number": rule.number, "text": rule.text, "kind": rule.kind, "parent": rule.parent}


def get_rule(db: Session, number: str, version: str | None = None) -> tuple[dict | None, list[dict], str | None]:
    """One rule (or glossary term as ``glossary:Term``) with its direct subrules, capped."""
    version = version or latest_rules_version(db)
    if version is None:
        return None, [], None
    rule = db.get(Rule, (version, number))
    if rule is None and not number.startswith("glossary:"):  # glossary terms are looked up case-insensitively
        return None, [], version
    if rule is None:
        rule = db.scalars(select(Rule).where(Rule.version == version, func.lower(Rule.number) == number.lower())).first()
        if rule is None:
            return None, [], version
    children = db.scalars(select(Rule).where(Rule.version == version, Rule.parent == rule.number)
                          .order_by(Rule.number).limit(MAX_SUBRULES)).all()
    return rule_body(rule), [rule_body(c) for c in children], version


def search_rules(db: Session, query: str, version: str | None = None, limit: int = MAX_RULE_RESULTS) -> tuple[list[dict], str | None, str]:
    """Full-text search of the rules, best matches first, capped. Headings are not returned.

    A plain-language question rarely has every word in one rule ("protection from red damage prevented"),
    so when no rule has all the words the search falls back to rules with any of them, best first. The
    third value says which happened: ``"all words"`` or ``"any word"``."""
    version = version or latest_rules_version(db)
    if version is None or not query.strip():
        return [], version, "all words"
    limit = max(1, min(limit, MAX_RULE_RESULTS))
    vector = func.to_tsvector("english", Rule.text)

    def run(tsq):
        return db.scalars(select(Rule).where(Rule.version == version, Rule.kind != "heading", vector.op("@@")(tsq))
                          .order_by(func.ts_rank(vector, tsq).desc(), Rule.number).limit(limit)).all()

    rows = run(func.websearch_to_tsquery("english", query))
    mode = "all words"
    words = list(dict.fromkeys(re.findall(r"[A-Za-z0-9]{2,}", query.lower())))[:12]
    if not rows and len(words) > 1:
        rows = run(func.to_tsquery("english", " | ".join(words)))
        mode = "any word"
    return [rule_body(r) for r in rows], version, mode


# -- citations ----------------------------------------------------------------------------------

def _squash(s: str) -> str:
    """Whitespace and typographic quotes made comparable; nothing else is forgiven."""
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def verify_citation(db: Session, kind: str, ref: str, quote: str, version: str | None = None) -> dict:
    """Is ``quote`` verbatim in the named source? ``kind``: rule (ref: number or glossary:Term),
    oracle_text (ref: card name or Oracle id) or ruling (ref: card name or Oracle id).
    The answer includes the true text when the quote does not match, so the caller can correct it."""
    wanted = _squash(quote)
    if not wanted:
        return {"verified": False, "reason": "empty quote"}
    if kind == "rule":
        rule, _, version = get_rule(db, ref, version)
        if rule is None:
            return {"verified": False, "reason": "no such rule", "version": version}
        ok = wanted in _squash(rule["text"])
        return {"verified": ok, "version": version, "source_text": None if ok else rule["text"], "number": rule["number"]}
    card, suggestions = find_card(db, **({"oracle_id": ref} if re.fullmatch(r"[0-9a-f-]{36}", ref) else {"name": ref}))
    if card is None:
        return {"verified": False, "reason": "no such card", "suggestions": [c.name for c in suggestions]}
    if kind == "oracle_text":
        ok = wanted in _squash(card.oracle_text or "")
        return {"verified": ok, "card": card.name, "source_text": None if ok else card.oracle_text}
    rulings, _ = rulings_for(db, card.oracle_id)
    hit = next((r for r in rulings if wanted in _squash(r["comment"])), None)
    return {"verified": hit is not None, "card": card.name, "ruling": hit,
            "source_text": None if hit else [r["comment"] for r in rulings[:5]]}


# -- versions -----------------------------------------------------------------------------------

def sources(db: Session) -> dict[str, CatalogSource]:
    return {s.name: s for s in db.scalars(select(CatalogSource))}


def provenance_for(db: Session, *names: str) -> list[prov.Provenance]:
    rows = sources(db)
    return [prov.for_catalog(n, rows.get(n)) for n in names]
