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
from .models import CatalogSource, OracleCard, OraclePrice, OracleTag, OracleTagLink, Ruling

MAX_RULINGS = 25
MAX_RULE_RESULTS = 10
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
    # kind "rule" is checked against the live rules (vault.rules_live) by the API
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
