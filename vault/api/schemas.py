"""Typed request/response models for /api/v1 (they drive the OpenAPI document, which can
generate the iOS client, e.g. with Apple's swift-openapi-generator)."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..deck_tools import FORMATS
from ..provenance import Provenance


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


class TagRow(BaseModel):
    tag: str
    source: str = Field(description="Who wrote this tag on the card: person, assistant or system")
    source_detail: str | None = Field(None, description="For an assistant, the app that wrote it; never shown as the person's own")


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
    tags: list[str] | None = Field(None, description="Your tags on the card (GET /collection/tags); left out of a shared collection, "
                                   "where tags are private")
    tags_detail: list[TagRow] | None = Field(None, description="The same tags with who wrote each (`source`, and the app's name in "
                                             "`source_detail` for an assistant), so a client can mark an assistant's tag as its own; "
                                             "left out of a shared collection")


class PricePoint(BaseModel):
    day: str
    price: float | None = None


class CopyRow(BaseModel):
    quantity: int
    folder: str | None = None
    bucket_id: int | None = Field(None, description="The bucket these copies are in (GET /collection/buckets); a printing can be in several")
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


class HistorySummary(BaseModel):
    """The market value over the requested range (`since` to the last day), computed by the server. It carries no cost:
    history's market value covers every holding while cost exists only for rows with a price paid recorded, so setting
    them side by side would mix two populations (`GET /collection/pnl` compares one cohort)."""
    model_config = ConfigDict(populate_by_name=True)
    from_: str | None = Field(None, alias="from", description="The first day with a value in the range (null when there is none)")
    to: str | None = Field(None, description="The last day with a value in the range")
    market_start: float | None = None
    market_end: float | None = None
    market_change: float | None = Field(None, description="market_end - market_start")


class HistoryPage(Page):
    items: list[DayValue]
    summary: HistorySummary = Field(description="Over every day in the range, not just this page")


# -- account, tokens -----------------------------------------------------------------------------

class Me(Hal):
    id: int
    name: str | None = None
    email: str | None = None
    providers: list[str]


class RecentSignInEmail(BaseModel):
    available: bool = Field(description="A code can be e-mailed: the Vault has a mail sender, the account has an address, and the caller is a browser session")
    to: str | None = Field(None, description="The address a code would go to, masked (***@e***.com); null when not available")
    reason: Literal["app", "no_sender", "no_address", "relay_unregistered"] | None = Field(
        None, description="Why not: the caller is an app (it signs in again instead); the Vault has no mail sender configured "
                          "(RESEND_API_KEY); the account has no address a provider vouches for that has been on it for 24 hours")


class RecentSignIn(Hal):
    fresh: bool = Field(description="This session signed in within `window_seconds`: delete, export, tokens, and adding or removing sign-in methods are allowed")
    seconds_left: int
    window_seconds: int
    email: RecentSignInEmail


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
    scopes: list[str] = Field(description="The most any of its connections was allowed: read, and write if any of them was given it")
    connections: int = Field(description="How many times the app is connected (each device or re-add is one); all of them share this row")
    connection_ids: list[int] = Field(description="The ids of those connections; DELETE on this row or any of them disconnects them all")
    created_at: str = Field(description="When the app was first connected")
    last_used_at: str | None = Field(None, description="When any of its connections last acted")
    idle: bool = Field(description="True when none of its connections was used for 14 days")
    used_minutes_ago: int | None = Field(None, description="Whole minutes since any connection acted, only when that was within the last hour (the Account page names it when you disconnect; the Vault records use at most every 5 minutes)")
    idle_connections: int = Field(description="How many connections were not used for 14 days; a connection nobody refreshes is removed after 30")


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
    kind: str = Field("import", description="import (a file), assistant (edits made through an assistant), undo, move (copies moved "
                                            "between buckets), reset (the collection or one bucket emptied) or reset_undo")
    app: str | None = Field(None, description="For assistant edits: the app that made them")
    lines: list[dict] | None = Field(None, description="For assistant edits: each printing, copies before and after")
    merge: dict | None = Field(None, description="For imports: how the file was applied (mode, what came from the person's app, "
                                                 "which edits made in the Vault were kept, the conflicts and their answers)")
    bucket: dict | None = Field(None, description="For an import into one bucket: its id and name; every other bucket was left alone")
    undoable: bool | None = Field(None, description="For assistant edits: true on the one change set that can be undone now "
                                  "(the latest, until the collection changes again); POST /collection/changes/undo")
    undone: bool | None = Field(None, description="For assistant edits and resets: true once it was undone")
    reset: dict | None = Field(None, description="For a reset or its undo: the scope, what was removed (rows, copies, market value, "
                               "tags, notes, history entries) and whether it can be undone")


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
    printings_page: int = Field(1, ge=1, le=20, description="The next page of printings to choose from, when the answer says there are more")


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
    last_change: dict | None = Field(None, description="What changed between the deck's previous saved version and its current list "
                                     "(`changes`, `summary`, when, from what); absent until the deck has two versions (#93)")
    created_at: str
    updated_at: str
    from_: str | None = Field(None, alias="from", description="Who shared it (shared decks only)")


class DeckVersionEntry(BaseModel):
    id: int
    created_at: str
    source: str = Field(description="saved, edited, imported or refreshed")
    cards: int
    changes: list[dict] | None = Field(None, description="Cards whose count changed from the version before; null for the oldest kept")
    summary: dict | None = None


class DeckVersions(Hal):
    deck_id: int
    keep: int = Field(description="Versions kept per deck; the oldest are dropped first")
    total: int
    items: list[DeckVersionEntry]


class DeckSeenIn(BaseModel):
    text: str | None = Field(None, max_length=50_000, description="The list the page showed (for a deck from a link: the source's "
                                                                  "current list); recorded as a version when its cards differ from the latest")


class DeckSeen(BaseModel):
    recorded: bool = Field(description="True when the list sent differed from the latest version and was recorded")
    since_last_looked: dict | None = Field(None, description="What changed since the person last opened the deck (`changes`, "
                                                              "`summary`, `since`); null the first time or when nothing changed")


class DeckVersionText(Hal):
    id: int
    deck_id: int
    created_at: str
    source: str
    text: str


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


class SignInMethodItem(Hal):
    id: int = Field(description="The row's id within its kind: a passkey id (DELETE /me/passkeys/{id}) or a linked provider's id (DELETE /me/identities/{id}); the two can be equal")
    kind: Literal["passkey", "provider"]
    provider: str = Field(description="passkey, google, microsoft, apple, facebook (or dev on a local server)")
    name: str = Field(description="What the person sees: the passkey's name, or the provider's")
    created_at: str = Field(description="When it was added to this account (a provider moved over from an empty account counts from the move)")
    last_used_at: str | None = Field(None, description="Passkeys only")
    recently_added: bool = Field(description="Added in the last `recent_hours` hours")
    added_minutes_ago: int
    removable: bool = Field(description="False when removing it would be refused (409)")
    removable_reason: Literal["only_method", "provider_too_old", "needs_older_method", "recent_sign_in_required"] | None = Field(
        None, description="Why not: it is the only way to sign in; a provider linked more than `recent_hours` ago is not unlinked "
                          "here; no OTHER method older than `recent_hours` would remain; a passkey added more than `recent_hours` "
                          "ago and this session has not signed in recently (GET /me/recent-sign-in, then confirm it's you)")


class SignInMethodPage(Page):
    items: list[SignInMethodItem]
    recent_hours: int = Field(description="The window `recently_added` and `recent_only` use")
    recent_count: int = Field(description="How many sign-in methods were added inside that window, on every page")
    recent_removable: bool = Field(description="True when DELETE /me/sign-in-methods/recent would succeed: something recent, and a "
                                               "method older than the window would stay")


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


# -- the Lab: spare copies, profit and loss (docs/lab-design.md) ---------------------------------------------

class SparePrinting(Hal):
    id: str = Field(description="The printing group's id, the same as `GET /collection/cards` items")
    scryfall_id: str | None = Field(None, description="Null for a copy the importer could not match to a printing")
    set: SetRef
    collector_number: str
    printing: str
    finish: str
    condition: str
    language: str
    spare_quantity: int
    unit_price: float | None = Field(None, description="Null when the Vault has no price for this printing")
    price_status: Literal["priced", "unpriced"]
    trade_marked_quantity: int = Field(description="Of the spare copies, how many are marked for trade")
    scryfall_link: str | None = Field(None, description="That printing's Scryfall page; null when the Vault holds no card data for it")
    scryfall_search: str = Field(description="A Scryfall search by the card's name (what to use when `scryfall_link` is null)")


class SpareName(Hal):
    name: str
    have: int = Field(description="All copies owned, any printing")
    needed: int = Field(description="Copies the saved decks need together, summed across decks")
    spare: int = Field(description="max(0, have - needed); only names above 0 are listed")
    market_value_of_spare: float
    priced_copies: int
    unpriced_copies: int = Field(description="Spare copies with no price: counted in `spare`, worth nothing in the value")
    deck_count: int = Field(description="How many saved decks use the card")
    printing_rows_total: int
    printings: list[SparePrinting] = Field(description="At most 10 printing rows, cheapest first (the allocation order); "
                                           "`_links.printings` pages all of them")


class SpareSummary(BaseModel):
    names: int
    copies: int
    market_value: float
    priced_copies: int
    unpriced_copies: int


class SparePage(Page):
    items: list[SpareName]
    status: Literal["ok", "no_decks", "empty_collection"]
    note: str
    summary: SpareSummary = Field(description="Over every spare card, whatever the page size")
    decks_analysed: int
    decks_skipped: int = Field(description="Saved decks the Vault could not read; their cards are not counted as needed")
    prices_as_of: str | None = None


class SparePrintingPage(Page):
    items: list[SparePrinting]
    name: str
    status: Literal["ok", "no_decks", "empty_collection"]
    have: int
    needed: int
    spare: int
    market_value_of_spare: float
    prices_as_of: str | None = None


class PnlItem(Hal):
    id: str
    name: str
    set: SetRef
    collector_number: str
    printing: str
    finish: str
    condition: str
    language: str
    quantity: int = Field(description="All copies of the printing")
    copies: int = Field(description="Copies with a price paid recorded: the ones paid, market_value and gain are about")
    paid: float
    unit_price: float = Field(description="Today's market price of one copy")
    market_value: float = Field(description="unit_price times copies")
    gain: float = Field(description="market_value - paid")
    gain_pct: float | None = None
    scryfall_id: str | None = None
    scryfall_link: str | None = None
    scryfall_search: str


class PnlSummary(BaseModel):
    biggest_gain: PnlItem | None = Field(None, description="Null when no counted holding is above cost")
    biggest_loss: PnlItem | None = Field(None, description="Null when no counted holding is below cost")
    net_gain: float | None = Field(None, description="The sum of the gains of the counted holdings; null when none is counted")
    total_copies: int
    covered_copies: int = Field(description="Copies counted: a price paid and a current market price are both known")
    unknown_cost_copies: int = Field(description="No price paid recorded (a copy with neither is counted here)")
    unpriced_market_copies: int = Field(description="A price paid but no current market price")
    reason: str | None = Field(None, description="Why there is nothing to show; null while some holding is counted")


class PnlPage(Page):
    items: list[PnlItem]
    side: Literal["winners", "losers"]
    summary: PnlSummary = Field(description="The same on every page and on both sides")
    prices_as_of: str | None = None


# -- deck independence (#165, docs/deck-independence.md) ------------------------------------------------------

class OverlapDeckRef(BaseModel):
    id: int
    name: str


class OverlapHold(BaseModel):
    card: str
    quantity: int = Field(description="Copies of this contested card the deck is given (`gets`): another deck wants them too")
    also_wanted_by: list[str] = Field(description="Names of the other decks that use the card (at most 10; `also_wanted_by_total` counts them)")
    also_wanted_by_total: int


class OverlapLack(BaseModel):
    card: str
    quantity: int = Field(description="Copies this deck lacks under the allocation: `not_owned` + `held_by_other_deck`")
    not_owned: int = Field(description="Copies the collection could not supply even if this deck took every owned copy: buy them whatever the other decks do")
    held_by_other_deck: int = Field(description="Copies that exist in the collection but were given to another deck: available by moving")
    unit_price: float | None = Field(None, description="The cheapest known USD price of one copy (Scryfall's); null when unknown")
    price_date: str | None = Field(None, description="The day `unit_price` is from")
    cost: float | None = Field(None, description="`unit_price` times `quantity`; null when unpriced")


class OverlapDeck(Hal):
    id: int
    name: str
    order: int = Field(description="Position in the allocation order (1 takes contested copies first); skipped decks come last")
    status: Literal["analysed", "skipped"]
    reason: str | None = Field(None, description="Why a deck was skipped (its saved list could not be read)")
    need: int | None = Field(None, description="Copies the deck needs, basic lands left out")
    free: int | None = Field(None, description="Of those, copies of cards that are not contested")
    holds: list[OverlapHold] = Field(default_factory=list, description="Contested cards the deck is given (at most 100 listed)")
    holds_total: int | None = None
    lacking: list[OverlapLack] = Field(default_factory=list, description="Cards the deck lacks under the allocation (at most 100 listed)")
    lacking_total: int | None = None
    stands_alone: bool | None = Field(None, description="True when the deck lacks nothing under the allocation (complete)")
    independent: bool | None = Field(None, description="True when it also holds no contested card, so no other deck's completeness depends on it")
    independence: float | None = Field(None, description="`free` / `need`, for sorting only; 1.0 for a deck with nothing to count")
    cost_to_complete: float | None = Field(None, description="What the lacking copies cost at the cheapest known prices, the other decks keeping theirs")
    cost_unpriced: int | None = Field(None, description="Lacking cards with no known price (not in `cost_to_complete`)")


class OverlapOption(BaseModel):
    kind: Literal["move", "buy"]
    effect: str = Field(description="What the option does, in words")
    quantity: int
    from_deck: OverlapDeckRef | None = Field(None, description="move: the deck that gives up a copy")
    to_deck: OverlapDeckRef | None = Field(None, description="move: the deck that gets it")
    unit_price: float | None = None
    cost: float | None = Field(None, description="buy: the cost to finish every deck for this card, counted once")
    price_status: Literal["priced", "unpriced"] | None = None
    price_date: str | None = None


class OverlapContestedDeck(BaseModel):
    deck_id: int
    deck: str
    need: int
    gets: int
    lacking: int


class OverlapContested(BaseModel):
    card: str
    have: int
    need_for_all: int
    global_deficit: int = Field(description="`need_for_all` - `have`: the decks' lacking copies add up to exactly this, whatever the order")
    decks: list[OverlapContestedDeck] = Field(description="The decks that use the card, in allocation order (at most 25)")
    decks_total: int
    options: list[OverlapOption] = Field(description="`move` (only when a deck lacks a copy another deck holds) and `buy`")


class OverlapPurchase(BaseModel):
    card: str
    have: int
    need_for_all: int
    global_deficit: int
    unit_price: float | None = None
    cost: float | None = Field(None, description="`global_deficit` times `unit_price`; null when unpriced")
    price_status: Literal["priced", "unpriced"]
    price_date: str | None = None
    contested: bool = Field(description="True when some copies are owned but short")
    move: OverlapOption | None = Field(None, description="For a contested card: the move that would give a lacking deck a copy")


class OverlapAllocation(BaseModel):
    rule: Literal["closest_to_complete", "priority"]
    priority_applied: bool
    description: str
    limits: str


class OverlapSummary(BaseModel):
    decks_analysed: int
    decks_needing_purchase: int = Field(description="Analysed decks that do not stand alone under the allocation")
    finish_all_cost: float = Field(description="What it costs to finish every deck, each card counted once (priced cards only)")
    unpriced: int = Field(description="Cards to buy with no known price (not in `finish_all_cost`)")
    contested_cards: int
    cards_to_buy: int


class OverlapDeckLine(BaseModel):
    deck_id: int
    deck: str
    quantity: int


class OverlapCard(BaseModel):
    name: str
    decks: list[OverlapDeckLine] = Field(description="Up to 10 of the decks that use the card; `decks_total` counts them")
    decks_total: int
    need_for_all: int
    have: int
    short: int


class DeckOverlap(Hal):
    decks_checked: int = Field(description="Saved decks, readable or not")
    shared_cards: int
    short_cards: int
    cards: list[OverlapCard] = Field(description="Cards in more than one deck (the first 200), the copies short first")
    note: str
    decks_analysed: int
    decks_skipped_count: int
    summary: OverlapSummary
    allocation: OverlapAllocation
    prices_date: str | None = Field(None, description="The day of the newest price used (Scryfall's, not a shop's today)")
    decks: list[OverlapDeck] = Field(description="The first page of decks; `_links.next` pages the rest")


class OverlapDeckPage(Page):
    items: list[OverlapDeck]
    allocation: OverlapAllocation
    prices_date: str | None = None


class OverlapContestedPage(Page):
    items: list[OverlapContested]
    allocation: OverlapAllocation
    prices_date: str | None = None


class OverlapPurchasePage(Page):
    items: list[OverlapPurchase]
    allocation: OverlapAllocation
    prices_date: str | None = None


class OverlapPurchaseText(Page):
    format: Literal["text"]
    text: str = Field(description="One page of the paste-ready list: `<copies> <card>` per line; join the pages")
    allocation: OverlapAllocation
    prices_date: str | None = None


# -- the deck ideas lab (#163, docs/deck-ideas-lab-design.md) ---------------------------------------------------------------

class IdeasBuy(BaseModel):
    quantity: int = Field(description="Copies to buy")
    unit_price: float | None = Field(None, description="Scryfall's cheapest known price, not a shop's price today")
    price_date: str | None = Field(None, description="The day that price is from")
    cost: float | None = None
    price_status: Literal["priced", "unpriced"]


class IdeasMove(BaseModel):
    kind: Literal["move"]
    from_deck: OverlapDeckRef = Field(description="The deck that holds a copy (the last in the allocation order that does)")
    quantity: int


class IdeasRole(BaseModel):
    role: str
    strength: Literal["core", "incidental"]
    basis: Literal["scryfall_tagger", "computed"] = Field(description="A Scryfall Tagger tag, or a rule over the Oracle text (the Vault's, always marked)")


class FineRole(BaseModel):
    """One of the Vault's 22 roles on a card (docs/functional-equivalents.md section 5): found by a written rule over the Oracle text."""
    role: str = Field(description="The stable slug, e.g. `token-doubler`, `draw-engine`, `free-counterspell`, `bounce`")
    name: str = Field(description="The plain name a player recognises")
    means: str = Field(description="What the role means, in the Vault's own words")
    strength: Literal["core", "incidental"] = Field(description="`core`: the card exists to do this; `incidental`: it does it on the side")
    basis: Literal["computed"] = Field(description="Always `computed`: a rule over the Oracle text, never a Scryfall tag and never an assistant's word")
    rule: str = Field(description="The id of the rule that found it (documented in docs/functional-equivalents.md)")
    repeatable: bool | None = Field(None, description="For `treasure` and `token-maker` only: does it happen again and again (true) or once (false)")


class IdeasCard(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    card: str
    oracle_id: str | None = Field(None, description="Null when the catalog does not know the name")
    known: bool
    section: str
    lane: str
    need: int = Field(description="Copies the deck lists")
    have: int = Field(description="Copies owned (any printing), whatever the other decks hold")
    gets: int = Field(description="Copies this deck holds when every saved deck takes what it needs (the allocation of get_deck_overlap)")
    not_owned: int = Field(description="Lacking copies the collection cannot supply: to buy")
    held_by_other_deck: int = Field(description="Lacking copies that exist and another deck holds: to move")
    status: Literal["owned", "partial", "missing"] = Field(description="owned: nothing lacking; missing: the deck holds none; partial: some")
    basic: bool = Field(description="A basic land: never short")
    borrowed: bool = Field(description="True when this deck lacks a copy another deck holds, or holds a copy another deck also wants")
    borrowed_from: str | None = Field(None, description="The deck holding the copy this deck lacks (when `held_by_other_deck` is above 0)")
    borrowed_from_deck_id: int | None = None
    also_wanted_by: list[str] = Field(default_factory=list, description="Decks that also want a copy this deck holds (at most 10)")
    type_line: str | None = None
    mana_cost: str | None = None
    mana_value: float | None = None
    roles: list[IdeasRole]
    tags: list[str] = Field(description="The card's roles other than its lane: it is counted once, in one lane")
    move: IdeasMove | None = Field(None, description="Offered only when a donor copy exists (`held_by_other_deck` above 0)")
    buy: IdeasBuy | None = Field(None, description="Offered only when copies must be bought (`not_owned` above 0)")
    links: dict[str, Link] = Field(default_factory=dict, alias="_links")


class IdeasLane(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    lane: str
    label: str
    kind: str
    copies: int = Field(description="Copies in the lane: the lanes add up to the deck's card count")
    cards: int
    covered: int
    missing: int = Field(description="Cards in the lane with `not_owned` above 0")
    borrowed: int = Field(description="Cards in the lane with `held_by_other_deck` above 0")
    items: list[IdeasCard] = Field(description="One page of the lane: cards that need a decision first, then by name")
    count: int
    total: int = Field(description="Cards in the lane")
    next_cursor: str | None = Field(None, description="The cursor of this lane's next page (the same as in `_links.next`), null on the last")
    links: dict[str, Link] = Field(default_factory=dict, alias="_links")


class IdeasSummary(BaseModel):
    copies: int = Field(description="Cards in the deck, every copy (the sum over all lanes)")
    covered: int = Field(description="Copies the deck holds under the allocation")
    lacking: int
    cards: int = Field(description="Distinct cards")
    missing: int = Field(description="Cards with `not_owned` above 0")
    borrowed: int = Field(description="Cards with `held_by_other_deck` above 0 (a card can be both missing and borrowed)")
    partial: int
    complete: bool
    unknown_cards: int = Field(description="Cards the catalog does not know (they are in Other)")


class IdeasCombo(BaseModel):
    cards: list[str]
    owned: bool = Field(description="Every card of the combo is fully held by this deck")
    url: str
    produces: list[str]
    source: str


class IdeasCombos(BaseModel):
    checked: bool
    reason: str | None = None
    total: int | None = None
    combos: list[IdeasCombo] = Field(default_factory=list)


class DeckIdeas(Hal):
    deck: dict = Field(description="Which deck this is about: id, name and overview (format, commander(s), card count, colour identity)")
    summary: IdeasSummary
    allocation: OverlapAllocation
    roles_note: str
    lanes_note: str
    borrow_note: str
    lanes: list[IdeasLane] = Field(description="Every lane, or the one asked for with `lane`; each pages on its own (`_links.next`)")
    combos: IdeasCombos | None = Field(None, description="Only with include_combos=true")
    prices_date: str | None = None
    provenance: list[Provenance]


class IdeasAlternative(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    card: str
    oracle_id: str
    type_line: str | None = None
    mana_cost: str | None = None
    mana_value: float
    image: dict | None = Field(None, description="Scryfall's image link and the artist to credit")
    scryfall_uri: str | None = None
    copies_owned: int
    copies_free: int = Field(description="Owned copies no saved deck needs")
    borrowed: bool = Field(description="Every owned copy is another deck's: using it takes it from that deck")
    borrowed_from: str | None = None
    borrowed_from_deck_id: int | None = None
    tier: Literal["same_job", "similar"] = Field(description="`same_job`: it has the card's main job (and agrees on repeating or being free); "
                                                 "`similar`: it shares another job or a neighbouring one, and `different`/`lacks` say what differs")
    roles: list[FineRole] = Field(description="All the roles the Vault finds on this card, core and incidental")
    shared_roles: list[dict] = Field(description="[{role, name, target, candidate, rule}]: the core roles both cards have; `rule` is the candidate's")
    lacks: list[dict] = Field(description="[{role, name}]: core roles of the asked-for card this one does not have (\"Does not: counter doubler\")")
    extra: list[dict] = Field(description="[{role, name}]: core roles this one has that the asked-for card does not")
    different: list[dict] = Field(description="Why a `similar` card is only similar: [{kind: neighbour|lacks_refinement|repeats, target, candidate}], each a {role, name}")
    type_note: dict | None = Field(None, description="{target, candidate}: the main card types when they differ (a creature for an enchantment)")
    oracle_text: str | None = Field(None, description="Wizards of the Coast's Oracle text via Scryfall, shown beside the asked-for card's")
    why: str
    mana_value_difference: float
    mana_value_change: float | None = Field(None, description="Its mana value minus the asked-for card's (negative: it costs less); ranked and shown, never a filter")
    legal: bool = Field(description="Always true: cards illegal in the format are filtered out before ranking")
    in_colours: bool = Field(description="Always true: cards outside the colour identity are filtered out before ranking")
    in_deck: int = Field(description="Copies of it the deck already lists")
    remaining_allowance: int | None = Field(None, description="Copies the format still allows in this deck; null for any number")
    move: IdeasMove | None = Field(None, description="Offered only when a donor copy exists")
    buy: IdeasBuy | None = Field(None, description="For a borrowed card: what a copy would cost")
    links: dict[str, Link] = Field(default_factory=dict, alias="_links")


class IdeasTarget(BaseModel):
    card: str
    oracle_id: str
    in_deck: int = Field(description="Copies the deck lists (0 when the card is not in it)")
    status: Literal["owned", "partial", "missing"] | None = Field(None, description="As in the ideas answer; null when the card is not in the deck")
    need: int | None = None
    gets: int | None = None
    not_owned: int | None = None
    held_by_other_deck: int | None = None
    borrowed_from: str | None = None
    roles: list[FineRole]
    core_roles: list[str]
    primary_role: str | None = Field(None, description="The first core role in vocabulary order: the main job a `same_job` candidate must have")
    type_line: str | None = None
    mana_cost: str | None = None
    mana_value: float | None = None
    oracle_text: str | None = Field(None, description="Wizards of the Coast's Oracle text via Scryfall")
    image: dict | None = None
    scryfall_uri: str | None = None
    buy: IdeasBuy = Field(description="One copy at Scryfall's cheapest known price, dated")


class DeckAlternatives(Page):
    deck: dict
    card: IdeasTarget
    format: str
    format_from: Literal["request", "deck", "default"] = Field(description="Where the format came from: the request, the format set on the deck, or the default (commander)")
    color_identity: list[str] = Field(description="The colours alternatives must stay within")
    reason: Literal["no_role", "none_found"] | None = Field(None, description="Why the list is empty: the Vault knows no role for the card (not the same as owning nothing like it), or nothing owned fits")
    message: str | None = None
    tiers: dict[str, int] = Field(description="How many alternatives are `same_job` and how many `similar`, over the whole list (not the page)")
    filtered_out: dict[str, int] = Field(description="Owned cards that do this job but were not offered: `colour_identity`, `format`, `in_deck` (the deck already holds as many as the format allows, or every copy is this deck's own)")
    filtered_note: str | None = Field(None, description="The same counts in a sentence, null when nothing was filtered out")
    roles_version: str = Field(description="The version of the Vault's role rules these roles come from (changes whenever a rule does)")
    roles_note: str
    items: list[IdeasAlternative]
    prices_date: str | None = None
    provenance: list[Provenance]
