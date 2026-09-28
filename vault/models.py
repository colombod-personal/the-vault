"""Tables.

Per user (tenant data, private unless shared): ``users``, ``identities`` (one per
linked OAuth account), ``imports`` (each uploaded CSV with its change summary),
``entries`` (the collection, one row per source-file row so exports round-trip),
``decks`` and ``collection_values``. ``shares`` records access a user has granted
to someone else. :func:`vault.privacy.purge_user` removes all of it.

Shared: ``cards`` (Scryfall data for printings someone owns), ``price_snapshots``
(one row per printing per day, written by the daily sync) and
``collection_values`` (each user's daily total, which powers the value chart).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from mtg_toolkits.models import CollectionEntry, Condition, Finish
from sqlalchemy import JSON, Boolean, Index, LargeBinary, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # Finish to price with when the matched printing exists in only one finish (e.g. Dragon Shield
    # leaves Printing blank on etched-only cards). Kept apart from `finish` so the imported data,
    # its delta keys and the CSV export stay exactly as imported.
    price_finish: Mapped[str | None] = mapped_column(String(10))

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


class Deck(Base):
    """A saved decklist (plain text, any common format)."""

    __tablename__ = "decks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Share(Base):
    """Read access one user grants another, to their collection or to one deck.

    Created as a pending invite (only a SHA-256 of the one-time token is stored);
    accepting binds it to the recipient's account. The owner can revoke it and the
    recipient can leave at any time.
    """

    __tablename__ = "shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # "collection" | "deck"
    deck_id: Mapped[int | None] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"))
    grantee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    show_costs: Mapped[bool] = mapped_column(Boolean, default=False)  # reveal prices paid?
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiSession(Base):
    """A signed-in app (e.g. the iOS app): bearer access token + rotating refresh token.

    Only SHA-256 hashes of the tokens are stored. Presenting an already-rotated refresh
    token revokes the session (refresh-token reuse detection).
    """

    __tablename__ = "api_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client: Mapped[str] = mapped_column(String(40))  # e.g. "ios"
    device_name: Mapped[str | None] = mapped_column(String(120))
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    access_expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    previous_refresh_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    refresh_expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthCode(Base):
    """One-time code handing a browser sign-in over to a native app (PKCE, ~2 minutes)."""

    __tablename__ = "auth_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(String(300))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Passkey(Base):
    """A WebAuthn credential (passkey: Face ID, Touch ID, Windows Hello, a phone or a security key).

    The account's WebAuthn user handle is its ``Identity(provider="passkey")`` subject, so
    passkeys are one more sign-in method, linked like the OAuth providers. Only the public key
    is stored.
    """

    __tablename__ = "passkeys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(String(1400), unique=True)  # base64url
    public_key: Mapped[bytes] = mapped_column(LargeBinary)  # COSE
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[list] = mapped_column(JSON, default=list)
    name: Mapped[str] = mapped_column(String(80))
    aaguid: Mapped[str | None] = mapped_column(String(36))
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)  # synced passkey
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccessToken(Base):
    """A personal access token: how a user's own agents, scripts and MCP clients call the API.

    Scoped ("read", or "read write"), named, revocable and expiring. Only a SHA-256 hash is
    stored; the token is shown once, when created.
    """

    __tablename__ = "access_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(20))  # shown in lists, e.g. "vault_pat_Ab3x"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(String(40))  # space-separated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotentRequest(Base):
    """The stored answer to a POST sent with an ``Idempotency-Key``, so a retry after a lost
    response returns the same answer instead of doing the work twice. Kept 24 hours."""

    __tablename__ = "idempotent_requests"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    endpoint: Mapped[str] = mapped_column(String(120))
    status: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Card(Base):
    """Scryfall printing data for printings someone owns (refreshed by the daily sync)."""

    __tablename__ = "cards"
    __table_args__ = (Index("ix_cards_set_number", "set_code", "collector_number"),)  # import-time matching

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
    artist: Mapped[str | None] = mapped_column(String(200))  # credited wherever the image is shown
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
