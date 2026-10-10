"""Where to buy: the links of the "Where to buy" menu for a card the person does not own (#212, docs/where-to-buy-design.md).

The Vault only *builds links*. It never contacts a shop or the Wizards store locator, shows no shop's price, stock or image, never
says which shop is cheapest, earns nothing from a click (no affiliate links) and never places the person from their IP address.
Every shop link comes from one template in :data:`SHOPS` and nowhere else in the code concatenates a shop address. A shop is in
the list only after its terms were read and its search-link format was checked by hand, both dated here (docs/data-sources.md,
docs/compliance.md): adding one is those two steps and a format test (tests/test_buy_links.py).

What the person can set (the ``buy_settings`` table, exported and erased with the account, docs/gdpr.md): the country they buy in
and up to three stores they typed (a name, a web address and, optionally, a search address with ``{card}``). A typed address is only
ever rendered as a link: it is checked for shape here and never requested.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote_plus, urlsplit

from . import provenance as prov

MAX_STORES = 3
NAME_MAX = 80
URL_MAX = 300
CARD_NAME_MAX = 300
PLACEHOLDER = "{card}"

FOOTER = ("Plain links to each shop's own search. The Vault does not contact these shops, show their prices, or earn from your "
          "clicks. Not affiliated with any of them.")
LOCATOR_URL = "https://locator.wizards.com/"
LOCATOR_NAME = "Wizards Store & Event Locator"
LOCATOR_TERMS = "https://company.wizards.com/tou"
LOCATOR_CHECKED = "2026-10-09"


@dataclass(frozen=True)
class Shop:
    id: str
    name: str
    template: str  # {q} is the card's front-face name, quote_plus-encoded
    terms: str     # the page whose terms were read (docs/compliance.md)
    checked: str   # the day the search-link format was checked by hand (docs/where-to-buy-design.md section 2)
    note: str      # what the dated check could and could not show

    @property
    def host(self) -> str:
        return urlsplit(self.template).hostname or ""

    def search_url(self, card: str) -> str:
        return self.template.replace("{q}", quote_plus(front_face(card)))


SHOPS: dict[str, Shop] = {s.id: s for s in (
    Shop("magicmadhouse", "Magic Madhouse", "https://magicmadhouse.co.uk/search.php?search_query={q}",
         "https://magicmadhouse.co.uk/terms-conditions/", "2026-10-09",
         "Format measured 2026-10-09 (HTTP 200, the result page listed Sol Ring); its terms mention no restriction on links."),
    Shop("cardkingdom", "Card Kingdom", "https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D={q}",
         "https://www.cardkingdom.com/static/tos", "2026-10-09",
         "Format measured 2026-10-09 (HTTP 200, the page listed Sol Ring); its terms forbid a link that suggests endorsement, so "
         "the menu says the shops are not affiliated."),
    Shop("cardmarket", "Cardmarket", "https://www.cardmarket.com/en/Magic/Products/Search?searchString={q}",
         "https://www.cardmarket.com/en/Magic/Policies/GeneralTermsAndConditions", "2026-10-07",
         "Format checked in a browser 2026-10-07 and terms (version of 20/02/2026) read that day; on 2026-10-09 its pages answered a "
         "bot check, which was not tried to get past, so nothing was re-checked."),
)}

# Shop order by the country the person buys in (design section 4.2). The second list is the "More shops" the menu collapses.
_EUROPE = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "  # the European Union
                    "IS LI NO "  # the rest of the European Economic Area
                    "CH".split())  # and Switzerland
_ORDERS = {
    "GB": ("magicmadhouse", "cardmarket"),
    "US": ("cardkingdom", "cardmarket"),
    "EU": ("cardmarket",),
    "other": ("cardmarket", "cardkingdom"),  # also the order when no country is set: the Vault does not know where they ship
}
REGION_NOTES = {
    "GB": "Magic Madhouse and Cardmarket first.",
    "US": "Card Kingdom and Cardmarket first.",
    "EU": "Cardmarket first.",
    "other": "Cardmarket and Card Kingdom first. The Vault does not know whether either ships to you.",
}

_COUNTRY_TABLE = """AD Andorra;AE United Arab Emirates;AF Afghanistan;AG Antigua and Barbuda;AI Anguilla;AL Albania;AM Armenia;AO Angola;\
AQ Antarctica;AR Argentina;AS American Samoa;AT Austria;AU Australia;AW Aruba;AX Åland Islands;AZ Azerbaijan;BA Bosnia and Herzegovina;\
BB Barbados;BD Bangladesh;BE Belgium;BF Burkina Faso;BG Bulgaria;BH Bahrain;BI Burundi;BJ Benin;BL Saint Barthélemy;BM Bermuda;\
BN Brunei;BO Bolivia;BQ Bonaire, Sint Eustatius and Saba;BR Brazil;BS Bahamas;BT Bhutan;BV Bouvet Island;BW Botswana;BY Belarus;\
BZ Belize;CA Canada;CC Cocos (Keeling) Islands;CD Congo (Democratic Republic);CF Central African Republic;CG Congo;CH Switzerland;\
CI Côte d'Ivoire;CK Cook Islands;CL Chile;CM Cameroon;CN China;CO Colombia;CR Costa Rica;CU Cuba;CV Cabo Verde;CW Curaçao;\
CX Christmas Island;CY Cyprus;CZ Czechia;DE Germany;DJ Djibouti;DK Denmark;DM Dominica;DO Dominican Republic;DZ Algeria;EC Ecuador;\
EE Estonia;EG Egypt;EH Western Sahara;ER Eritrea;ES Spain;ET Ethiopia;FI Finland;FJ Fiji;FK Falkland Islands;FM Micronesia;\
FO Faroe Islands;FR France;GA Gabon;GB United Kingdom;GD Grenada;GE Georgia;GF French Guiana;GG Guernsey;GH Ghana;GI Gibraltar;\
GL Greenland;GM Gambia;GN Guinea;GP Guadeloupe;GQ Equatorial Guinea;GR Greece;GS South Georgia and the South Sandwich Islands;\
GT Guatemala;GU Guam;GW Guinea-Bissau;GY Guyana;HK Hong Kong;HM Heard Island and McDonald Islands;HN Honduras;HR Croatia;HT Haiti;\
HU Hungary;ID Indonesia;IE Ireland;IL Israel;IM Isle of Man;IN India;IO British Indian Ocean Territory;IQ Iraq;IR Iran;IS Iceland;\
IT Italy;JE Jersey;JM Jamaica;JO Jordan;JP Japan;KE Kenya;KG Kyrgyzstan;KH Cambodia;KI Kiribati;KM Comoros;KN Saint Kitts and Nevis;\
KP North Korea;KR South Korea;KW Kuwait;KY Cayman Islands;KZ Kazakhstan;LA Laos;LB Lebanon;LC Saint Lucia;LI Liechtenstein;\
LK Sri Lanka;LR Liberia;LS Lesotho;LT Lithuania;LU Luxembourg;LV Latvia;LY Libya;MA Morocco;MC Monaco;MD Moldova;ME Montenegro;\
MF Saint Martin (French part);MG Madagascar;MH Marshall Islands;MK North Macedonia;ML Mali;MM Myanmar;MN Mongolia;MO Macao;\
MP Northern Mariana Islands;MQ Martinique;MR Mauritania;MS Montserrat;MT Malta;MU Mauritius;MV Maldives;MW Malawi;MX Mexico;\
MY Malaysia;MZ Mozambique;NA Namibia;NC New Caledonia;NE Niger;NF Norfolk Island;NG Nigeria;NI Nicaragua;NL Netherlands;NO Norway;\
NP Nepal;NR Nauru;NU Niue;NZ New Zealand;OM Oman;PA Panama;PE Peru;PF French Polynesia;PG Papua New Guinea;PH Philippines;\
PK Pakistan;PL Poland;PM Saint Pierre and Miquelon;PN Pitcairn;PR Puerto Rico;PS Palestine;PT Portugal;PW Palau;PY Paraguay;\
QA Qatar;RE Réunion;RO Romania;RS Serbia;RU Russia;RW Rwanda;SA Saudi Arabia;SB Solomon Islands;SC Seychelles;SD Sudan;SE Sweden;\
SG Singapore;SH Saint Helena;SI Slovenia;SJ Svalbard and Jan Mayen;SK Slovakia;SL Sierra Leone;SM San Marino;SN Senegal;SO Somalia;\
SR Suriname;SS South Sudan;ST São Tomé and Príncipe;SV El Salvador;SX Sint Maarten;SY Syria;SZ Eswatini;TC Turks and Caicos Islands;\
TD Chad;TF French Southern Territories;TG Togo;TH Thailand;TJ Tajikistan;TK Tokelau;TL Timor-Leste;TM Turkmenistan;TN Tunisia;\
TO Tonga;TR Türkiye;TT Trinidad and Tobago;TV Tuvalu;TW Taiwan;TZ Tanzania;UA Ukraine;UG Uganda;UM US Minor Outlying Islands;\
US United States;UY Uruguay;UZ Uzbekistan;VA Holy See;VC Saint Vincent and the Grenadines;VE Venezuela;VG British Virgin Islands;\
VI US Virgin Islands;VN Vietnam;VU Vanuatu;WF Wallis and Futuna;WS Samoa;YE Yemen;YT Mayotte;ZA South Africa;ZM Zambia;ZW Zimbabwe"""
COUNTRIES: dict[str, str] = {row[:2]: row[3:] for row in _COUNTRY_TABLE.split(";")}


class BuyError(ValueError):
    """What a person typed is not acceptable; the message says what to change."""


def region_of(country: str | None) -> str:
    """The row of the shop table a country belongs to: GB, US, EU (the EU, the EEA and Switzerland) or other."""
    if country in ("GB", "US"):
        return country
    return "EU" if country in _EUROPE else "other"


def clean_country(value: object) -> str | None:
    """An ISO 3166-1 alpha-2 code from the person's choice, or None for 'not set'. Anything else is refused."""
    if value is None or value == "":
        return None
    code = str(value).strip().upper()
    if code not in COUNTRIES:
        raise BuyError(f"'{value}' is not a country code the Vault knows: use two letters such as GB, US or DE.")
    return code


def front_face(card: str) -> str:
    """A double-faced card is searched by its front face (design section 4.6): 'A // B' gives 'A'."""
    return " ".join(str(card).split(" // ")[0].split())


def clean_card(card: object) -> str:
    text = " ".join(str(card or "").split())
    if not text:
        raise BuyError("Give the name of a card.")
    if len(text) > CARD_NAME_MAX or any(ord(ch) < 32 for ch in str(card)):
        raise BuyError(f"A card name is at most {CARD_NAME_MAX} characters, with no control characters.")
    return text


def order_for(country: str | None) -> tuple[list[str], list[str]]:
    """The shops in the order for a country, and the 'More shops' (every other shop, in the registry's order)."""
    first = list(_ORDERS[region_of(country)])
    return first, [i for i in SHOPS if i not in first]


# -- the shops the person typed -------------------------------------------------------------------------------------------------------

def _clean_text(value: object, what: str, limit: int) -> str:
    text = " ".join("".join(ch for ch in str(value or "") if unicodedata.category(ch) != "Cf").split())  # no bidi or zero-width characters
    if not text:
        raise BuyError(f"{what} is needed.")
    if len(text) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in str(value)):
        raise BuyError(f"{what} is at most {limit} characters, with no control characters.")
    return text


_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")


def _host_ok(host: str) -> bool:
    labels = host.split(".")
    if not (len(labels) >= 2 and all(label and len(label) <= 63 and not label.startswith("-") and not label.endswith("-")
                                     and all(ch.isalnum() or ch == "-" for ch in label) for label in labels)):
        return False
    # a shop is a name, not an address: browsers read a last label that is all digits or 0x-hex as an IPv4 address (10.0.0.1, 0x7f.1, 2130706433)
    if labels[-1].isdigit() or re.fullmatch(r"0[xX][0-9a-fA-F]*", labels[-1]):
        return False
    return not host.endswith(_LOCAL_SUFFIXES) and host != "localhost"


def _clean_address(value: object, what: str, *, search: bool = False) -> str:
    """An https address that is only ever shown as a link: https only, a real host name, no username or password, no spaces."""
    text = str(value or "").strip()
    if not text:
        raise BuyError(f"{what} is needed.")
    if len(text) > URL_MAX:
        raise BuyError(f"{what} is at most {URL_MAX} characters.")
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in text):
        raise BuyError(f"{what} must not contain spaces or control characters.")
    if not search and PLACEHOLDER in text:
        raise BuyError(f"{what} is the shop's page: {PLACEHOLDER} belongs in the search address only.")
    if search:
        if text.count(PLACEHOLDER) != 1:
            raise BuyError(f"{what} must contain {PLACEHOLDER} exactly once, where the card's name goes (copy it from the shop's own search page).")
        authority = text.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if PLACEHOLDER in authority:
            raise BuyError(f"{what}: {PLACEHOLDER} may only be in the path or the query, never in the shop's host name.")
    try:
        parts = urlsplit(text.replace(PLACEHOLDER, "card"))
    except ValueError:  # an address urlsplit cannot read ("https://[x/", a host that changes under Unicode normalisation)
        raise BuyError(f"{what} is not a web address the Vault can read.") from None
    if parts.scheme != "https":
        raise BuyError(f"{what} must start with https:// (other kinds of address are not accepted).")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise BuyError(f"{what} must not contain a user name or password.")
    try:
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        port = parts.port
    except (UnicodeError, ValueError):
        raise BuyError(f"{what} does not have a valid host name.") from None
    if not _host_ok(host) or port not in (None, 443):
        raise BuyError(f"{what} needs a shop's web address such as https://example.com/ (a host name with a dot, on the usual https port).")
    return text


def clean_store(raw: dict) -> dict:
    """One store the person typed: {name, url, search_url|None}. Raises BuyError saying what to change."""
    if not isinstance(raw, dict):
        raise BuyError("A store is a name and a web address.")
    search = str(raw.get("search_url") or "").strip()
    return {"name": _clean_text(raw.get("name"), "The store's name", NAME_MAX),
            "url": _clean_address(raw.get("url"), "The store's web address"),
            "search_url": _clean_address(search, "The search address", search=True) if search else None}


def clean_stores(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise BuyError("Stores are a list.")
    if len(raw) > MAX_STORES:
        raise BuyError(f"At most {MAX_STORES} stores.")
    return [clean_store(item) for item in raw]


def _shown_host(address: str) -> str | None:
    """The host as a browser will use it (punycode for a non-ASCII name), so a look-alike letter is not shown as the plain one."""
    host = urlsplit(address).hostname
    try:
        return host.encode("idna").decode("ascii") if host else host
    except UnicodeError:
        return host


def store_row(index: int, store: dict, card: str | None = None) -> dict:
    """A stored store as the menu shows it. With a card and a search address the row searches for the card; otherwise it opens the page."""
    searches = bool(store.get("search_url")) and card is not None
    href = store["search_url"].replace(PLACEHOLDER, quote_plus(front_face(card))) if searches else store["url"]
    return {"id": f"store-{index + 1}", "name": store["name"], "host": _shown_host(href), "url": href,
            "opens": "a search for the card on the store's own site" if searches else "the store's page",
            "typed_by_you": True}


def settings_view(country: str | None, stores: list[dict]) -> dict:
    """What GET /me/buy-settings answers: the saved choices, with the stores as the person typed them."""
    return {"country": country, "country_name": COUNTRIES.get(country) if country else None,
            "stores": [{"id": f"store-{i + 1}", "name": s["name"], "url": s["url"], "search_url": s.get("search_url"),
                        "host": _shown_host(s["url"])} for i, s in enumerate(stores)],
            "limits": {"stores": MAX_STORES, "name": NAME_MAX, "address": URL_MAX, "placeholder": PLACEHOLDER}}


# -- the menu -----------------------------------------------------------------------------------------------------------------------

def shop_row(shop: Shop, card: str) -> dict:
    return {"id": shop.id, "name": shop.name, "host": shop.host, "url": shop.search_url(card),
            "opens": "a search for the card on the shop's own site", "link_format_checked": shop.checked,
            "terms": shop.terms, "typed_by_you": False}


def menu(card: str, country: str | None, stores: list[dict]) -> dict:
    """The links of the menu for one card, in the order for the person's country. No network, no prices, no stock, no ranking.

    With no country set the order is the neutral one and ``region_orders`` carries the four orders, so the browser can pick the one
    for its own language's region without telling the server (design section 4.5); the server never reads the request's address.
    """
    card = clean_card(card)
    first, more = order_for(country)
    out = {
        "card": card,
        "searched_as": front_face(card),
        "country": country, "country_name": COUNTRIES.get(country) if country else None,
        "region_note": REGION_NOTES[region_of(country)] if country else
        "No country is set: the shops are in a neutral order. Choose where you buy to see the nearest first.",
        "my_stores": [store_row(i, s, card) for i, s in enumerate(stores)],
        "shops": [shop_row(SHOPS[i], card) for i in first],
        "more_shops": [shop_row(SHOPS[i], card) for i in more],
        "locator": {"name": LOCATOR_NAME, "url": LOCATOR_URL, "terms": LOCATOR_TERMS, "link_format_checked": LOCATOR_CHECKED,
                    "opens": "the official store finder's own page: type your town or postcode there"},
        "scryfall": {"name": "Scryfall", "url": "https://scryfall.com/search?q=" + quote_plus('!"' + front_face(card).replace('"', "") + '"'),
                     "opens": "the card's page on Scryfall, which has its own buy links"},
        "notice": FOOTER,
        "prices": "No price, stock or shipping is shown: the Vault fetches nothing from any shop.",
    }
    if country is None:
        out["region_orders"] = {key: list(ids) for key, ids in _ORDERS.items()}
        out["region_notes"] = dict(REGION_NOTES)
        out["europe_countries"] = sorted(_EUROPE)
    return out


def provenance_for(shop_ids: list[str]) -> list[dict]:
    """The provenance of a menu answer: computed by the Vault from the card's name; the shops and the locator are named as theirs."""
    inputs = [prov.source(SHOPS[i].name, origin=f"search link format checked {SHOPS[i].checked}; terms: {SHOPS[i].terms}",
                          url=f"https://{SHOPS[i].host}/", as_of=SHOPS[i].checked) for i in shop_ids]
    inputs.append(prov.source(LOCATOR_NAME, origin="Wizards of the Coast (official retailers)", url=LOCATOR_URL, as_of=LOCATOR_CHECKED))
    return [prov.computed("plain links built from the card's name; nothing was fetched from any shop or from the store locator", inputs,
                          as_of=date.today()).model_dump(exclude_none=True)]
