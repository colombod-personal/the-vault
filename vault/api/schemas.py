"""Typed request/response models for /api/v1 (they drive the OpenAPI document, which can
generate the iOS client, e.g. with Apple's swift-openapi-generator)."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..deck_tools import FORMATS


MAX_ID = 2**31 - 1  # ids are INTEGER columns: 32 bits on Postgres


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


class CardImage(BaseModel):
    small: str | None = None
    normal: str | None = None
    artist: str | None = None
    credit: str


class CardPrices(BaseModel):
    usd: float | None = None
    usd_foil: float | None = None
    usd_etched: float | None = None
    eur: float | None = None
    eur_foil: float | None = None
    eur_etched: float | None = None
    day: str | None = Field(None, description="The day of these prices")


class CardData(BaseModel):
    scryfall_id: str
    oracle_id: str | None = None
    name: str
    set_code: str | None = Field(None, description="Scryfall's set code, lower case")
    set_name: str | None = None
    collector_number: str | None = None
    type_line: str | None = None
    mana_cost: str | None = None
    cmc: float | None = None
    colors: list[str] = []
    color_identity: list[str] = []
    oracle_text: str | None = None
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    rarity: str | None = None
    layout: str | None = None
    finishes: list[str] = []
    image: CardImage
    scryfall_uri: str | None = None
    prices: CardPrices | None = Field(None, description="Scryfall's latest prices for every finish")


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
    paid_quantity: int | None = Field(None, description="How many of these copies `paid` covers (copies with no "
                                      "price paid recorded are left out of P&L); null when the owner hides it")
    gain: float | None = Field(None, description="Unrealised gain or loss on the copies `paid` covers: their market "
                               "value today minus `paid`. Null when no cost is known or the owner hides costs")
    price: Price
    value: float
    acquired: Acquired
    scryfall_id: str | None = None
    card: CardData | None = Field(None, description="Scryfall's data for the printing, kept by the daily sync; "
                                  "null until the printing is matched and synced")


class PricePoint(BaseModel):
    day: str
    price: float | None = None


class CopyRow(BaseModel):
    quantity: int
    folder: str | None = None
    purchase_price: float | None = None
    purchase_date: str | None = None


class CardDetail(CardItem):
    price_history: list[PricePoint] = []
    copies: list[CopyRow] = Field([], description="The rows this printing came from: the first 500")
    copies_total: int = Field(0, description="How many rows this printing came from in all")


class CardPage(Page):
    items: list[CardItem]
    value_total: float | None = Field(None, description="Market value of every printing matching the filters "
                                      "(all pages, not just this one)")


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
    pnl: float | None = Field(None, description="Unrealised gain or loss on the copies with a known cost: "
                              "known_cost_market - known_cost_paid. Null when no cost is known or costs are hidden")
    pnl_pct: float | None = Field(None, description="pnl as a percentage of known_cost_paid")
    known_cost_paid: float | None = Field(None, description="What was paid for the copies with a recorded price paid")
    known_cost_market: float | None = Field(None, description="Today's market value of those same copies")
    known_cost_copies: int | None = Field(None, description="Copies with a recorded price paid (P&L counts only these)")
    unknown_cost_copies: int | None = Field(None, description="Copies without one: left out of P&L, never valued at "
                                            "zero cost")


class SetItem(Hal):
    code: str
    name: str
    copies: int
    printings: int
    market_value: float
    colors: dict[str, int] = Field(default_factory=dict, description="Copies by colour identity: W, U, B, R, G, "
                                   "M (multicolour), C (colourless), unknown (printing not in the card table yet)")
    released_at: str | None = Field(None, description="The set's release date, from the Scryfall set catalog when "
                                    "the Vault has it")


class SetPage(Page):
    items: list[SetItem]


class Month(BaseModel):
    month: str
    copies: int
    market: float = Field(0.0, description="Today's market value of the copies bought that month")
    paid: float | None = None


class Timeline(Hal):
    months: list[Month]


class DayValue(BaseModel):
    day: str
    market: float
    cost: float | None = None
    copies: int
    priced: int
    imported: bool = Field(False, description="A file was imported that day (a change there may be cards, not prices)")


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


class ConnectedApp(Hal):
    id: int
    name: str = Field(description="The app's name as it described itself")
    domain: str | None = Field(None, description="The web address that identifies the app (absent for self-registered apps)")
    verified_by_address: bool = Field(description="False for an app that registered itself: the Vault can't confirm who made it")
    scopes: list[str] = Field(description="What you allowed: read, and write if you chose it")
    created_at: str
    last_used_at: str | None = None


class ConnectedAppPage(Page):
    items: list[ConnectedApp]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str
    session_id: int | None = None


class NativeSignIn(BaseModel):
    id_token: str
    nonce: str = Field(min_length=16, max_length=200,
                       description="The raw nonce the app generated for this sign-in (required; each works once)")
    name: str | None = Field(None, max_length=200,
                             description="Apple sends the user's name to the app only once; pass it here")
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
    kind: str = Field("import", description="import (a file), assistant (edits made through an assistant) or undo")
    app: str | None = Field(None, description="For assistant edits: the app that made them")
    lines: list[dict] | None = Field(None, description="For assistant edits: each printing, copies before and after")
    merge: dict | None = Field(None, description="For imports: how the file was applied (mode, what came from the person's app, "
                                                 "which edits made in the Vault were kept, the conflicts and their answers)")
    undoable: bool | None = Field(None, description="For assistant edits: true on the one change set that can be undone now "
                                  "(the latest, until the collection changes again); POST /collection/changes/undo")
    undone: bool | None = Field(None, description="For assistant edits: true once it was undone")


class ImportPage(Page):
    items: list[ImportItem]


class OwnedChangeLine(BaseModel):
    action: Literal["add", "remove", "set"] = Field(description="add or remove copies, or set the number owned")
    name: str = Field(min_length=1, max_length=300, description="The card's name")
    quantity: int = Field(ge=0, le=999)
    set: str | None = Field(None, max_length=20, description="The printing's set code, with number")
    number: str | None = Field(None, max_length=30, description="The printing's collector number, with set")
    finish: Literal["nonfoil", "foil", "etched"] | None = None
    printing_unknown: bool = Field(False, description="Only when the person does not know the printing (adds only)")


class OwnedChangesIn(BaseModel):
    lines: list[OwnedChangeLine] = Field(min_length=1, max_length=50)


class OwnedChangesApplyIn(OwnedChangesIn):
    confirmation: str = Field(min_length=8, max_length=400, description="From the preview the person agreed to")


class OwnedUndoIn(BaseModel):
    confirmation: str | None = Field(None, max_length=400, description="Omit to preview the undo; give it to apply")


class ImportLinkIn(BaseModel):
    url: str = Field(min_length=8, max_length=500, description="An Archidekt deck link (archidekt.com/decks/<number>)")
    name: str | None = Field(None, max_length=200, description="Name to save it under; default: the deck's name on Archidekt")
    update: bool = Field(False, description="If this deck is already saved, compare it with Archidekt's current list")
    confirm: bool = Field(False, description="With update: replace the saved list (needs the preview's fingerprint)")
    fingerprint: str | None = Field(None, max_length=64, description="From the update preview")


class DeckRefreshIn(BaseModel):
    confirm: bool = Field(False, description="Replace the saved list with the source's (needs the preview's fingerprint)")
    fingerprint: str | None = Field(None, max_length=64, description="From the preview: the source's list that was shown")


class DeckIn(BaseModel):
    name: str
    text: str = Field(max_length=50_000)
    format: Literal[FORMATS] | None = Field(None, description="The deck's format (commander, standard, pioneer, ...). Left as "
                                            "it is when an update omits it; null clears it (then it is read from the list)")
    source_url: str | None = Field(None, max_length=500, description="Where the deck came from (an http or https "
                                   "link). Left as it is when an update omits it; null clears it")
    source_author: str | None = Field(None, max_length=200, description="Who made the deck at its source (for the "
                                      "credit). Left as it is when an update omits it; null clears it")

    @field_validator("source_author")
    @classmethod
    def _author(cls, author: str | None) -> str | None:
        return (author or "").strip() or None

    @field_validator("source_url")
    @classmethod
    def _http_link(cls, url: str | None) -> str | None:
        url = (url or "").strip()
        if not url:
            return None
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise ValueError("source_url must be an http or https link")
        return url


class DeckAuthorIn(BaseModel):
    source_url: str = Field(max_length=500, description="The link the author was read from: the deck's current link")
    source_author: str = Field(max_length=200, description="Who made the deck at that link (not blank)")

    @field_validator("source_author")
    @classmethod
    def _not_blank(cls, author: str) -> str:
        author = author.strip()
        if not author:
            raise ValueError("source_author must not be blank")
        return author


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


class NamedQuantity(BaseModel):
    name: str
    quantity: int


class OwnedPrinting(BaseModel):
    set: str
    collector_number: str
    printing: str
    finish: str
    quantity: int
    unit_price: float


class CoverageLine(BaseModel):
    name: str
    section: str = Field("main", description="Where the card sits in the list: commander, main, sideboard, maybeboard, companion")
    set: str | None = None
    number: str | None = None
    need: int
    have: int
    missing: int
    status: Literal["owned", "partial", "missing"]
    unit_price: float | None = Field(None, description="Cheapest known USD market price of the card (of the line's "
                                     "printing when it names one the Vault knows), any finish; null when unknown")
    price_date: str | None = Field(None, description="The day unit_price is from (the price snapshot it was read from; Scryfall's "
                                   "figure, not a shop's today); null when the price is unknown")
    missing_cost: float | None = Field(None, description="unit_price times missing; null when no price is known")
    owned_printings: list[OwnedPrinting] = Field([], description="The card's printings in the collection (first 50)")
    maybe_owned: list[NamedQuantity] = Field([], description="On a line you own none of: cards in the collection whose "
                                             "name differs only in accents, punctuation, case or an Alchemy 'A-' prefix")


class Coverage(BaseModel):
    cards: list[CoverageLine]
    cards_total: int | None = Field(None, description="With ?detail=summary: how many cards the deck has")
    fully_owned: int | None = Field(None, description="With ?detail=summary: how many of them are fully owned")
    shown: str | None = Field(None, description="With ?detail=summary: which lines `cards` holds, and how to get the rest")
    unparsed: list[str]
    missing_cost: float | None = Field(None, description="What the missing copies cost at the cheapest known prices")
    missing_unpriced: int | None = Field(None, description="Lines with missing copies and no known price")
    priced_as_of: str | None = Field(None, description="The day the prices are from (Scryfall's, not a shop's today)")


class DeckSummary(BaseModel):
    need: int = Field(description="Copies the deck needs")
    have: int = Field(description="Of those, copies you own (at most what each line needs)")
    missing: int = Field(description="Copies missing")
    missing_cost: float | None = Field(None, description="What the missing copies cost at the cheapest known prices")
    missing_unpriced: int | None = Field(None, description="Lines with missing copies and no known price")


class DeckOverview(BaseModel):
    format: str | None = Field(None, description="commander, standard, ... or null when unknown")
    format_from: str | None = Field(None, description="'set on the deck', or 'the list names a commander' (a reading, not a fact)")
    commanders: list[str] = Field(default_factory=list, description="The commander(s): partners and backgrounds are several")
    cards: int | None = Field(None, description="Cards in the deck (commanders included; sideboard and maybeboard apart)")
    sideboard: int | None = None
    maybeboard: int | None = None
    companion: int | None = None
    color_identity: str | None = Field(None, description="The commanders' colour identity, WUBRG order (Scryfall's Oracle data)")
    note: str | None = None


class Deck(Hal):
    # Field order is answer order: who the deck is first (#216, #232), the decklist last.
    id: int
    name: str
    format: str | None = Field(None, description="The format stored on the deck, if any")
    overview: DeckOverview | None = Field(None, description="Format, commander(s), card count and colour identity at a glance")
    summary: DeckSummary | None = Field(None, description="Owned, missing and cost to finish, against your collection (list with ?summary=true, and a deck with ?detail=summary)")
    source_url: str | None = None
    source: str | None = Field(None, description="Where the deck came from: archidekt, moxfield, link (another address) or pasted")
    source_author: str | None = None
    credit: dict | None = Field(None, description="For a deck from Archidekt: its source, link, author, `fetched_at` (when the "
                                "Vault last took the list from that link) and the notice to repeat")
    coverage: Coverage | None = None
    text: str | None = Field(None, description="The decklist (left out of the AI tools' brief deck list)")
    created_at: str
    updated_at: str
    from_: str | None = Field(None, alias="from", description="Who shared it (shared decks only)")


class DeckPage(Page):
    items: list[Deck]
    closest: list[str] | None = Field(None, description="With ?q= and no match: the nearest deck names")


class AuthorRecorded(Deck):
    recorded: bool = Field(description="False when the deck has another link or already has an author")


class ShareIn(BaseModel):
    kind: Literal["collection", "deck"]
    deck_id: int | None = Field(None, ge=1, le=MAX_ID)
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

    @model_validator(mode="after")
    def _one_complete_form(self) -> "CardIdentifier":
        if not (self.id or self.name or (self.set and self.collector_number)):
            raise ValueError("needs an id, a name, or both set and collector_number")
        return self


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
    alias_prefixes: list[str] = Field(default_factory=list,
                                      description="Any other code starting with one of these stands for its first three characters")


# -- analytics (computed in Postgres, vault.analytics) --------------------------------------------

class Counts(BaseModel):
    copies: int
    printings: int = Field(description="Distinct printings (as /collection/cards groups them)")
    market_value: float


class Bucket(Counts):
    key: str
    label: str


class MatrixCell(Counts):
    color: str
    type: str


class Breakdowns(Hal):
    totals: Counts
    colors: list[Bucket] = Field(description="By colour identity: W, U, B, R, G, M (multicolour), C (colourless), unknown")
    types: list[Bucket] = Field(description="By main card type of the front face (see docs/api.md), or unknown")
    mana_values: list[Bucket] = Field(description="By mana value: 0 to 7, then 8+, or unknown")
    rarities: list[Bucket]
    matrix: list[MatrixCell] = Field(description="Colour x main type")


class ValuationMonth(BaseModel):
    month: str
    copies: int
    market: float = Field(description="Today's market value of the copies bought that month")
    paid: float | None = Field(None, description="Spent that month; null when costs are hidden")
    copies_cum: int
    market_cum: float
    cost_cum: float | None = None
    gain_cum: float | None = Field(None, description="market_cum - cost_cum (every dated copy)")
    known_gain_cum: float | None = Field(None, description="Running P&L: copies with a known cost only")


class ValuationTotals(BaseModel):
    copies: int
    market: float
    cost: float | None = None
    gain: float | None = None
    known_gain: float | None = None


class ValuationPeak(BaseModel):
    month: str
    market: float
    copies: int


class Trailing(BaseModel):
    since: str
    until: str
    copies: int
    market: float
    paid: float | None = None


class Undated(BaseModel):
    copies: int
    market: float


class Valuation(Hal):
    months: list[ValuationMonth]
    totals: ValuationTotals
    peak: ValuationPeak | None = Field(None, description="The month whose copies are worth the most today")
    trailing_12m: Trailing | None = Field(None, description="The 12 calendar months up to the latest month bought")
    undated: Undated = Field(description="Copies with no purchase date: in the market value, not in the months")
    costs_hidden: bool


class NameItem(Hal):
    name: str
    copies: int
    market_value: float
    unit_price: float = Field(description="market_value / copies")
    printings: int
    sets: list[str]
    color: str = Field(description="W, U, B, R, G, M, C, or unknown")
    color_identity: list[str] = []
    type: str = Field(description="Main card type, or unknown")
    type_line: str | None = None
    cmc: float | None = None
    rarity: str | None = None
    image: CardImage | None = Field(None, description="The most valuable printing's image, with its artist")
    scryfall_id: str | None = None


class NamePage(Page):
    items: list[NameItem]


class RefreshIn(BaseModel):
    cursor: str | None = Field(None, max_length=36, description="The `cursor` from the previous call's answer")
    force: bool = Field(False, description="Refresh printings that already have today's price too")


class RefreshProgress(Hal):
    done: int
    total: int = Field(description="Printings in the collection")
    remaining: int = Field(description="Printings still to refresh: call again (with `cursor`) until 0")
    processed: int = Field(description="Printings fetched from Scryfall by this call")
    not_found: int = Field(0, description="Printings Scryfall didn't know, in this call")
    unmatched_rows: int = Field(description="Rows not matched to a printing yet (the daily sync matches them)")
    cursor: str | None = Field(None, description="Send it back to continue; null when finished")
    unavailable: bool = Field(False, description="Scryfall didn't answer part of this call: wait, then call again")
    prices_as_of: str | None = None
    version: str = Field(description="The collection's version after this call")
