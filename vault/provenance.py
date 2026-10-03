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


class Provenance(BaseModel):
    kind: Literal["source", "computed"] = Field(description="'source': third-party material, theirs. 'computed': derived by the Vault from `inputs`.")
    source: str = Field(description="Who published it, e.g. 'Scryfall', 'Scryfall Tagger', 'Wizards of the Coast (Comprehensive Rules)', or 'The Vault' for computed values.")
    origin: str | None = Field(default=None, description="Who wrote it when that differs from who published it, e.g. 'Wizards of the Coast (rulings)'.")
    url: str | None = Field(default=None, description="Link to the original, for people to check.")
    as_of: str | None = Field(default=None, description="Date the data was fetched or valid.")
    version: str | None = Field(default=None, description="Data version, e.g. the bulk file stamp or the Comprehensive Rules date.")
    notice: str | None = Field(default=None, description="Notice to repeat when showing this to people.")
    inputs: list[Provenance] | None = Field(default=None, description="For computed values: the sources and versions used.")


def _day(value: date | datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()


def source(name: str, *, origin: str | None = None, url: str | None = None,
           as_of: date | datetime | str | None = None, version: str | None = None,
           wizards_material: bool = False) -> Provenance:
    """Third-party material. ``wizards_material`` adds the Fan Content notice (rules, rulings, card text)."""
    return Provenance(kind="source", source=name, origin=origin, url=url, as_of=_day(as_of), version=version,
                      notice=FAN_CONTENT_NOTICE if wizards_material else None)


def computed(what: str, inputs: list[Provenance], *, as_of: date | datetime | str | None = None) -> Provenance:
    """Something the Vault derived (a count, a legality check, a budget). ``what`` says what it is."""
    return Provenance(kind="computed", source="The Vault", origin=what, as_of=_day(as_of), inputs=inputs,
                      notice=FAN_CONTENT_NOTICE if any(i.notice for i in inputs) else None)


# Where each catalog source comes from. Keys are ``catalog_sources.name``.
CATALOG_SOURCES = {
    "oracle_cards": dict(name="Scryfall", origin="Wizards of the Coast (card text)", url="https://scryfall.com/docs/api/bulk-data", wizards_material=True),
    "rulings": dict(name="Scryfall", origin="Wizards of the Coast (rulings)", url="https://scryfall.com/docs/api/rulings", wizards_material=True),
    "oracle_tags": dict(name="Scryfall Tagger", origin="community tags, opinions rather than rules", url="https://tagger.scryfall.com/", wizards_material=False),
    "oracle_prices": dict(name="Scryfall", origin="TCGplayer and Cardmarket (prices)", url="https://scryfall.com/docs/api/cards", wizards_material=False),
    "rules": dict(name="Wizards of the Coast", origin="Magic: The Gathering Comprehensive Rules", url="https://magic.wizards.com/en/rules", wizards_material=True),
}


def for_catalog(name: str, row=None) -> Provenance:
    """The provenance of one catalog source, filled from its ``catalog_sources`` row when given."""
    info = dict(CATALOG_SOURCES[name])
    source_name = info.pop("name")
    return source(source_name, as_of=getattr(row, "fetched_at", None), version=getattr(row, "version", None), **info)
