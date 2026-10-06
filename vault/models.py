"""Tables.

Per user (tenant data, private unless shared): ``users``, ``identities`` (one per
linked sign-in method (OAuth provider or passkey)), ``imports`` (each uploaded CSV with its change summary),
``entries`` (the collection, one row per source-file row so exports round-trip),
``decks`` and ``collection_values``. ``shares`` records access a user has granted
to someone else. :func:`vault.privacy.purge_user` removes all of it.

Shared: ``cards`` (Scryfall data for printings someone owns) and ``price_snapshots``
(one row per printing per day, written by the daily sync).
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

from mtg_toolkits.models import CollectionEntry, Condition, Finish
from sqlalchemy import JSON, Boolean, Index, LargeBinary, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy import DDL, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_session_key() -> str:
    return secrets.token_hex(16)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Web session cookies carry this key and are valid only while it matches: "sign out
    # everywhere" replaces it, and an account made later with a reused id never has an old one.
    session_key: Mapped[str | None] = mapped_column(String(32), default=new_session_key)
    # Bumped by every collection import; an import that read an older version is refused, so two
    # imports at once can't both replace the collection and leave both files' cards in it.
    collection_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    identities: Mapped[list[Identity]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Identity(Base):
    """A sign-in method linked to a user. Accounts are never merged by e-mail."""

    __tablename__ = "identities"
    # An account has one WebAuthn user handle (its passkey identity); the index makes the database
    # refuse a second one.
    __table_args__ = (UniqueConstraint("provider", "subject"),
                      Index("uq_identities_one_passkey", "user_id", unique=True,
                            postgresql_where=text("provider = 'passkey'")))

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
    # "import" (a file), "assistant" (a change set made through an assistant, vault.owned_changes) or "undo"
    kind: Mapped[str] = mapped_column(String(20), default="import", server_default="import")
    app: Mapped[str | None] = mapped_column(String(200))  # who made an assistant change set (OAuth app, token name)
    # an assistant change set: {"lines": [printing, before, after, folders], "value_change_usd", "version_after", ...}
    changes: Mapped[dict | None] = mapped_column(JSON)


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
    # Who made the deck at its source (e.g. the Archidekt author), so a saved copy is still credited.
    source_author: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Share(Base):
    """Read access one user grants another, to their collection or to one deck.

    Created as a pending invite (only a SHA-256 of the one-time token is stored);
    accepting binds it to the recipient's account. The owner can revoke it and the
    recipient can leave at any time.
    """

    __tablename__ = "shares"
    # One accepted grant per owner, person and thing shared (pending invites have no grantee, and
    # NULLs never collide): the database refuses a duplicate even where row locks don't exist.
    __table_args__ = (Index("uq_shares_grant", "owner_id", "grantee_id", "kind",
                            text("coalesce(deck_id, 0)"), unique=True),)

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
    refresh_expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetiredRefreshToken(Base):
    """A refresh token that was already rotated (its hash only). If one comes back, it was
    copied, and its whole session is revoked. Kept until the token would have expired."""

    __tablename__ = "retired_refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("api_sessions.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthCode(Base):
    """One-time code handing a browser sign-in over to a native app (PKCE, ~2 minutes)."""

    __tablename__ = "auth_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    code_challenge: Mapped[str] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(String(300))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthClient(Base):
    """An OAuth client of the Vault's MCP authorization server (vault.oauth_clients): an AI app
    identified by an https URL whose metadata document the Vault fetched (``cimd``), or one that
    registered itself (``dcr``, RFC 7591). Holds what the app said about itself (name, redirect
    URIs), nothing about any person, so it is not personal data."""

    __tablename__ = "oauth_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(String(512), unique=True)
    kind: Mapped[str] = mapped_column(String(8))  # "cimd" | "dcr"
    name: Mapped[str] = mapped_column(String(80))
    redirect_uris: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # cimd: when the document was read
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)  # dcr: unused ones go
    # token endpoint authentication (vault.client_auth): "none" (public, PKCE) or "private_key_jwt" with these keys
    token_auth: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    jwks_uri: Mapped[str | None] = mapped_column(String(512))
    auth_alg: Mapped[str | None] = mapped_column(String(10))


class OAuthGrant(Base):
    """One person's consent to one app (the "connected app"): the scopes they allowed, the
    resource the tokens are for, and the current access and refresh token (hashes only).
    Deleting the row revokes everything issued from it."""

    __tablename__ = "oauth_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(String(512), index=True)
    scopes: Mapped[str] = mapped_column(String(40))  # what the person allowed, space-separated
    access_scopes: Mapped[str] = mapped_column(String(40))  # what the current access token carries (refresh may narrow)
    resource: Mapped[str] = mapped_column(String(300))
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    access_expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthRetiredRefresh(Base):
    """A rotated OAuth refresh token (hash only). If one comes back it was copied, and the whole
    grant is revoked. Kept until the token would have expired."""

    __tablename__ = "oauth_retired_refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    grant_id: Mapped[int] = mapped_column(ForeignKey("oauth_grants.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthCode(Base):
    """An authorization code (60 seconds, single use), bound to the client, redirect URI, PKCE
    challenge, resource and person it was issued for. Redeeming marks it used and records the
    grant it made: a second redemption revokes that grant."""

    __tablename__ = "oauth_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    client_id: Mapped[str] = mapped_column(String(512))
    redirect_uri: Mapped[str] = mapped_column(String(2000))
    code_challenge: Mapped[str] = mapped_column(String(128))
    resource: Mapped[str] = mapped_column(String(300))
    scopes: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grant_id: Mapped[int | None] = mapped_column(Integer)  # the grant this code made (no FK: it may be revoked)


class OAuthConsent(Base):
    """A consent screen that was shown and not yet answered (10 minutes). The browser's session holds
    only a random nonce; the answer must present it, and the row is consumed by one conditional DELETE,
    so a copied cookie and form can not be replayed. ``query`` is the authorization request being
    answered (no secrets)."""

    __tablename__ = "oauth_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True)
    query: Mapped[str] = mapped_column(String(2000))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NativeNonce(Base):
    """A one-time value already used, kept until it would expire anyway: a native sign-in's nonce
    (the same Apple / Google ID token can't sign in twice) or a Facebook data-deletion request
    (it can't be replayed). Rows name no one (hashes)."""

    __tablename__ = "native_nonces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(provider:nonce)
    expires: Mapped[float] = mapped_column(Float, index=True)  # unix time (the token's exp)


class RateHit(Base):
    """Requests to a sign-in endpoint in one minute (vault.ratelimit). ``key`` is a keyed hash of
    the endpoint's bucket and the client's IP, so no IP address is stored; rows go after a few
    minutes."""

    __tablename__ = "rate_hits"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    minute: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)  # unix time // 60
    hits: Mapped[int] = mapped_column(Integer)


class PasskeyChallenge(Base):
    """A pending passkey ceremony's challenge (vault.passkeys). The session cookie holds only the
    id; verifying claims the row with a conditional DELETE, so each challenge is used once, even
    by requests racing with the same cookie. Rows live five minutes and name no one."""

    __tablename__ = "passkey_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    challenge: Mapped[str] = mapped_column(String(128))  # base64url
    expires: Mapped[float] = mapped_column(Float, index=True)  # unix time


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
    power: Mapped[str | None] = mapped_column(String(20))
    toughness: Mapped[str | None] = mapped_column(String(20))
    loyalty: Mapped[str | None] = mapped_column(String(20))
    layout: Mapped[str | None] = mapped_column(String(40))
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


# -- The catalog: global, read-only grounding data (no user_id, nothing personal). Filled by
# jobs/sync_catalog.py from Scryfall's bulk files and the Comprehensive Rules; read by the MCP
# tools. Every row can be traced to a ``catalog_sources`` entry. See docs/catalog-design.md and
# docs/compliance.md: always show provenance, never present this data as the Vault's own.

class OracleCard(Base):
    """One row per Oracle card (Scryfall's ``oracle_cards`` file). Not the owned-printings ``cards``."""

    __tablename__ = "oracle_cards"
    __table_args__ = (
        Index("ix_oracle_cards_name_lower", text("lower(name)")),
        Index("ix_oracle_cards_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index("ix_oracle_cards_fts", text("to_tsvector('english', oracle_text)"),
              postgresql_using="gin"),
    )

    oracle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    layout: Mapped[str | None] = mapped_column(String(40))
    mana_cost: Mapped[str | None] = mapped_column(String(100))
    cmc: Mapped[float | None] = mapped_column(Float)
    type_line: Mapped[str | None] = mapped_column(String(300))
    oracle_text: Mapped[str | None] = mapped_column(Text)
    power: Mapped[str | None] = mapped_column(String(20))
    toughness: Mapped[str | None] = mapped_column(String(20))
    loyalty: Mapped[str | None] = mapped_column(String(20))
    defense: Mapped[str | None] = mapped_column(String(20))
    colors: Mapped[list] = mapped_column(JSONB, default=list)
    color_identity: Mapped[list] = mapped_column(JSONB, default=list)
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    produced_mana: Mapped[list] = mapped_column(JSONB, default=list)
    legalities: Mapped[dict] = mapped_column(JSONB, default=dict)
    faces: Mapped[list | None] = mapped_column(JSONB)  # per-face text for multi-face cards
    game_changer: Mapped[bool | None] = mapped_column(Boolean)
    edhrec_rank: Mapped[int | None] = mapped_column(Integer)
    released_at: Mapped[date | None] = mapped_column(Date)
    scryfall_uri: Mapped[str | None] = mapped_column(String(500))
    representative_id: Mapped[str | None] = mapped_column(String(36))  # the printing Scryfall shows
    artist: Mapped[str | None] = mapped_column(String(200))  # of that printing: credited wherever its image is shown
    image_normal: Mapped[str | None] = mapped_column(String(500))  # Scryfall's own link to that printing's image
    digital: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(40))  # lets the daily job skip unchanged rows


# The name search index needs pg_trgm; the migration creates it too (Neon and Postgres 14+ allow it).
event.listen(OracleCard.__table__, "before_create", DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm"))


class Ruling(Base):
    """A ruling as published (Wizards' text via Scryfall). The id is a hash of its content, so a
    ruling that did not change is never rewritten."""

    __tablename__ = "rulings"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(String(36), index=True)
    published_at: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20))  # "wotc" or "scryfall": who wrote it
    comment: Mapped[str] = mapped_column(Text)


class OracleTag(Base):
    """A functional tag from Scryfall's Tagger (community opinion, not a rule)."""

    __tablename__ = "oracle_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    label: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    parent_ids: Mapped[list] = mapped_column(JSONB, default=list)
    child_ids: Mapped[list] = mapped_column(JSONB, default=list)


class OracleTagLink(Base):
    """Which cards carry which tag, for a curated set of tags only (docs/catalog-design.md)."""

    __tablename__ = "oracle_tag_links"
    __table_args__ = (Index("ix_oracle_tag_links_oracle_id", "oracle_id"),)

    tag_id: Mapped[str] = mapped_column(ForeignKey("oracle_tags.id", ondelete="CASCADE"), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    weight: Mapped[str | None] = mapped_column(String(12))  # Scryfall's own word: weak, median, strong...


class LegalityChange(Base):
    """Written by the daily job when a card's legality in a format changes, so answers can say "as of"."""

    __tablename__ = "legality_changes"
    __table_args__ = (Index("ix_legality_changes_oracle_id", "oracle_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    oracle_id: Mapped[str] = mapped_column(String(36))
    format: Mapped[str] = mapped_column(String(30))
    old: Mapped[str | None] = mapped_column(String(20))
    new: Mapped[str | None] = mapped_column(String(20))
    observed_on: Mapped[date] = mapped_column(Date)


class OraclePrice(Base):
    """The cheapest priced paper printing of each card, today only (no history; see docs/catalog-design.md)."""

    __tablename__ = "oracle_prices"

    oracle_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scryfall_id: Mapped[str] = mapped_column(String(36))
    usd: Mapped[float | None] = mapped_column(Float)
    usd_foil: Mapped[float | None] = mapped_column(Float)
    eur: Mapped[float | None] = mapped_column(Float)
    day: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(40), default="scryfall")  # whose numbers these are


class CatalogSource(Base):
    """What was loaded, when, from where. Feeds ``whoami`` and every "as of" line."""

    __tablename__ = "catalog_sources"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)  # "oracle_cards", "rulings", "oracle_tags", "oracle_prices"
    version: Mapped[str] = mapped_column(String(60))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(500))


class StagedUpload(Base):
    """A collection file an assistant asked the person to upload (``start_collection_upload``): too big to pass
    through a chat. The link holds a random ticket (only its hash is stored), works for one hour, and the file
    waits here, not imported, until the person confirms through their assistant. Deleted when applied or expired."""

    __tablename__ = "staged_uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticket_hash: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArchidektDeckCache(Base):
    """A public Archidekt deck's JSON as last fetched, so repeat reads never reach Archidekt (docs/compliance.md: its
    terms forbid automated requests and its developers warned they would lock the API if it is hammered). Public data
    only, no person's id; entries unread for ``RETENTION`` are deleted."""

    __tablename__ = "archidekt_deck_cache"

    deck_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
