"""Where to buy (#212, docs/where-to-buy-design.md): the link templates and their format, the order of the shops for a country, the stores a
person types, and the rules that keep the menu private: nothing contacts a shop, nothing is placed from an address, no price is shown."""

import inspect
import re
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlsplit

import pytest

from vault import buy_links as b

ROOT = Path(__file__).parent.parent
NAMES = {
    "plain": "Sol Ring",
    "apostrophe": "Teferi's Protection",
    "comma": "Mondrak, Glory Dominus",
    "non-ascii": "Lim-Dûl's Vault",
    "ampersand and slash": "Fire & Ice / Test",
}


@pytest.mark.parametrize("what", NAMES)
@pytest.mark.parametrize("shop", b.SHOPS)
def test_every_shop_template_encodes_the_name_the_way_the_skills_already_expect(shop, what):
    name = NAMES[what]
    url = b.SHOPS[shop].search_url(name)
    assert url.startswith("https://") and url.count("{") == 0
    assert url.endswith(quote_plus(name))  # spaces become +, apostrophes and commas are percent-encoded, nothing is left to break the query
    assert " " not in url and "'" not in url


def test_the_exact_addresses_of_the_three_shops():
    assert b.SHOPS["magicmadhouse"].search_url("Sol Ring") == "https://magicmadhouse.co.uk/search.php?search_query=Sol+Ring"
    assert b.SHOPS["cardkingdom"].search_url("Sol Ring") == "https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D=Sol+Ring"
    assert b.SHOPS["cardmarket"].search_url("Sol Ring") == "https://www.cardmarket.com/en/Magic/Products/Search?searchString=Sol+Ring"
    assert b.SHOPS["magicmadhouse"].search_url("Teferi's Protection").endswith("Teferi%27s+Protection")
    assert b.SHOPS["cardmarket"].search_url("Mondrak, Glory Dominus").endswith("Mondrak%2C+Glory+Dominus")
    assert b.SHOPS["cardkingdom"].search_url("Lim-Dûl's Vault").endswith("Lim-D%C3%BBl%27s+Vault")


def test_a_name_with_an_ampersand_cannot_add_a_parameter_to_a_shop_query():
    for shop in b.SHOPS.values():
        query = parse_qs(urlsplit(shop.search_url("Fire & Ice")).query)
        assert "Ice" not in query and any(v == ["Fire & Ice"] for v in query.values()), (shop.id, query)


def test_a_double_faced_card_is_searched_by_its_front_face():
    assert b.front_face("Delver of Secrets // Insectile Aberration") == "Delver of Secrets"
    assert b.SHOPS["magicmadhouse"].search_url("Fire // Ice").endswith("search_query=Fire")
    assert b.menu("Wear // Tear", None, [])["searched_as"] == "Wear"
    assert b.menu("Wear // Tear", None, [])["scryfall"]["url"] == "https://scryfall.com/search?q=%21%22Wear%22"  # the search the app already used


def test_the_countries_are_the_iso_ones_and_each_belongs_to_one_region():
    assert len(b.COUNTRIES) == 249 and all(re.fullmatch(r"[A-Z]{2}", c) for c in b.COUNTRIES) and all(b.COUNTRIES.values())
    assert b.COUNTRIES["GB"] == "United Kingdom" and b.COUNTRIES["US"] == "United States"
    assert {b.region_of(c) for c in ("DE", "FR", "NO", "CH", "IS")} == {"EU"}  # the EU, the EEA and Switzerland
    assert b.region_of("GB") == "GB" and b.region_of("US") == "US" and b.region_of("AU") == "other" and b.region_of(None) == "other"


def test_the_shops_come_first_in_the_order_the_owner_approved_and_the_rest_are_more_shops():
    assert b.order_for("GB") == (["magicmadhouse", "cardmarket"], ["cardkingdom"])
    assert b.order_for("US") == (["cardkingdom", "cardmarket"], ["magicmadhouse"])
    assert b.order_for("DE") == (["cardmarket"], ["magicmadhouse", "cardkingdom"])
    assert b.order_for("AU") == (["cardmarket", "cardkingdom"], ["magicmadhouse"])
    assert b.order_for(None) == b.order_for("AU")  # not set: the neutral order, Cardmarket first
    for country in ("GB", "US", "DE", "AU", None):
        first, more = b.order_for(country)
        assert sorted(first + more) == sorted(b.SHOPS)  # every shop is somewhere, none twice


def test_the_menu_for_a_card_has_plain_links_and_no_price_stock_or_ranking():
    menu = b.menu("Hunter Sliver", "GB", [{"name": "The Games Shop", "url": "https://gamesshop.example.co.uk/", "search_url": None}])
    assert [s["id"] for s in menu["shops"]] == ["magicmadhouse", "cardmarket"] and [s["id"] for s in menu["more_shops"]] == ["cardkingdom"]
    assert menu["my_stores"][0]["name"] == "The Games Shop" and menu["my_stores"][0]["typed_by_you"] is True
    assert menu["locator"]["url"] == "https://locator.wizards.com/"  # the locator's own page: no place is put in the address
    text = repr(menu).lower()
    for word in ("price_usd", "in stock", "stock:", "cheapest", "best price", "affiliate="):
        assert word not in text, word
    assert "does not contact these shops" in menu["notice"] and "earn from your clicks" in menu["notice"] and "not affiliated" in menu["notice"].lower()
    for row in menu["shops"] + menu["more_shops"]:
        assert row["url"].startswith("https://") and row["link_format_checked"] and row["terms"].startswith("https://")
        assert set(row) == {"id", "name", "host", "url", "opens", "link_format_checked", "terms", "typed_by_you"}  # nothing a shop would have told us


def test_without_a_country_the_menu_carries_the_four_orders_so_the_browser_can_choose_by_its_language():
    menu = b.menu("Sol Ring", None, [])
    assert menu["country"] is None and set(menu["region_orders"]) == {"GB", "US", "EU", "other"}
    assert "DE" in menu["europe_countries"] and "GB" not in menu["europe_countries"]
    assert [s["id"] for s in menu["shops"]] == menu["region_orders"]["other"]
    assert "region_orders" not in b.menu("Sol Ring", "GB", [])  # a saved country needs no browser guess


def test_a_typed_store_opens_its_page_or_searches_for_the_card():
    page = b.store_row(0, {"name": "Shop", "url": "https://shop.example.com/", "search_url": None}, "Sol Ring")
    assert page["url"] == "https://shop.example.com/" and page["opens"] == "the store's page" and page["host"] == "shop.example.com"
    search = b.store_row(1, {"name": "Shop", "url": "https://shop.example.com/", "search_url": "https://shop.example.com/find?q={card}&x=1"}, "Fire & Ice // Hot")
    assert search["url"] == "https://shop.example.com/find?q=Fire+%26+Ice&x=1" and search["opens"].startswith("a search")


GOOD = {"name": "Aldershot Game Shop", "url": "https://aldershotgames.example.co.uk/", "search_url": "https://aldershotgames.example.co.uk/search?q={card}"}


def test_invisible_formatting_characters_are_dropped_from_a_name_and_a_shown_host_is_the_one_a_browser_uses():
    assert b.clean_store({"name": "Shop\u202egnp.exe\u200b", "url": "https://shop.example.com/"})["name"] == "Shopgnp.exe"
    row = b.store_row(0, {"name": "S", "url": "https://k\u00e4rtchen.example/", "search_url": None})
    assert row["host"] == "xn--krtchen-5wa.example"  # punycode, so a look-alike letter is not shown as the plain one


def test_a_quote_in_a_card_name_cannot_change_the_scryfall_search_text():
    assert b.menu('He said "hi"', None, [])["scryfall"]["url"] == "https://scryfall.com/search?q=%21%22He+said+hi%22"


def test_a_good_store_is_kept_as_typed():
    assert b.clean_store(GOOD) == GOOD
    assert b.clean_store({"name": "  A   B ", "url": "https://shop.example.com"})["name"] == "A B"
    assert b.clean_store({"name": "Shop", "url": "https://shop.example.com", "search_url": ""})["search_url"] is None
    assert b.clean_store({"name": "Kartenhändler", "url": "https://kartenhändler.example/"})["url"] == "https://kartenhändler.example/"


@pytest.mark.parametrize("field, bad", [
    ("url", "http://shop.example.com/"),                   # https only
    ("url", "javascript:alert(1)"), ("url", "data:text/html,hi"), ("url", "//shop.example.com/"), ("url", "ftp://shop.example.com/"),
    ("url", "https://user:pw@shop.example.com/"), ("url", "https://shop.example.com@evil.example/"),
    ("url", "https://localhost/"), ("url", "https://10.0.0.1/"), ("url", "https://shop/"), ("url", "https://-bad.example.com/"),
    ("url", "https://shop.example.com:8443/"), ("url", "https://shop .example.com/"), ("url", "https://shop.example.com/\npath"),
    ("url", "https://" + "a" * 300 + ".example.com/"), ("url", ""),
    ("url", "https://[x/"), ("url", "https://good.com\uff0fevil.example/"), ("url", "https://\u2100.com/"),   # urlsplit cannot read them: a reason, not a 500
    ("url", "https://0x7f.0.0.1/"), ("url", "https://0x7f.1/"), ("url", "https://2130706433/"), ("url", "https://foo.localhost/"),
    ("url", "https://printer.local/"), ("url", "https://router.home.arpa/"),         # an address in any spelling a browser reads, or a local name
    ("url", "https://{card}.example.com/"), ("url", "https://shop.example.com/{card}"),  # {card} belongs in the search address only
    ("name", ""), ("name", "x" * 81), ("name", "bad\x00name"),
    ("search_url", "https://shop.example.com/search"),     # no {card}
    ("search_url", "https://shop.example.com/{card}/{card}"),
    ("search_url", "https://{card}.example.com/"),          # the card's name must never choose the host
    ("search_url", "https://shop.example.com{card}"),
    ("search_url", "http://shop.example.com/?q={card}"),
])
def test_a_store_that_could_be_unsafe_or_is_malformed_is_refused_with_a_reason(field, bad):
    with pytest.raises(b.BuyError) as why:
        b.clean_store({**GOOD, field: bad})
    assert str(why.value)


def test_at_most_three_stores_and_the_country_must_be_a_known_code():
    assert len(b.clean_stores([GOOD, GOOD, GOOD])) == 3
    with pytest.raises(b.BuyError):
        b.clean_stores([GOOD] * 4)
    with pytest.raises(b.BuyError):
        b.clean_stores("not a list")
    assert b.clean_country("gb") == "GB" and b.clean_country("") is None and b.clean_country(None) is None
    for bad in ("UK", "GBR", "ZZ", "1", "G"):
        with pytest.raises(b.BuyError):
            b.clean_country(bad)


def test_a_card_name_is_checked_before_a_link_is_made():
    for bad in ("", "   ", "x" * 301, "a\x00b"):
        with pytest.raises(b.BuyError):
            b.clean_card(bad)
    assert b.clean_card("  Sol   Ring ") == "Sol Ring"


def test_the_provenance_names_the_shops_as_theirs_and_the_vault_as_the_builder():
    block = b.provenance_for(["magicmadhouse", "cardmarket"])[0]
    assert block["kind"] == "computed" and "nothing was fetched" in block["origin"]
    assert [i["source"] for i in block["inputs"]] == ["Magic Madhouse", "Cardmarket", b.LOCATOR_NAME]
    assert all(i["kind"] == "source" and i["as_of"] for i in block["inputs"])


def test_the_code_that_builds_the_menu_cannot_contact_anything_or_read_where_the_request_came_from():
    """Design section 4.5: never the IP address, never a header. The module and the routes import no way to reach a host and read no address."""
    for path in ("vault/buy_links.py", "vault/api/buy_api.py"):
        source = (ROOT / path).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", '"""')))
        for forbidden in ("httpx", "requests", "urllib.request", "urlopen", "socket", "aiohttp", "http.client", "transport"):
            assert forbidden not in code, f"{path} mentions {forbidden}"
        for forbidden in ("request.client", "x-forwarded-for", "x-real-ip", "accept-language", "cf-ipcountry", "x-vercel-ip", "geoip", "headers"):
            assert forbidden not in code.lower(), f"{path} reads {forbidden}"


def test_every_shop_template_goes_through_one_module():
    """Nothing else in the code concatenates a shop address: only buy_links.py (and the shopping-list formats' help text) names a shop host."""
    hosts = {s.host for s in b.SHOPS.values()} | {"locator.wizards.com"}
    offenders = []
    for path in list((ROOT / "vault").rglob("*.py")):
        if path.name in ("buy_links.py", "shopping.py", "experts_data.py") or "migrations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.relative_to(ROOT)}: {h}" for h in hosts if h in text and f"https://{h}" in text.replace("www.", "")]
    assert offenders == [], offenders


def test_the_signature_of_the_public_functions_did_not_grow_a_place_or_an_address_parameter():
    for fn in (b.menu, b.order_for, b.store_row):
        names = set(inspect.signature(fn).parameters)
        assert not names & {"ip", "address", "postcode", "place", "lat", "lng", "latitude", "longitude", "locale", "language"}, (fn.__name__, names)
