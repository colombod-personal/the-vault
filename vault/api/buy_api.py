"""Where to buy (#212, docs/where-to-buy-design.md): the links of the menu for a card, and the person's own settings for it.

``GET /buy/menu?card=`` builds the plain links (shops in the order for the person's country, the stores they typed, the Wizards store
locator, Scryfall). ``GET`` and ``PUT`` and ``DELETE`` ``/me/buy-settings`` hold the country they chose and up to three stores they
typed; ``GET /buy/countries`` lists the countries to choose from. Nothing here contacts a shop or the locator, shows a price, or reads
the request's address: a country the Vault does not know is "not set", never a guess. The settings are the person's own: account
sessions only (a personal token or a connected app can read the menu, which is built from them, but cannot change or read the raw settings).
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .. import buy_links
from ..models import BuySettings, IdempotentRequest, User, utcnow
from ..provenance import Provenance
from ..ratelimit import per_user
from .hal import link, page_body, paginate
from .idempotency import idempotent
from .schemas import Hal, Page

V1 = "/api/v1"


class StoreIn(BaseModel):
    name: str = Field(max_length=buy_links.NAME_MAX + 200, description=f"What the store is called, at most {buy_links.NAME_MAX} characters")
    url: str = Field(max_length=buy_links.URL_MAX + 200, description="The store's web address: https only, a real host name, no user name or password")
    search_url: str | None = Field(None, max_length=buy_links.URL_MAX + 200, description=(
        f"Optional: the store's own search address with {buy_links.PLACEHOLDER} where the card's name goes (copy it from the store's search page). "
        "Without it the menu opens the store's page"))


class BuySettingsIn(BaseModel):
    country: str | None = Field(None, max_length=10, description="Two letters (ISO 3166-1), for example GB; null or empty clears it. Leave the field out to keep what is saved")
    stores: list[StoreIn] | None = Field(None, max_length=buy_links.MAX_STORES + 5, description=f"At most {buy_links.MAX_STORES} stores, replacing the saved ones; [] removes them. Leave the field out to keep what is saved")


class StoreOut(BaseModel):
    id: str
    name: str
    url: str = Field(description="The web address as typed")
    search_url: str | None = Field(None, description="The search address as typed, with {card}")
    host: str | None = None


class BuyLimits(BaseModel):
    stores: int
    name: int
    address: int
    placeholder: str


class BuySettingsOut(Hal):
    country: str | None = Field(description="The country the person chose, or null: the Vault never guesses it from an address")
    country_name: str | None = None
    stores: list[StoreOut]
    limits: BuyLimits
    updated_at: str | None = None


class CountryItem(BaseModel):
    code: str
    name: str
    region: str = Field(description="Which shop order the country gets: GB, US, EU (the EU, the EEA and Switzerland) or other")
    shops: list[str] = Field(description="The shops first for this country, by id")


class CountryPage(Page):
    items: list[CountryItem]


class MenuRow(BaseModel):
    id: str
    name: str
    host: str | None = None
    url: str = Field(description="A link to open in a new tab: the shop's own search for the card, or the store's page")
    opens: str
    typed_by_you: bool
    link_format_checked: str | None = Field(None, description="The day the format of this link was checked by hand")
    terms: str | None = Field(None, description="The page whose terms were read for this shop")


class MenuLink(BaseModel):
    name: str
    url: str
    opens: str
    terms: str | None = None
    link_format_checked: str | None = None


class BuyMenu(Hal):
    card: str
    searched_as: str = Field(description="The name the links search for: the front face of a double-faced card")
    country: str | None = None
    country_name: str | None = None
    region_note: str
    my_stores: list[MenuRow] = Field(description="The stores the person typed, first, marked typed_by_you")
    shops: list[MenuRow] = Field(description="The shops for the person's country, in order")
    more_shops: list[MenuRow] = Field(description="Every other shop")
    locator: MenuLink = Field(description="The official Wizards store locator: its own page, not a search")
    scryfall: MenuLink
    notice: str
    prices: str
    region_orders: dict[str, list[str]] | None = Field(None, description="Only when no country is set: the shop order for each region, for a browser to pick by its own language")
    region_notes: dict[str, str] | None = None
    europe_countries: list[str] | None = None
    provenance: list[Provenance]


def build_router(get_db, current_user, account_user) -> APIRouter:
    router = APIRouter(prefix=V1)

    def stored(db: Session, user: User) -> tuple[str | None, list[dict], str | None]:
        row = db.get(BuySettings, user.id)
        return (row.country, list(row.stores or []), row.updated_at.isoformat() if row else None) if row else (None, [], None)

    def settings_body(db: Session, user: User) -> dict:
        country, stores, updated = stored(db, user)
        body = {**buy_links.settings_view(country, stores), "updated_at": updated,
                "_links": {"self": link(f"{V1}/me/buy-settings"), "menu": link(f"{V1}/buy/menu", title="Add ?card=NAME"),
                           "countries": link(f"{V1}/buy/countries"), "me": link(f"{V1}/me")}}
        return BuySettingsOut.model_validate(body).model_dump(by_alias=True)  # the same shape from GET and PUT

    @router.get("/me/buy-settings", tags=["buy"], response_model=BuySettingsOut, response_model_by_alias=True,
                summary="Where you buy: the country you chose and the stores you typed (yours only)")
    def get_settings(user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        return settings_body(db, user)

    @router.put("/me/buy-settings", tags=["buy"], response_model=BuySettingsOut, response_model_by_alias=True,
                summary="Save where you buy: a country and up to three stores you typed. A field you leave out keeps its saved value")
    def put_settings(request: Request, body: BuySettingsIn, user: User = Depends(account_user), db: Session = Depends(get_db)):
        per_user(request, "buy settings", user.id, 30, db)
        sent = body.model_fields_set
        try:
            country = buy_links.clean_country(body.country) if "country" in sent else None
            stores = buy_links.clean_stores([s.model_dump() for s in body.stores]) if "stores" in sent and body.stores is not None else None
        except buy_links.BuyError as exc:
            raise HTTPException(422, str(exc)) from None

        def run() -> dict:
            if not (sent & {"country", "stores"}):
                return settings_body(db, user)  # nothing to save: no row is made
            db.execute(pg_insert(BuySettings).values(user_id=user.id, country=None, stores=[], updated_at=utcnow()).on_conflict_do_nothing())
            row = db.scalars(select(BuySettings).where(BuySettings.user_id == user.id).with_for_update()).one()
            if "country" in sent:
                row.country = country
            if stores is not None:
                row.stores = stores
            row.updated_at = utcnow()
            db.flush()
            return settings_body(db, user)

        # The stored answer of a retried save holds no country and no store (it would outlive "Remove"): a replay is rebuilt from the live row.
        return idempotent(request, db, user, 200, run, redact=lambda body: {"saved": True}, replay=lambda stored: settings_body(db, user))

    @router.delete("/me/buy-settings", tags=["buy"], summary="Remove everything saved about where you buy")
    def delete_settings(request: Request, user: User = Depends(account_user), db: Session = Depends(get_db)) -> dict:
        per_user(request, "buy settings", user.id, 30, db)
        row = db.get(BuySettings, user.id)
        if row is not None:
            db.delete(row)
        db.execute(delete(IdempotentRequest).where(IdempotentRequest.user_id == user.id, IdempotentRequest.endpoint == f"PUT {V1}/me/buy-settings"))
        db.commit()
        return {"deleted": row is not None}

    @router.get("/buy/countries", tags=["buy"], response_model=CountryPage, response_model_by_alias=True,
                summary="The countries a person can choose where they buy, with the shops each gets first")
    def countries(request: Request, cursor: str | None = None, limit: Annotated[int | None, Query(ge=1, le=500)] = None,
                  user: User = Depends(current_user)) -> dict:
        rows = [{"code": code, "name": name, "region": buy_links.region_of(code), "shops": buy_links.order_for(code)[0]}
                for code, name in buy_links.COUNTRIES.items()]
        page, nxt = paginate(rows, lambda c: (c["name"].lower(),), lambda c: c["code"], cursor=cursor, limit=limit)
        return page_body(request, page, nxt, len(rows), limit=limit)

    @router.get("/buy/menu", tags=["buy"], response_model=BuyMenu, response_model_by_alias=True,
                summary="The 'Where to buy' links for a card: plain links to shops, in the order for your country; no prices, nothing is fetched")
    def get_menu(request: Request, card: Annotated[str, Query(min_length=1, max_length=buy_links.CARD_NAME_MAX, description="The card's name (a double-faced card is searched by its front face)")],
                 user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
        country, stores, _ = stored(db, user)
        try:
            body = buy_links.menu(card, country, stores)
        except buy_links.BuyError as exc:
            raise HTTPException(422, str(exc)) from None
        ids = [r["id"] for r in body["shops"] + body["more_shops"]]
        return {**body, "provenance": buy_links.provenance_for(ids),
                "_links": {"self": link(f"{V1}/buy/menu?" + urlencode({"card": card})), "settings": link(f"{V1}/me/buy-settings"),
                           "countries": link(f"{V1}/buy/countries")}}

    return router
