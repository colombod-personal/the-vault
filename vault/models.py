"""Tables.

Per user: ``users``, ``identities`` (one per linked OAuth account), ``imports``
(each uploaded CSV with its change summary) and ``entries`` (the collection,
one row per source-file row so exports round-trip).

Shared: ``cards`` (Scryfall data for printings someone owns), ``price_snapshots``
(one row per printing per day, written by the daily sync) and
``collection_values`` (each user's daily total, which powers the value chart).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from mtg_toolkits.models import CollectionEntry, Condition, Finish
from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    identities: Mapped[list[Identity]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Identity(Base):
    """A sign-in method linked to a user. Accounts are never merged by e-mail."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # google | microsoft | apple | facebook | dev
    subject: Mapped[str] = mapped_column(String(255))  # provider's stable user id
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="identities")


class Import(Base):
    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(30), default="dragonshield")
    rows: Mapped[int] = mapped_column(Integer)
    copies: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)  # delta.CollectionDiff.summary()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Entry(Base):
    """One collection row, as imported."""

    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("imports.id", ondelete="SET NULL"))
    position: Mapped[int] = mapped_column(Integer, default=0)  # row order in the source file

    name: Mapped[str] = mapped_column(String(300))
    set_code: Mapped[str | None] = mapped_column(String(20))
    set_name: Mapped[str | None] = mapped_column(String(200))
    collector_number: Mapped[str | None] = mapped_column(String(30))
    finish: Mapped[str] = mapped_column(String(10), default=Finish.NONFOIL.value)
    condition: Mapped[str] = mapped_column(String(20), default=Condition.NEAR_MINT.value)
    language: Mapped[str] = mapped_column(String(5), default="en")
    folder: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    trade_quantity: Mapped[int] = mapped_column(Integer, default=0)
    purchase_price: Mapped[float | None] = mapped_column(Float)  # per copy
    purchase_date: Mapped[date | None] = mapped_column(Date)
    source_prices: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. Dragon Shield low/mid/market
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # lossless round-trip data (raw Printing, ...)

    scryfall_id: Mapped[str | None] = mapped_column(String(36), index=True)
    match_method: Mapped[str | None] = mapped_column(String(12))  # set_number | name_set | name | id

    def to_collection_entry(self) -> CollectionEntry:
        return CollectionEntry(
            name=self.name, quantity=self.quantity, set_code=self.set_code, set_name=self.set_name,
            collector_number=self.collector_number, finish=Finish(self.finish),
            condition=Condition(self.condition), language=self.language, folder=self.folder,
            trade_quantity=self.trade_quantity, purchase_price=self.purchase_price,
            purchase_date=self.purchase_date, scryfall_id=self.scryfall_id,
            source_prices=dict(self.source_prices or {}), extra=dict(self.extra or {}),
        )

    @classmethod
    def from_collection_entry(cls, e: CollectionEntry, **kwargs) -> Entry:
        return cls(
            name=e.name, quantity=e.quantity, set_code=e.set_code, set_name=e.set_name,
            collector_number=e.collector_number, finish=e.finish.value, condition=e.condition.value,
            language=e.language, folder=e.folder, trade_quantity=e.trade_quantity,
            purchase_price=e.purchase_price, purchase_date=e.purchase_date, scryfall_id=e.scryfall_id,
            source_prices=dict(e.source_prices), extra=dict(e.extra), **kwargs,
        )


class Card(Base):
    """Scryfall printing data for printings someone owns (refreshed by the daily sync)."""

    __tablename__ = "cards"

    scryfall_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    oracle_id: Mapped[str | None] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(300))
    set_code: Mapped[str] = mapped_column(String(20))
    set_name: Mapped[str | None] = mapped_column(String(200))
    collector_number: Mapped[str] = mapped_column(String(30))
    rarity: Mapped[str | None] = mapped_column(String(20))
    type_line: Mapped[str | None] = mapped_column(String(300))
    mana_cost: Mapped[str | None] = mapped_column(String(100))
    cmc: Mapped[float | None] = mapped_column(Float)
    colors: Mapped[list] = mapped_column(JSON, default=list)
    color_identity: Mapped[list] = mapped_column(JSON, default=list)
    oracle_text: Mapped[str | None] = mapped_column(Text)
    finishes: Mapped[list] = mapped_column(JSON, default=list)
    image_small: Mapped[str | None] = mapped_column(String(500))
    image_normal: Mapped[str | None] = mapped_column(String(500))
    scryfall_uri: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    scryfall_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    usd: Mapped[float | None] = mapped_column(Float)
    usd_foil: Mapped[float | None] = mapped_column(Float)
    usd_etched: Mapped[float | None] = mapped_column(Float)
    eur: Mapped[float | None] = mapped_column(Float)
    eur_foil: Mapped[float | None] = mapped_column(Float)
    eur_etched: Mapped[float | None] = mapped_column(Float)

    def for_finish(self, finish: str, currency: str = "usd") -> float | None:
        suffix = {"nonfoil": "", "foil": "_foil", "etched": "_etched"}.get(finish, "")
        return getattr(self, currency + suffix)


class CollectionValue(Base):
    __tablename__ = "collection_values"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    market_usd: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float)
    copies: Mapped[int] = mapped_column(Integer)
    priced_copies: Mapped[int] = mapped_column(Integer)  # copies with a Scryfall price (rest use the file's)
