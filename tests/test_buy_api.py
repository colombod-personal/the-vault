"""Where to buy over the API and MCP (#212): the menu of a card, the person's country and typed stores, their export and erasure, and the promise
that nothing is contacted and no address is read."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.test_agents import V1, auth, bot, call_tool, make_token, rpc  # noqa: F401 - fixtures and helpers
from twins.universe import Universe
from vault.api import mcp
from vault.app import create_app
from vault.config import Settings
from vault.models import BuySettings, Identity

STORE = {"name": "Aldershot Game Shop", "url": "https://aldershotgames.example.co.uk/",
         "search_url": "https://aldershotgames.example.co.uk/search?q={card}"}


def login(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200


def ids(rows):
    return [r["id"] for r in rows]


def test_a_new_account_has_no_country_and_no_stores(signed_in):
    body = signed_in.get(f"{V1}/me/buy-settings").json()
    assert body["country"] is None and body["stores"] == [] and body["updated_at"] is None
    assert body["limits"] == {"stores": 3, "name": 80, "address": 300, "placeholder": "{card}"}
    assert body["_links"]["self"]["href"] == f"{V1}/me/buy-settings" and signed_in.get(f"{V1}/me").json()["_links"]["buy_settings"]


def test_the_country_and_the_stores_are_saved_and_read_back_as_typed(signed_in):
    saved = signed_in.put(f"{V1}/me/buy-settings", json={"country": "gb", "stores": [STORE]})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["country"] == "GB" and body["country_name"] == "United Kingdom" and body["updated_at"]
    assert body["stores"] == [{"id": "store-1", "name": STORE["name"], "url": STORE["url"], "search_url": STORE["search_url"], "host": "aldershotgames.example.co.uk"}]
    assert signed_in.get(f"{V1}/me/buy-settings").json() == body


def test_a_field_left_out_keeps_its_saved_value_and_null_clears_the_country(signed_in):
    signed_in.put(f"{V1}/me/buy-settings", json={"country": "DE", "stores": [STORE]})
    only_country = signed_in.put(f"{V1}/me/buy-settings", json={"country": "US"}).json()
    assert only_country["country"] == "US" and len(only_country["stores"]) == 1
    only_stores = signed_in.put(f"{V1}/me/buy-settings", json={"stores": []}).json()
    assert only_stores["country"] == "US" and only_stores["stores"] == []
    cleared = signed_in.put(f"{V1}/me/buy-settings", json={"country": None}).json()
    assert cleared["country"] is None


@pytest.mark.parametrize("body, words", [
    ({"country": "UK"}, "country code"),
    ({"country": "ZZ"}, "country code"),
    ({"stores": [{"name": "A", "url": "http://shop.example.com/"}]}, "https"),
    ({"stores": [{"name": "A", "url": "javascript:alert(1)"}]}, "https"),
    ({"stores": [{"name": "A", "url": "https://u:p@shop.example.com/"}]}, "password"),
    ({"stores": [{"name": "", "url": "https://shop.example.com/"}]}, "name"),
    ({"stores": [{"name": "A", "url": "https://shop.example.com/", "search_url": "https://shop.example.com/s"}]}, "{card}"),
    ({"stores": [{"name": "A", "url": "https://shop.example.com/", "search_url": "https://{card}.example.com/"}]}, "host name"),
    ({"stores": [STORE] * 4}, "At most 3"),
])
def test_what_cannot_be_kept_is_refused_with_the_reason_and_nothing_changes(signed_in, body, words):
    signed_in.put(f"{V1}/me/buy-settings", json={"country": "FR"})
    res = signed_in.put(f"{V1}/me/buy-settings", json=body)
    assert res.status_code == 422 and words in res.text, res.text
    assert signed_in.get(f"{V1}/me/buy-settings").json()["country"] == "FR"


def test_the_settings_are_removed_on_their_own_and_a_second_removal_is_harmless(signed_in):
    signed_in.put(f"{V1}/me/buy-settings", json={"country": "GB", "stores": [STORE]})
    assert signed_in.delete(f"{V1}/me/buy-settings").json() == {"deleted": True}
    assert signed_in.get(f"{V1}/me/buy-settings").json()["country"] is None
    assert signed_in.delete(f"{V1}/me/buy-settings").json() == {"deleted": False}


def test_a_retried_save_with_the_same_key_is_answered_from_the_first(signed_in):
    first = signed_in.put(f"{V1}/me/buy-settings", json={"country": "GB"}, headers={"Idempotency-Key": "buy-1"})
    signed_in.put(f"{V1}/me/buy-settings", json={"country": "US"})
    again = signed_in.put(f"{V1}/me/buy-settings", json={"country": "GB"}, headers={"Idempotency-Key": "buy-1"})
    assert again.headers["idempotent-replayed"] == "true" and again.json() == first.json()
    assert signed_in.get(f"{V1}/me/buy-settings").json()["country"] == "US"  # the replay changed nothing


def test_the_countries_to_choose_from_are_listed_with_the_shops_each_gets_first(signed_in):
    page = signed_in.get(f"{V1}/buy/countries?limit=500").json()
    assert page["total"] == 249 and page["count"] == 249 and "next" not in page["_links"]
    by_code = {c["code"]: c for c in page["items"]}
    assert by_code["GB"] == {"code": "GB", "name": "United Kingdom", "region": "GB", "shops": ["magicmadhouse", "cardmarket"]}
    assert by_code["DE"]["region"] == "EU" and by_code["DE"]["shops"] == ["cardmarket"]
    assert [c["name"].lower() for c in page["items"]] == sorted(c["name"].lower() for c in page["items"])
    small = signed_in.get(f"{V1}/buy/countries?limit=100").json()
    assert small["count"] == 100 and "next" in small["_links"]


def test_the_menu_for_a_card_follows_the_saved_country_and_leads_with_the_stores_typed(signed_in):
    signed_in.put(f"{V1}/me/buy-settings", json={"country": "GB", "stores": [STORE, {"name": "Page only", "url": "https://pageonly.example.com/"}]})
    menu = signed_in.get(f"{V1}/buy/menu", params={"card": "Hunter Sliver"}).json()
    assert menu["country"] == "GB" and ids(menu["shops"]) == ["magicmadhouse", "cardmarket"] and ids(menu["more_shops"]) == ["cardkingdom"]
    assert [(s["name"], s["opens"]) for s in menu["my_stores"]] == [
        ("Aldershot Game Shop", "a search for the card on the store's own site"), ("Page only", "the store's page")]
    assert menu["my_stores"][0]["url"] == "https://aldershotgames.example.co.uk/search?q=Hunter+Sliver"
    assert menu["my_stores"][1]["url"] == "https://pageonly.example.com/"
    assert menu["shops"][0]["url"] == "https://magicmadhouse.co.uk/search.php?search_query=Hunter+Sliver"
    assert menu["locator"]["url"] == "https://locator.wizards.com/" and menu["scryfall"]["url"].startswith("https://scryfall.com/search?q=")
    assert menu.get("region_orders") is None  # a saved country needs no browser guess
    provenance = menu["provenance"][0]
    assert provenance["kind"] == "computed" and provenance["source"] == "The Vault" and "nothing was fetched" in provenance["origin"]
    assert {i["source"] for i in provenance["inputs"]} >= {"Magic Madhouse", "Cardmarket", "Card Kingdom", "Wizards Store & Event Locator"}


def test_without_a_country_the_menu_is_neutral_and_says_so(signed_in):
    menu = signed_in.get(f"{V1}/buy/menu", params={"card": "Sol Ring"}).json()
    assert menu["country"] is None and ids(menu["shops"]) == ["cardmarket", "cardkingdom"] and ids(menu["more_shops"]) == ["magicmadhouse"]
    assert menu["region_orders"]["GB"] == ["magicmadhouse", "cardmarket"] and "No country is set" in menu["region_note"]


def test_the_server_never_reads_where_the_request_came_from(signed_in):
    """Design section 4.5: the same card gives the same menu whatever address or language the request carries."""
    plain = signed_in.get(f"{V1}/buy/menu", params={"card": "Sol Ring"}).json()
    other = signed_in.get(f"{V1}/buy/menu", params={"card": "Sol Ring"}, headers={
        "Accept-Language": "en-GB,en;q=0.9", "X-Forwarded-For": "81.2.69.142", "X-Real-IP": "81.2.69.142",
        "CF-IPCountry": "GB", "X-Vercel-IP-Country": "GB"}).json()
    assert {k: v for k, v in plain.items() if k != "provenance"} == {k: v for k, v in other.items() if k != "provenance"}
    assert other["country"] is None and ids(other["shops"]) == ["cardmarket", "cardkingdom"]
    saved = signed_in.put(f"{V1}/me/buy-settings", json={"country": "GB"}, headers={"X-Forwarded-For": "8.8.8.8", "Accept-Language": "fr-FR"})
    assert saved.json()["country"] == "GB"


def test_a_menu_needs_a_card_name_and_a_sign_in(client, signed_in):
    assert signed_in.get(f"{V1}/buy/menu").status_code == 422
    assert signed_in.get(f"{V1}/buy/menu", params={"card": "x" * 301}).status_code == 422
    assert signed_in.get(f"{V1}/buy/menu", params={"card": "   "}).status_code == 422
    client.cookies.clear()
    for path in ("/buy/menu?card=Sol+Ring", "/buy/countries", "/me/buy-settings"):
        assert client.get(V1 + path).status_code == 401, path
    assert client.put(f"{V1}/me/buy-settings", json={"country": "GB"}).status_code == 401


def test_each_person_has_their_own_settings(client):
    login(client, "alice@example.com")
    client.put(f"{V1}/me/buy-settings", json={"country": "GB", "stores": [STORE]})
    login(client, "bob@example.com")
    assert client.get(f"{V1}/me/buy-settings").json()["country"] is None
    assert client.get(f"{V1}/buy/menu", params={"card": "Sol Ring"}).json()["my_stores"] == []
    client.put(f"{V1}/me/buy-settings", json={"country": "US"})
    login(client, "alice@example.com")
    body = client.get(f"{V1}/me/buy-settings").json()
    assert body["country"] == "GB" and len(body["stores"]) == 1


def test_a_token_reads_the_menu_but_cannot_read_or_change_the_settings(signed_in, bot):
    signed_in.put(f"{V1}/me/buy-settings", json={"country": "GB", "stores": [STORE]})
    write = auth(make_token(signed_in, scopes=["read", "write"]))
    read = auth(make_token(signed_in))
    menu = bot.get(f"{V1}/buy/menu", params={"card": "Sol Ring"}, headers=read)
    assert menu.status_code == 200 and menu.json()["country"] == "GB"
    for headers in (read, write):
        assert bot.get(f"{V1}/me/buy-settings", headers=headers).status_code == 403
        assert bot.put(f"{V1}/me/buy-settings", json={"country": "US"}, headers=headers).status_code == 403
        assert bot.delete(f"{V1}/me/buy-settings", headers=headers).status_code == 403
    assert signed_in.get(f"{V1}/me/buy-settings").json()["country"] == "GB"


def test_the_assistant_tool_reads_the_same_menu_and_cannot_write_the_settings(signed_in, bot):
    signed_in.put(f"{V1}/me/buy-settings", json={"country": "US", "stores": [STORE]})
    token = make_token(signed_in)
    tool = {t["name"]: t for t in rpc(bot, "tools/list", token=token).json()["result"]["tools"]}["where_to_buy"]
    assert tool["annotations"]["readOnlyHint"] is True and tool["annotations"]["destructiveHint"] is False
    assert tool["inputSchema"]["required"] == ["card"] and list(tool["inputSchema"]["properties"]) == ["card"]
    answer = call_tool(bot, token, "where_to_buy", card="Delver of Secrets // Insectile Aberration")
    assert not answer.get("isError"), answer
    body = answer["structuredContent"]
    assert body["searched_as"] == "Delver of Secrets" and ids(body["shops"]) == ["cardkingdom", "cardmarket"]
    assert body["my_stores"][0]["url"].endswith("q=Delver+of+Secrets")
    assert body["provenance"][0]["kind"] == "computed" and "nothing was fetched" in body["provenance"][0]["origin"]
    assert "where_to_buy" in mcp.BY_NAME and mcp.BY_NAME["where_to_buy"].provenance == ("computed",)
    assert not [n for n in mcp.BY_NAME if "buy" in n and "setting" in n]  # no tool writes the settings
    refused = rpc(bot, "tools/call", {"name": "where_to_buy", "arguments": {"card": ""}}, token).json()
    assert refused["error"]["code"] == -32602  # the tool's own schema refuses an empty name before any link is made


def test_the_tool_description_says_what_it_does_and_names_no_workflow_or_cheapest_shop():
    d = mcp.BY_NAME["where_to_buy"].description.lower()
    assert "plain links" in d and "no price" in d and "contacts no shop" in d


# -- export and erasure ---------------------------------------------------------------------------------------------------------------

def archive(client):
    return zipfile.ZipFile(io.BytesIO(client.get(f"{V1}/me/export").content))


def test_the_export_holds_the_country_and_the_stores_as_typed_and_names_the_file(signed_in):
    empty = json.loads(archive(signed_in).read("buy_settings.json"))
    assert empty == {"country": None, "stores": [], "updated_at": None}
    signed_in.put(f"{V1}/me/buy-settings", json={"country": "GB", "stores": [STORE]})
    z = archive(signed_in)
    data = json.loads(z.read("buy_settings.json"))
    assert data["country"] == "GB" and data["updated_at"]
    assert data["stores"] == [{"name": STORE["name"], "web_address": STORE["url"], "search_address": STORE["search_url"]}]
    assert set(data) == {"country", "stores", "updated_at"}  # nothing else is held: no postcode, address or language
    assert "buy_settings.json" in z.read("README.txt").decode()


def test_erasing_the_account_deletes_the_settings_and_leaves_other_peoples_alone(client, app):
    login(client, "bob@example.com")
    client.put(f"{V1}/me/buy-settings", json={"country": "US"})
    login(client, "alice@example.com")
    client.put(f"{V1}/me/buy-settings", json={"country": "GB", "stores": [STORE]})
    with app.state.db.sessions() as db:
        alice = db.scalar(select(Identity).where(Identity.subject == "alice@example.com")).user_id
        assert db.get(BuySettings, alice) is not None
    removed = client.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}).json()["removed"]
    assert removed["buy_settings"] == 1
    with app.state.db.sessions() as db:
        assert db.get(BuySettings, alice) is None
        assert [r.country for r in db.scalars(select(BuySettings))] == ["US"]


def test_the_table_holds_only_the_country_the_stores_and_a_date(app):
    assert [c.name for c in BuySettings.__table__.columns] == ["user_id", "country", "stores", "updated_at"]


# -- nothing contacts a shop ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def twin_client(database_url):
    universe = Universe()
    settings = Settings(database_url=database_url, session_secret="t", base_url="http://testserver", dev_login=True, archidekt_interval=0)
    with TestClient(create_app(settings, serve_static=False, transport=universe.transport)) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        yield c, universe
    assert not universe.escapes, universe.escapes


def test_saving_settings_and_building_menus_contacts_nothing(twin_client):
    client, universe = twin_client
    before = len(universe.escapes)
    client.put(f"{V1}/me/buy-settings", json={"country": "GB", "stores": [STORE]})
    for card in ("Sol Ring", "Wear // Tear", "Teferi's Protection"):
        assert client.get(f"{V1}/buy/menu", params={"card": card}).status_code == 200
    assert len(universe.escapes) == before == 0  # the typed store's address is shown as a link, never requested
