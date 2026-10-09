"""Tables.

Per user (tenant data, private unless shared): ``users``, ``identities`` (one per
linked sign-in method (OAuth provider or passkey)), ``imports`` (each uploaded CSV with its change summary),
``entries`` (the collection, one row per source-file row so exports round-trip),
``collection_baselines`` (the last imported file's cards, for the next re-import's three-way update),
``decks`` and ``collection_values``. ``shares`` records access a user has granted
to someone else. :func:`vault.privacy.purge_user` removes all of it.

Shared: ``cards`` (Scryfall data for printings someone owns) and ``price_snapshots``
(one row per printing per day, written by the daily sync).
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

from mtg_toolkits.models import CollectionEntry, Condition, Finish
from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Index, LargeBinary, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy import DDL, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

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


# What every ``vault_metadata`` column must hold (docs/collections.md, section 7): a JSON object with a positive integer
# ``version`` (the shape its readers upgrade from), at most 8 KB. Free-form otherwise, never read by the Vault's own logic.
METADATA_OK = ("coalesce(jsonb_typeof(vault_metadata) = 'object' AND jsonb_typeof(vault_metadata->'version') = 'number' "
               "AND (vault_metadata->>'version') ~ '^[1-9][0-9]{0,8}$' AND octet_length(vault_metadata::text) <= 8192, false)")
METADATA_DEFAULT = text("""'{"version": 1}'::jsonb""")


class Bucket(Base):
    """A named place copies live in: a binder, a deck box, a trade box (docs/collections.md, #118/#121). Every entry is in
    exactly one bucket and the inventory is the sum of them. A bucket per distinct Dragon Shield folder (names compare
    case-insensitively), and "Unsorted" for copies with no folder. ``entries.folder`` stays as imported, for the CSV round-trip."""

    __tablename__ = "buckets"
    __table_args__ = (Index("uq_buckets_user_name", "user_id", text("lower(name)"), unique=True),
                      CheckConstraint(METADATA_OK, name="ck_buckets_vault_metadata"))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(12), default="folder")  # default (Unsorted), folder (from a file) or made (by the person)
    position: Mapped[int] = mapped_column(Integer, default=0)
    vault_metadata: Mapped[dict] = mapped_column(JSONB, default=lambda: {"version": 1}, server_default=METADATA_DEFAULT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TagAssignment(Base):
    """A person's tag on a card (the oracle id, not a printing and not an entry: entries are replaced on every import, so a
    tag survives them and stays, shown as "not owned", when the card leaves the inventory). ``source`` says who wrote it."""

    __tablename__ = "tag_assignments"
    __table_args__ = (UniqueConstraint("user_id", "oracle_id", "tag", name="uq_tag_assignments_card_tag"),
                      Index("ix_tag_assignments_user_tag", "user_id", "tag"),
                      CheckConstraint("tag ~ '^[a-z0-9:-]{1,40}$'", name="ck_tag_assignments_tag"),
                      CheckConstraint("source IN ('person', 'assistant', 'system')", name="ck_tag_assignments_source"),
                      CheckConstraint(METADATA_OK, name="ck_tag_assignments_vault_metadata"))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    oracle_id: Mapped[str] = mapped_column(String(36))
    tag: Mapped[str] = mapped_column(String(40))  # lower case letters, digits, "-" and ":" (deck:sliver, trade:sell)
    source: Mapped[str] = mapped_column(String(12), default="person")  # person, assistant or system
    source_detail: Mapped[str | None] = mapped_column(String(200))  # which app wrote it, or imported:<file>
    vault_metadata: Mapped[dict] = mapped_column(JSONB, default=lambda: {"version": 1}, server_default=METADATA_DEFAULT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CardAnnotation(Base):
    """Free-form notes about a card for one person (``vault_metadata``), keyed like the tags: the oracle id. Written by the
    person or by an assistant (``source``), never read by the Vault's own logic."""

    __tablename__ = "card_annotations"
    __table_args__ = (UniqueConstraint("user_id", "oracle_id", name="uq_card_annotations_card"),
                      CheckConstraint("source IN ('person', 'assistant', 'system')", name="ck_card_annotations_source"),
                      CheckConstraint(METADATA_OK, name="ck_card_annotations_vault_metadata"))

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    oracle_id: Mapped[str] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(12), default="person")
    source_detail: Mapped[str | None] = mapped_column(String(200))
    vault_metadata: Mapped[dict] = mapped_column(JSONB, default=lambda: {"version": 1}, server_default=METADATA_DEFAULT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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
    # The Vault's grouping of ``folder`` (see Bucket). Filled in by the before_flush hook below for every new entry, so no
    # code path that adds a row can leave a copy outside a bucket. RESTRICT: a bucket with copies in it can't be deleted.
    bucket_id: Mapped[int] = mapped_column(ForeignKey("buckets.id", ondelete="RESTRICT"), index=True)
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


class CollectionBaseline(Base):
    """The last imported file as it was imported (per person), the base of the next re-import's three-way update
    (vault.merge, #194): each card with its copies and their state. Replaced by every import; never changed by the
    Vault's own edits, which is how a re-import can tell them from what changed in the person's app."""

    __tablename__ = "collection_baselines"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("imports.id", ondelete="SET NULL"))
    cards: Mapped[list] = mapped_column(JSON)  # vault.merge.stored()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BucketBaseline(Base):
    """The base of a re-import into one bucket (#124): the last file imported into that bucket as it was imported, the same
    thing ``CollectionBaseline`` holds for a whole-collection import, kept per scope (docs/collections.md, decision 6). Replaced by
    every import into the bucket, forgotten by the next whole-collection import (which becomes the latest word on every bucket),
    and deleted with its bucket. Never changed by the Vault's own edits."""

    __tablename__ = "bucket_baselines"

    bucket_id: Mapped[int] = mapped_column(ForeignKey("buckets.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("imports.id", ondelete="SET NULL"))
    cards: Mapped[list] = mapped_column(JSON)  # vault.merge.stored()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResetSnapshot(Base):
    """What the person's last reset (#129) removed, kept for a limited time so it can be undone: one per person (the latest reset
    only; the next reset replaces it), compact JSON compressed with zlib in ``payload`` (the removed rows with their bucket and
    folder, the baselines of the scope, and the tags, notes and import history when the reset cleared them). ``version_after`` is
    the collection version the reset left: the undo is allowed only while the collection is still at it. Deleted by the daily
    retention job once ``expires_at`` has passed, with the account, and when the undo is used."""

    __tablename__ = "reset_snapshots"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("imports.id", ondelete="SET NULL"))  # the reset's entry in the history
    bucket_id: Mapped[int | None] = mapped_column(Integer)  # the bucket that was reset; none: the whole inventory
    version_after: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)  # what the snapshot holds, in words and numbers
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    raw_bytes: Mapped[int] = mapped_column(Integer)  # the JSON before compression (the cap applies to it)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


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
    # When the list was last taken from source_url (read by the Vault, or pasted with the link); decks saved before this
    # column existed carry their last saved time.
    source_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The deck's format as the person (or their assistant) set it; unset, it is read from the list (vault.deck_overview).
    format: Mapped[str | None] = mapped_column(String(30))
    # The version the person had last looked at when they last opened the deck page (#93): "changed since you last looked"
    # is the latest list against this one. Null: they have not opened it since versions exist, or it was dropped (20 kept).
    viewed_version_id: Mapped[int | None] = mapped_column(ForeignKey("deck_versions.id", ondelete="SET NULL", use_alter=True,
                                                                     name="fk_decks_viewed_version"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeckVersion(Base):
    """A saved deck's list as it was when its cards last changed (vault.deck_versions, #93). At most 20 a deck; they go with
    the deck (and so with the account)."""

    __tablename__ = "deck_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(20))  # of the cards, whatever their spelling or order
    source: Mapped[str] = mapped_column(String(12))  # saved, edited, imported or refreshed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    """A passkey ceremony that someone tried to finish (vault.passkeys), kept so it can be used once. The challenge itself
    lives in the signed session cookie (#346): a row is written only when a verification is attempted, with the ceremony's id
    as primary key, so of two requests racing with the same cookie only the first insert wins. Rows live until the
    ceremony would have expired (five minutes) and name no one."""

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


class EmailCode(Base):
    """A one-time code (and a one-click link) e-mailed to the address on an account, to confirm it is the person before a
    serious account action (#347, vault.recent_signin). Bound to the account, to the browser session that asked
    (``session_digest``, a keyed hash of the account's session key and that browser's request id) and to nothing else. Only keyed
    hashes are stored: of the code, of the link's token and of the session. A code lives ten minutes, works once and dies after
    five tries. Rows are also what the per-account and per-day send caps count, so they are kept two days (vault.retention)."""

    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_digest: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64))
    link_hash: Mapped[str] = mapped_column(String(64), unique=True)
    asked_from: Mapped[str] = mapped_column(String(80))  # "Chrome on Windows": what the e-mail says asked (no address is kept)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # the link was approved; the asking browser picks it up
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # spent, replaced or refused: never usable again
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


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


def _cents_property(column: str):
    """A price in dollars (what every reader and the API use) over the integer-cents column that stores it."""
    def get(self) -> float | None:
        cents = getattr(self, column)
        return None if cents is None else cents / 100

    def set_(self, value) -> None:
        from .prices import to_cents  # no import cycle at load time: prices imports this module

        setattr(self, column, to_cents(value))
    return property(get, set_)


class PriceSnapshot(Base):
    """One row per owned printing per day: the Scryfall prices that day, in **integer cents** (``usd_cents`` and so on), under a
    native 16-byte ``uuid`` key. Compacted from float prices under a ``varchar(36)`` key by migration 0113 (#63): about 40% fewer
    bytes a row, measured in docs/catalog-design.md. Readers use the dollar properties ``usd``, ``usd_foil``... (None when there
    is no price); a price no import would accept is stored as no price (``vault.prices.to_cents``). Ids that are not UUIDs
    (a user's CSV can carry anything in its id column) never match a row: see ``vault.prices.valid_ids``. ``eur_etched`` was
    dropped: nothing read it."""

    __tablename__ = "price_snapshots"

    scryfall_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    usd_cents: Mapped[int | None] = mapped_column(Integer)
    usd_foil_cents: Mapped[int | None] = mapped_column(Integer)
    usd_etched_cents: Mapped[int | None] = mapped_column(Integer)
    eur_cents: Mapped[int | None] = mapped_column(Integer)
    eur_foil_cents: Mapped[int | None] = mapped_column(Integer)

    usd = _cents_property("usd_cents")
    usd_foil = _cents_property("usd_foil_cents")
    usd_etched = _cents_property("usd_etched_cents")
    eur = _cents_property("eur_cents")
    eur_foil = _cents_property("eur_foil_cents")

    def for_finish(self, finish: str, currency: str = "usd") -> float | None:
        suffix = {"nonfoil": "", "foil": "_foil", "etched": "_etched"}.get(finish, "")
        return getattr(self, currency + suffix, None)  # None for a column that is not stored (eur_etched)


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


class OraclePrinting(Base):
    """Every priced paper printing of every card, today's figures only (Scryfall's, from TCGplayer and Cardmarket), so a
    shopping list can pick the cheapest printing that fits a person's rules (set, language, finish; #29). Third-party data:
    shown with its provenance, never as the Vault's. The day of the figures is the ``oracle_printings`` row of
    ``catalog_sources`` (rows whose figures did not change are not rewritten, so no row carries its own day). No history; no
    personal data."""

    __tablename__ = "oracle_printings"

    scryfall_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(String(36), index=True)
    set_code: Mapped[str] = mapped_column(String(10))
    set_name: Mapped[str | None] = mapped_column(String(100))
    collector_number: Mapped[str] = mapped_column(String(20))
    lang: Mapped[str] = mapped_column(String(5), default="en")
    usd: Mapped[float | None] = mapped_column(Float)
    usd_foil: Mapped[float | None] = mapped_column(Float)
    usd_etched: Mapped[float | None] = mapped_column(Float)


class LimitedGameStat(Base):
    """Per-card game counts (games, never copies) from 17Lands' public game data, one row per set, format and card (#178, docs/limited-data-design.md).

    Third-party data (17Lands, CC BY 4.0), reduced by the Vault to counts: no game, deck, player or time of a game is kept, and
    the table has no user. Rates, intervals and sample warnings are worked out when read (``vault.limited_stats``), never stored."""

    __tablename__ = "limited_game_stats"

    set_code: Mapped[str] = mapped_column(String(10), primary_key=True)  # 17Lands' expansion code, upper case
    format: Mapped[str] = mapped_column(String(20), primary_key=True)  # PremierDraft or TradDraft
    card_name: Mapped[str] = mapped_column(String(200), primary_key=True)  # as 17Lands names the card
    oracle_id: Mapped[str | None] = mapped_column(String(36), index=True)  # the catalog's card, matched by name; null when none matches
    games_played: Mapped[int] = mapped_column(Integer, default=0)  # games with the card in the main deck (#GP)
    wins_played: Mapped[int] = mapped_column(Integer, default=0)
    opening: Mapped[int] = mapped_column(Integer, default=0)  # games with the card in the kept opening hand (#OH)
    wins_opening: Mapped[int] = mapped_column(Integer, default=0)
    drawn: Mapped[int] = mapped_column(Integer, default=0)  # games with the card drawn later, not tutored (#GD)
    wins_drawn: Mapped[int] = mapped_column(Integer, default=0)
    in_hand: Mapped[int] = mapped_column(Integer, default=0)  # games with the card in hand at least once, opener or draw (#GIH): a union
    wins_in_hand: Mapped[int] = mapped_column(Integer, default=0)


class LimitedPickStat(Base):
    """Per-card pick counts from 17Lands' public draft data (#178): how often a card was seen, where it was last seen, how often
    it was taken and at which pick. Same rules as :class:`LimitedGameStat`: counts only, no draft, no drafter."""

    __tablename__ = "limited_pick_stats"

    set_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    format: Mapped[str] = mapped_column(String(20), primary_key=True)
    card_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    oracle_id: Mapped[str | None] = mapped_column(String(36), index=True)
    seen: Mapped[int] = mapped_column(Integer, default=0)  # packs (per drafter and pack round) in which the card was seen
    last_seen_sum: Mapped[int] = mapped_column(Integer, default=0)  # sum of the pick number (1 to 15) at which it was last seen
    picked: Mapped[int] = mapped_column(Integer, default=0)  # times it was taken
    picked_sum: Mapped[int] = mapped_column(Integer, default=0)  # sum of the pick numbers at which it was taken


class LimitedSource(Base):
    """What was read from 17Lands, one row per set, format and kind (``game`` or ``draft``): the version of the data behind every
    figure (the file's own ETag and Last-Modified, not the page's date), and the window it covers. Written last, in the same
    transaction as the rows it describes."""

    __tablename__ = "limited_sources"

    set_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    format: Mapped[str] = mapped_column(String(20), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_length: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    records: Mapped[int] = mapped_column(BigInteger, default=0)  # games (kind game) or picks (kind draft) used
    skipped_records: Mapped[int] = mapped_column(BigInteger, default=0)  # games left out for inconsistent data
    wins: Mapped[int | None] = mapped_column(BigInteger)  # games won among the games used (game kind): baseline = wins / records
    first_picks: Mapped[int | None] = mapped_column(BigInteger)  # draft kind: rows at the first pick of a pack round...
    empty_first_picks: Mapped[int | None] = mapped_column(BigInteger)  # ...and how many of them show an empty pack (P1P1 data missing)
    first_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cards: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_cards: Mapped[int] = mapped_column(Integer, default=0)


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
    # The bucket the import goes into (#124), chosen when the link is made (or on the upload page) and used by the preview and the
    # apply; none: the whole collection. CASCADE: a link whose bucket is deleted is deleted, never a whole-collection import.
    bucket_id: Mapped[int | None] = mapped_column(ForeignKey("buckets.id", ondelete="CASCADE"))
    bucket_bound: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))  # made for it: the page can't change it


class ArchidektDeckCache(Base):
    """A public Archidekt deck's JSON as last fetched, so repeat reads never reach Archidekt (docs/compliance.md: its
    terms forbid automated requests and its developers warned they would lock the API if it is hammered). Public data
    only, no person's id; entries unread for ``RETENTION`` are deleted."""

    __tablename__ = "archidekt_deck_cache"

    deck_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


@event.listens_for(Session, "before_flush")
def _entries_get_their_bucket(session, flush_context, instances):
    """Every new entry lands in the bucket of its folder, whichever code adds it (an import, an assistant's edit, a merge):
    the inventory is the sum of the buckets only if no copy is outside one (docs/collections.md, #121)."""
    fresh = [o for o in session.new if isinstance(o, Entry) and o.bucket_id is None]
    if fresh:
        from . import buckets

        buckets.assign(session, fresh)
