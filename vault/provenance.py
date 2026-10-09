"""Provenance: where a piece of data came from, shown with every answer.

Two rules (docs/compliance.md): always show provenance, and never make anything look like the
Vault's own. A block is either a ``source`` (third-party material, shown as theirs) or
``computed`` (the Vault derived it, with the sources and versions it used as ``inputs``). The two
are never mixed in one field. Every MCP tool result that carries third-party data includes one.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

FAN_CONTENT_NOTICE = (
    "The Vault is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed "
    "by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC."
)
NOT_ENDORSED = "The Vault is not produced by or endorsed by Scryfall."
# 17Lands' public data sets are CC BY 4.0: the notice names the creator, the licence and its link, the warranty disclaimer and what the
# Vault changed (docs/limited-data-design.md section 6). The answers that carry set figures use ``limited_stats.attribution`` (the same
# text with the set, format and dates filled in).
GENERIC_17LANDS_NOTICE = (
    "Data from 17Lands (https://www.17lands.com/public_datasets), licensed under CC BY 4.0 "
    "(https://creativecommons.org/licenses/by/4.0/), which also states that it is provided without warranty (section 5). "
    "Magic Arena games and drafts. Changed by the Vault: the per-game and per-pick rows were reduced to per-card counts, and the "
    "percentages, intervals and sample-size warnings were computed by the Vault, so they can differ from the figures on 17lands.com. "
    "Not produced or endorsed by 17Lands.")


class Licence(BaseModel):
    name: str = Field(description="The licence the material is shared under, e.g. 'CC BY 4.0'.")
    url: str = Field(description="The licence's own page.")


class Provenance(BaseModel):
    kind: Literal["source", "computed"] = Field(description="'source': third-party material, theirs. 'computed': derived by the Vault from `inputs`.")
    source: str = Field(description="Who published it, e.g. 'Scryfall', 'Scryfall Tagger', 'Wizards of the Coast (Comprehensive Rules)', or 'The Vault' for computed values.")
    origin: str | None = Field(default=None, description="Who wrote it when that differs from who published it, e.g. 'Wizards of the Coast (rulings)'.")
    url: str | None = Field(default=None, description="Link to the original, for people to check.")
    as_of: str | None = Field(default=None, description="Date the data was fetched or valid.")
    version: str | None = Field(default=None, description="Data version, e.g. the bulk file stamp or the Comprehensive Rules date.")
    notice: str | None = Field(default=None, description="Notice to repeat when showing this to people.")
    licence: Licence | None = Field(default=None, description="For material shared under a licence (e.g. 17Lands, CC BY 4.0): its name and link.")
    changes: str | None = Field(default=None, description="What the Vault changed in the source material before showing it (the licence asks that this is said).")
    inputs: list[Provenance] | None = Field(default=None, description="For computed values: the sources and versions used.")


def _day(value: date | datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()


def source(name: str, *, origin: str | None = None, url: str | None = None,
           as_of: date | datetime | str | None = None, version: str | None = None,
           wizards_material: bool = False, notice: str | None = None, licence: Licence | dict | None = None,
           changes: str | None = None) -> Provenance:
    """Third-party material. ``wizards_material`` adds the Fan Content notice (rules, rulings, card text); ``notice`` is the
    source's own (17Lands' attribution), ``licence`` and ``changes`` say under which licence it is shared and what the Vault did to it."""
    return Provenance(kind="source", source=name, origin=origin, url=url, as_of=_day(as_of), version=version,
                      notice=notice or (FAN_CONTENT_NOTICE if wizards_material else None),
                      licence=Licence(**licence) if isinstance(licence, dict) else licence, changes=changes)


def computed(what: str, inputs: list[Provenance], *, as_of: date | datetime | str | None = None) -> Provenance:
    """Something the Vault derived (a count, a legality check, a budget). ``what`` says what it is."""
    return Provenance(kind="computed", source="The Vault", origin=what, as_of=_day(as_of), inputs=inputs,
                      notice=FAN_CONTENT_NOTICE if any(i.notice == FAN_CONTENT_NOTICE for i in inputs) else None)


# Where each catalog source comes from. Keys are ``catalog_sources.name``.
CATALOG_SOURCES = {
    "oracle_cards": dict(name="Scryfall", origin="Wizards of the Coast (card text)", url="https://scryfall.com/docs/api/bulk-data", wizards_material=True),
    "rulings": dict(name="Scryfall", origin="Wizards of the Coast (rulings)", url="https://scryfall.com/docs/api/rulings", wizards_material=True),
    "oracle_tags": dict(name="Scryfall Tagger", origin="community tags, opinions rather than rules", url="https://tagger.scryfall.com/", wizards_material=False),
    "oracle_prices": dict(name="Scryfall", origin="TCGplayer and Cardmarket (prices)", url="https://scryfall.com/docs/api/cards", wizards_material=False),
    "oracle_printings": dict(name="Scryfall", origin="TCGplayer and Cardmarket (prices of each printing)", url="https://scryfall.com/docs/api/cards", wizards_material=False),
    "limited_17lands": dict(name="17Lands", origin="17Lands users' Magic Arena games and drafts (public data sets)",
                            url="https://www.17lands.com/public_datasets", wizards_material=False,
                            licence={"name": "CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
                            changes="Reduced to per-card counts; percentages, intervals and sample-size warnings computed by the Vault.",
                            notice=GENERIC_17LANDS_NOTICE),
    "rules": dict(name="Wizards of the Coast", origin="Magic: The Gathering Comprehensive Rules", url="https://magic.wizards.com/en/rules", wizards_material=True),
}


def for_catalog(name: str, row=None) -> Provenance:
    """The provenance of one catalog source, filled from its ``catalog_sources`` row when given."""
    info = dict(CATALOG_SOURCES[name])
    source_name = info.pop("name")
    return source(source_name, as_of=getattr(row, "fetched_at", None), version=getattr(row, "version", None), **info)
