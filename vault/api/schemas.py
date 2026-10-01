"""Typed request/response models for /api/v1 (they drive the OpenAPI document, which can
generate the iOS client, e.g. with Apple's swift-openapi-generator)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Link(BaseModel):
    href: str
    title: str | None = None


class Hal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    links: dict[str, Link] = Field(default_factory=dict, alias="_links")


class Page(Hal):
    count: int
    total: int


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str


# -- collection ------------------------------------------------------------------------------

class SetRef(BaseModel):
    code: str
    name: str


class Price(BaseModel):
    market: float
    low: float | None = None
    mid: float | None = None
    currency: Literal["USD"] = "USD"
    source: Literal["scryfall", "file"]


class Acquired(BaseModel):
    first: str | None = None
    last: str | None = None


class CardItem(Hal):
    id: str
    name: str
    set: SetRef
    collector_number: str
    printing: str
    finish: str
    condition: str
    language: str
    quantity: int
    paid: float | None = Field(None, description="Total paid for these copies; null when the owner hides it")
    price: Price
    value: float
    acquired: Acquired
    scryfall_id: str | None = None


class CardImage(BaseModel):
    small: str | None = None
    normal: str | None = None
    artist: str | None = None
    credit: str


class CardData(BaseModel):
    scryfall_id: str
    oracle_id: str | None = None
    name: str
    type_line: str | None = None
    mana_cost: str | None = None
    cmc: float | None = None
    colors: list[str] = []
    color_identity: list[str] = []
    oracle_text: str | None = None
    rarity: str | None = None
    finishes: list[str] = []
    image: CardImage
    scryfall_uri: str | None = None


class PricePoint(BaseModel):
    day: str
    price: float | None = None


class CopyRow(BaseModel):
    quantity: int
    folder: str | None = None
    purchase_price: float | None = None
    purchase_date: str | None = None


class CardDetail(CardItem):
    card: CardData | None = None
    price_history: list[PricePoint] = []
    copies: list[CopyRow] = []


class CardPage(Page):
    items: list[CardItem]


class CollectionSummary(Hal):
    version: str = Field("", description="Changes whenever the collection or its prices change; clients cache by it")
    source: str | None = Field(None, description="Format of the last import: dragonshield, moxfield or csv")
    copies: int
    printings: int
    cards: int
    sets: int
    market_value: float
    paid: float | None = None
    costs_hidden: bool
    priced_by_scryfall: int
    by_printing: dict[str, int]
    by_condition: dict[str, int]
    imported_at: str | None = None
    prices_as_of: str | None = None
    owner: str | None = Field(None, description="Display name of the owner, for shared collections")


class SetItem(Hal):
    code: str
    name: str
    copies: int
    printings: int
    market_value: float


class SetPage(Page):
    items: list[SetItem]


class Month(BaseModel):
    month: str
    copies: int
    paid: float | None = None


class Timeline(Hal):
    months: list[Month]


class DayValue(BaseModel):
    day: str
    market: float
    cost: float | None = None
    copies: int
    priced: int


class HistoryPage(Page):
    items: list[DayValue]


# -- account, tokens -----------------------------------------------------------------------------

class Me(Hal):
    id: int
    name: str | None = None
    email: str | None = None
    providers: list[str]


class ProfileUpdate(BaseModel):
    name: str


class DeleteRequest(BaseModel):
    confirm: str = Field(description='Must be "DELETE"')


class SessionItem(Hal):
    id: int
    client: str
    device_name: str | None = None
    created_at: str
    last_used_at: str | None = None
    current: bool = False


class SessionPage(Page):
    items: list[SessionItem]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str
    session_id: int | None = None


class NativeSignIn(BaseModel):
    id_token: str
    nonce: str | None = Field(None, description="The raw nonce the app generated for this sign-in")
    name: str | None = Field(None, description="Apple sends the user's name to the app only once; pass it here")
    device_name: str | None = None


class TokenRequest(BaseModel):
    grant_type: Literal["refresh_token", "authorization_code"]
    refresh_token: str | None = None
    code: str | None = None
    code_verifier: str | None = None
    redirect_uri: str | None = None
    device_name: str | None = None


# -- imports, decks, shares ------------------------------------------------------------------------

class ImportItem(Hal):
    id: int
    filename: str
    source: str = Field("dragonshield", description="Detected format: dragonshield, moxfield or csv")
    rows: int
    copies: int
    changes: dict[str, int]
    created_at: str


class ImportPage(Page):
    items: list[ImportItem]


class DeckIn(BaseModel):
    name: str
    text: str = Field(max_length=50_000)
    source_url: str | None = None


class TextIn(BaseModel):
    text: str = Field(max_length=50_000, description="A pasted decklist in any common format")


class DeckLine(BaseModel):
    name: str
    set: str
    collector_number: str
    qty: int
    finish: str
    section: str
    categories: list[str]


class ParsedDeck(BaseModel):
    cards: list[DeckLine]
    unparsed: list[str]


class CoverageLine(BaseModel):
    name: str
    set: str | None = None
    number: str | None = None
    need: int
    have: int
    missing: int
    status: Literal["owned", "partial", "missing"]


class Coverage(BaseModel):
    cards: list[CoverageLine]
    unparsed: list[str]


class Deck(Hal):
    id: int
    name: str
    text: str
    source_url: str | None = None
    created_at: str
    updated_at: str
    coverage: Coverage | None = None
    from_: str | None = Field(None, alias="from", description="Who shared it (shared decks only)")


class DeckPage(Page):
    items: list[Deck]


class ShareIn(BaseModel):
    kind: Literal["collection", "deck"]
    deck_id: int | None = None
    show_costs: bool = False


class Invite(Hal):
    id: int
    url: str
    expires_at: str


class ShareItem(Hal):
    id: int
    kind: str
    deck_id: int | None = None
    deck_name: str | None = None
    show_costs: bool
    status: Literal["active", "pending"]
    with_: str | None = Field(None, alias="with")
    created_at: str
    expires_at: str | None = None


class SharePage(Page):
    items: list[ShareItem]


class AcceptIn(BaseModel):
    token: str


class SharedItem(Hal):
    id: int
    kind: str
    from_: str = Field(alias="from")
    deck_name: str | None = None
    show_costs: bool


class SharedPage(Page):
    items: list[SharedItem]


# -- personal access tokens ---------------------------------------------------------------------

class AccessTokenIn(BaseModel):
    name: str = Field("Agent", max_length=80, description="What the token is for, e.g. 'Claude desktop'")
    scopes: list[Literal["read", "write"]] = Field(["read"], description='"read", or "read" and "write"')
    expires_in_days: int = Field(90, ge=1, le=365)

    @field_validator("scopes")
    @classmethod
    def _needs_read(cls, scopes: list[str]) -> list[str]:
        if "read" not in scopes:
            raise ValueError('scopes must include "read" (a write-only token is not offered)')
        return scopes


class AccessTokenItem(Hal):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    created_at: str
    expires_at: str
    last_used_at: str | None = None


class NewAccessToken(AccessTokenItem):
    token: str = Field(description="The token. It is shown only once; store it safely.")
    mcp_url: str = Field(description="The MCP server to point agents at, with this token as the bearer")


class AccessTokenPage(Page):
    items: list[AccessTokenItem]


class ExportFormat(Hal):
    format: str
    label: str
    description: str
    media_type: str
    extension: str
    reimportable: bool = Field(description="The Vault (and the app it's named after) can read it back")


class ExportFormats(Hal):
    items: list[ExportFormat]


class PasskeyItem(Hal):
    id: int
    name: str
    synced: bool = Field(description="Backed up by the platform (e.g. iCloud Keychain, Google Password Manager)")
    created_at: str
    last_used_at: str | None = None


class PasskeyPage(Page):
    items: list[PasskeyItem]


# -- card catalog (Scryfall data served by the Vault) ---------------------------------------------

class CardIdentifier(BaseModel):
    """One of: ``id`` (Scryfall id), ``set`` + ``collector_number``, or ``name`` (optionally with ``set``)."""
    id: str | None = Field(None, max_length=36)
    set: str | None = Field(None, max_length=20)
    collector_number: str | None = Field(None, max_length=30)
    name: str | None = Field(None, max_length=300)


class CardLookupIn(BaseModel):
    identifiers: list[CardIdentifier] = Field(min_length=1, max_length=75)
    refresh: bool = Field(False, description="Fetch fresh data and prices from Scryfall instead of the Vault's copy")


class CatalogCard(BaseModel):
    model_config = ConfigDict(extra="allow")
    object: str = "card"
    id: str
    name: str
    set: str
    collector_number: str
    image_uris: dict[str, str] = Field(default_factory=dict)
    prices: dict[str, str | None] = Field(default_factory=dict)
    artist: str | None = None


class CardLookup(Hal):
    data: list[CatalogCard]
    not_found: list[dict]
    unavailable: bool = Field(description="Scryfall was needed for some cards but didn't answer; retry later")


class CatalogSet(BaseModel):
    code: str
    name: str | None = None
    icon_svg_uri: str | None = None
    released_at: str | None = None
    set_type: str | None = None
    parent_set_code: str | None = None


class SetCatalog(Page):
    items: list[CatalogSet]
    aliases: dict[str, str] = Field(default_factory=dict,
                                    description="Dragon Shield set codes and the Scryfall set code each stands for")
