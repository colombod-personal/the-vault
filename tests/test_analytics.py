"""Server-side analytics (vault.analytics): P&L, breakdowns, valuation, per-name rollups, set colour
mixes, deck prices, and the server price refresh. All computed in Postgres from the owner's rows."""

import base64
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mtg_toolkits.scryfall import Card

from twins import Universe
from vault import analytics
from vault.app import create_app
from vault.collection_view import CollectionView, view_version
from vault.config import Settings
from vault.models import User
from vault.sync import sync

V1 = "/api/v1"
CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
# Two copies of a card with no price paid and no purchase date (and a printing the sync won't find).
BOLT = b"my cards,2,0,Lightning Bolt,M11,Magic 2011,149,NearMint,Normal,English,,,0.50,0.90,1.00\r\n"


def upload(client, content=CSV + BOLT):
    res = client.post(f"{V1}/imports", files={"file": ("export.csv", content, "text/csv")})
    assert res.status_code == 201, res.text
    return res.json()


def login(client, email):
    client.cookies.clear()
    assert client.post("/api/auth/dev-login", params={"email": email}).status_code == 200


def card(id, name, set_code, number, type_line, colors, cmc, rarity, finishes=("nonfoil",), **prices):
    return Card.from_json({
        "id": id, "name": name, "set": set_code, "collector_number": number, "finishes": list(finishes),
        "prices": {k: str(v) for k, v in prices.items()}, "type_line": type_line, "color_identity": colors,
        "cmc": cmc, "rarity": rarity, "artist": "Artist",
        "image_uris": {"small": f"https://img.test/{id}-s.jpg", "normal": f"https://img.test/{id}.jpg"},
    })


BULK = [
    card("kil", "A Killer Among Us", "mkm", "167", "Enchantment", ["G"], 5.0, "uncommon", usd=0.12),
    card("sol", "Sol Ring", "c21", "263", "Artifact", [], 1.0, "uncommon", ("nonfoil", "foil"), usd=1.0, usd_foil=3.0),
    card("acc", "Accursed Marauder", "mh3", "512", "Creature — Zombie Warrior", ["B"], 2.0, "common", ("etched",),
         usd_etched=0.8),
    card("bel", "Belfry Spirit", "gk2", "29", "Legendary Enchantment Creature — Spirit", ["W", "B"], 9.0, "mythic",
         usd=0.25),
]


@pytest.fixture
def synced(app, signed_in):
    upload(signed_in)
    with app.state.db.sessions() as db:
        sync(db, BULK, day=date(2026, 9, 27))
    return signed_in


def by_key(buckets):
    return {b["key"]: (b["copies"], b["printings"], b["market_value"]) for b in buckets}


# -- pure rules ---------------------------------------------------------------------------------

def test_main_type_rule():
    assert analytics.main_type("Legendary Creature — Dragon") == "Creature"
    assert analytics.main_type("Artifact Creature — Golem") == "Creature"
    assert analytics.main_type("Enchantment Creature — God") == "Creature"
    assert analytics.main_type("Artifact Land") == "Land"
    assert analytics.main_type("Land Creature — Forest Dryad") == "Creature"
    assert analytics.main_type("Legendary Planeswalker — Jace") == "Planeswalker"
    assert analytics.main_type("Kindred Instant — Elf") == "Instant"
    assert analytics.main_type("Sorcery // Land") == "Sorcery"  # modal double-faced: the front face
    assert analytics.main_type("Creature — Human Wizard // Creature — Human Insect") == "Creature"
    assert analytics.main_type("Instant // Sorcery") == "Instant"  # split card: its left half
    assert analytics.main_type("Battle — Siege") == "Battle"
    assert analytics.main_type("Legendary Artifact") == "Artifact"
    assert analytics.main_type("Conspiracy") == "Other" and analytics.main_type(None) == "Other"
    assert [analytics.color_key(c) for c in ([], ["U"], ["W", "B"], None)] == ["C", "U", "M", "C"]


# -- P&L in the summary -------------------------------------------------------------------------

def test_summary_pnl_counts_only_copies_with_a_known_cost(signed_in):
    upload(signed_in)
    s = signed_in.get(f"{V1}/collection").json()
    # file prices: Killer 4 × 0.09, Sol Ring 2.20, Marauder 0.55, Belfry 0.10; the Bolts have no price paid
    assert (s["known_cost_paid"], s["known_cost_market"]) == (2.83, 3.21)
    assert (s["pnl"], s["pnl_pct"]) == (0.38, round(0.38 / 2.83 * 100, 2))
    assert (s["known_cost_copies"], s["unknown_cost_copies"]) == (7, 2)
    assert s["market_value"] == 5.21  # the Bolts are in the value, never in the P&L at zero cost
    assert {"breakdowns", "valuation", "names", "refresh"} <= set(s["_links"])


def test_summary_pnl_is_null_without_any_known_cost(signed_in):
    upload(signed_in, CSV.split(b"\r\n")[0] + b"\r\n" + CSV.split(b"\r\n")[1] + b"\r\n" + BOLT)
    s = signed_in.get(f"{V1}/collection").json()
    assert s["pnl"] is None and s["pnl_pct"] is None and s["known_cost_copies"] == 0 and s["unknown_cost_copies"] == 2


# -- breakdowns -------------------------------------------------------------------------------

def test_breakdowns_by_colour_type_mana_value_rarity_and_matrix(synced):
    b = synced.get(f"{V1}/collection/breakdowns").json()
    assert b["totals"] == {"copies": 9, "printings": 5, "market_value": round(0.48 + 3.0 + 0.8 + 0.25 + 2.0, 2)}
    colors = by_key(b["colors"])
    assert [x["key"] for x in b["colors"]] == ["W", "U", "B", "R", "G", "M", "C", "unknown"]
    assert colors["G"] == (4, 1, 0.48) and colors["C"] == (1, 1, 3.0) and colors["B"] == (1, 1, 0.8)
    assert colors["M"] == (1, 1, 0.25) and colors["unknown"] == (2, 1, 2.0) and colors["U"] == (0, 0, 0.0)
    types = by_key(b["types"])
    assert types["Enchantment"] == (4, 1, 0.48) and types["Artifact"] == (1, 1, 3.0)
    assert types["Creature"] == (2, 2, 1.05) and types["unknown"] == (2, 1, 2.0) and types["Land"][0] == 0
    mvs = by_key(b["mana_values"])
    assert [x["key"] for x in b["mana_values"]] == ["0", "1", "2", "3", "4", "5", "6", "7", "8+", "unknown"]
    assert (mvs["5"][0], mvs["1"][0], mvs["2"][0], mvs["8+"][0], mvs["unknown"][0]) == (4, 1, 1, 1, 2)
    rarities = by_key(b["rarities"])
    assert (rarities["uncommon"][0], rarities["common"][0], rarities["mythic"][0], rarities["unknown"][0]) == (5, 1, 1, 2)
    cell = {(c["color"], c["type"]): c["copies"] for c in b["matrix"]}
    assert cell[("G", "Enchantment")] == 4 and cell[("M", "Creature")] == 1 and cell[("unknown", "unknown")] == 2
    assert sum(cell.values()) == 9
    tag = synced.get(f"{V1}/collection/breakdowns").headers["etag"]
    assert synced.get(f"{V1}/collection/breakdowns", headers={"If-None-Match": tag}).status_code == 304


def test_view_version_matches_the_views(app, synced):
    with app.state.db.sessions() as db:
        user = db.query(User).one()
        for hide in (False, True):
            assert view_version(db, user, hide_costs=hide) == CollectionView(db, user, hide_costs=hide).version


# -- valuation ----------------------------------------------------------------------------------

def test_valuation_cumulative_by_month(signed_in):
    upload(signed_in)
    v = signed_in.get(f"{V1}/collection/valuation").json()
    months = {m["month"]: m for m in v["months"]}
    assert list(months) == ["2022-11", "2023-01", "2024-02", "2024-03", "2024-06"]
    assert months["2024-02"]["copies"] == 3 and months["2024-02"]["paid"] == 0.18 and months["2024-02"]["market"] == 0.27
    last = v["months"][-1]
    assert (last["copies_cum"], last["market_cum"], last["cost_cum"]) == (7, 3.21, 2.83)
    assert last["gain_cum"] == last["known_gain_cum"] == 0.38
    assert v["totals"] == {"copies": 7, "market": 3.21, "cost": 2.83, "gain": 0.38, "known_gain": 0.38}
    assert v["peak"] == {"month": "2023-01", "market": 2.2, "copies": 1}
    assert v["trailing_12m"] == {"since": "2023-07", "until": "2024-06", "copies": 5, "market": 0.91, "paid": 0.73}
    assert v["undated"] == {"copies": 2, "market": 2.0} and v["costs_hidden"] is False
    # the timeline uses the same months and the same spend
    timeline = signed_in.get(f"{V1}/collection/timeline").json()["months"]
    assert [(t["month"], t["copies"], t["paid"]) for t in timeline] == [(m["month"], m["copies"], m["paid"]) for m in v["months"]]


def test_valuation_of_an_empty_collection(signed_in):
    v = signed_in.get(f"{V1}/collection/valuation").json()
    assert v["months"] == [] and v["peak"] is None and v["trailing_12m"] is None and v["totals"]["copies"] == 0


# -- names --------------------------------------------------------------------------------------

def test_names_rollup_sorts_filters_and_pages(synced):
    page = synced.get(f"{V1}/collection/names").json()
    assert [n["name"] for n in page["items"]] == ["Sol Ring", "Lightning Bolt", "Accursed Marauder",
                                                  "A Killer Among Us", "Belfry Spirit"]
    killer = next(n for n in page["items"] if n["name"] == "A Killer Among Us")
    assert (killer["copies"], killer["market_value"], killer["unit_price"], killer["printings"]) == (4, 0.48, 0.12, 1)
    assert (killer["color"], killer["type"], killer["cmc"], killer["rarity"], killer["sets"]) == ("G", "Enchantment", 5.0, "uncommon", ["MKM"])
    assert killer["image"]["artist"] == "Artist" and killer["image"]["normal"] == "https://img.test/kil.jpg"
    bolt = next(n for n in page["items"] if n["name"] == "Lightning Bolt")
    assert bolt["color"] == "unknown" and bolt["type"] == "unknown" and bolt["image"] is None

    first = synced.get(f"{V1}/collection/names", params={"limit": 2, "sort": "-quantity"}).json()
    assert first["total"] == 5 and [n["name"] for n in first["items"]] == ["A Killer Among Us", "Lightning Bolt"]
    seen = [n["name"] for n in first["items"]]
    nxt = first["_links"].get("next")
    while nxt:
        body = synced.get(nxt["href"]).json()
        seen += [n["name"] for n in body["items"]]
        nxt = body["_links"].get("next")
    assert len(seen) == 5 and len(set(seen)) == 5
    names = lambda **p: [n["name"] for n in synced.get(f"{V1}/collection/names", params=p).json()["items"]]  # noqa: E731
    assert names(sort="name") == sorted(seen, key=str.lower) and names(sort="-name") == sorted(seen, key=str.lower)[::-1]
    assert names(sort="value")[0] == "Belfry Spirit"
    assert names(colors="B") == ["Accursed Marauder", "Belfry Spirit"]  # multicolour W/B matches B
    assert names(colors="M") == ["Belfry Spirit"] and names(colors="C,G") == ["Sol Ring", "A Killer Among Us"]
    assert names(type="creature") == ["Accursed Marauder", "Belfry Spirit"] and names(type="unknown") == ["Lightning Bolt"]
    assert names(min_value=0.5) == ["Sol Ring", "Lightning Bolt", "Accursed Marauder"]
    assert synced.get(f"{V1}/collection/names", params={"limit": 2, "min_value": 0.5}).json()["total"] == 3
    for bad in ({"sort": "nope"}, {"colors": "X"}, {"cursor": "%%%"}, {"min_value": -1}):
        assert synced.get(f"{V1}/collection/names", params=bad).status_code in (400, 422), bad
    for forged in ({}, ["x"], [None, None, None], ["abc", "k"]):
        cursor = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
        assert synced.get(f"{V1}/collection/names", params={"cursor": cursor}).status_code == 400, forged


# -- /cards and /sets and /stats additions --------------------------------------------------------

def test_cards_printing_filter_new_sorts_and_value_total(synced):
    page = synced.get(f"{V1}/collection/cards").json()
    assert page["value_total"] == round(0.48 + 3.0 + 0.8 + 0.25 + 2.0, 2)
    foil = synced.get(f"{V1}/collection/cards", params={"printing": "foil"}).json()
    assert [c["name"] for c in foil["items"]] == ["Sol Ring"] and foil["value_total"] == 3.0
    assert "printing=foil" in foil["_links"]["self"]["href"]
    oldest = [c["name"] for c in synced.get(f"{V1}/collection/cards", params={"sort": "acquired"}).json()["items"]]
    assert oldest == ["Belfry Spirit", "Sol Ring", "A Killer Among Us", "Accursed Marauder", "Lightning Bolt"]
    page = synced.get(f"{V1}/collection/cards", params={"sort": "-name", "limit": 2}).json()
    z_to_a = page["items"]
    while "next" in page["_links"]:
        page = synced.get(page["_links"]["next"]["href"]).json()
        z_to_a += page["items"]
    assert [c["name"] for c in z_to_a] == ["Sol Ring", "Lightning Bolt", "Belfry Spirit", "Accursed Marauder",
                                           "A Killer Among Us"]


def test_sets_search_sort_and_colour_mix(synced):
    sets = synced.get(f"{V1}/collection/sets").json()["items"]
    assert [s["code"] for s in sets] == ["C21", "M11", "MH3", "MKM", "GK2_ORZHOV"]  # by value, as before
    mkm = next(s for s in sets if s["code"] == "MKM")
    assert mkm["colors"]["G"] == 4 and sum(mkm["colors"].values()) == 4
    assert next(s for s in sets if s["code"] == "M11")["colors"]["unknown"] == 2
    assert next(s for s in sets if s["code"] == "GK2_ORZHOV")["colors"]["M"] == 1
    get = lambda **p: [s["code"] for s in synced.get(f"{V1}/collection/sets", params=p).json()["items"]]  # noqa: E731
    assert get(q="horizons") == ["MH3"] and get(q="mkm") == ["MKM"]
    assert get(sort="code") == ["C21", "GK2_ORZHOV", "M11", "MH3", "MKM"]
    assert get(sort="-quantity")[0] == "MKM" and get(sort="value")[0] == "GK2_ORZHOV"
    assert get(sort="name")[0] == "C21"  # Commander 2021
    assert synced.get(f"{V1}/collection/sets", params={"sort": "nope"}).status_code == 400


def test_stats_limit_and_stockpile_print_counts(synced):
    st = synced.get(f"{V1}/collection/stats", params={"limit": 2}).json()
    assert len(st["most_valuable"]) == 2 and len(st["most_copies"]) == 2
    assert st["most_copies"][0] == {**st["most_copies"][0], "name": "A Killer Among Us", "copies": 4, "printings": 1,
                                    "market_value": 0.48}
    assert len(synced.get(f"{V1}/collection/stats").json()["most_valuable"]) == 5  # default 8
    for bad in (0, 51):
        assert synced.get(f"{V1}/collection/stats", params={"limit": bad}).status_code == 422


# -- decks with prices --------------------------------------------------------------------------

def test_deck_coverage_with_prices(synced):
    res = synced.post(f"{V1}/decks/coverage", json={"text": "1 Sol Ring\n6 A Killer Among Us\n1 Rhystic Study"}).json()
    lines = {c["name"]: c for c in res["cards"]}
    killer = lines["A Killer Among Us"]
    assert (killer["missing"], killer["unit_price"], killer["missing_cost"]) == (2, 0.12, 0.24)
    assert killer["owned_printings"] == [{"set": "MKM", "collector_number": "167", "printing": "Normal",
                                          "finish": "nonfoil", "quantity": 4, "unit_price": 0.12}]
    sol = lines["Sol Ring"]
    assert sol["missing"] == 0 and sol["missing_cost"] == 0.0 and sol["unit_price"] == 1.0  # cheapest finish
    assert lines["Rhystic Study"]["unit_price"] is None and lines["Rhystic Study"]["missing_cost"] is None
    assert res["missing_cost"] == 0.24 and res["missing_unpriced"] == 1
    deck = synced.post(f"{V1}/decks", json={"name": "d", "text": "6 A Killer Among Us"}).json()
    got = synced.get(f"{V1}/decks/{deck['id']}").json()["coverage"]
    assert got["missing_cost"] == 0.24 and got["cards"][0]["owned_printings"][0]["quantity"] == 4


# -- tenancy and sharing ------------------------------------------------------------------------

ANALYTICS = ("breakdowns", "valuation", "names")


def test_analytics_never_show_another_users_data(app, client):
    login(client, "alice@example.com")
    upload(client)
    login(client, "bob@example.com")
    assert client.get(f"{V1}/collection/breakdowns").json()["totals"]["copies"] == 0
    assert client.get(f"{V1}/collection/names").json()["total"] == 0
    assert client.get(f"{V1}/collection/valuation").json()["months"] == []
    s = client.get(f"{V1}/collection").json()
    assert s["known_cost_copies"] == 0 and s["pnl"] is None
    coverage = client.post(f"{V1}/decks/coverage", json={"text": "1 Sol Ring"}).json()
    assert coverage["cards"][0]["owned_printings"] == [] and coverage["cards"][0]["have"] == 0
    for path in ANALYTICS:
        assert client.get(f"{V1}/shared/999/collection/{path}").status_code == 404


def test_shared_collection_analytics_follow_the_cost_setting(client):
    login(client, "alice@example.com")
    upload(client)
    hidden = client.post(f"{V1}/shares", json={"kind": "collection"}).json()["url"].split("invite=")[1]
    login(client, "bob@example.com")
    share_id = client.post(f"{V1}/shares/accept", json={"token": hidden}).json()["id"]
    base = f"{V1}/shared/{share_id}/collection"
    s = client.get(base).json()
    assert s["pnl"] is None and s["known_cost_paid"] is None and s["known_cost_copies"] is None
    assert {"breakdowns", "valuation", "names"} <= set(s["_links"]) and "refresh" not in s["_links"]
    v = client.get(f"{base}/valuation").json()
    assert v["costs_hidden"] and v["totals"]["cost"] is None and all(m["paid"] is None for m in v["months"])
    assert v["totals"]["market"] == 3.21
    assert client.get(f"{base}/breakdowns").json()["totals"]["copies"] == 9
    names = client.get(f"{base}/names").json()
    assert names["total"] == 5 and names["_links"]["self"]["href"].startswith(base)
    assert client.get(f"{base}/sets").json()["items"][0]["colors"]["unknown"] > 0

    login(client, "carol@example.com")  # not shared with Carol
    for path in ("",) + tuple("/" + p for p in ANALYTICS):
        assert client.get(f"{base}{path}").status_code == 404

    login(client, "alice@example.com")
    shown = client.post(f"{V1}/shares", json={"kind": "collection", "show_costs": True}).json()["url"].split("invite=")[1]
    login(client, "dave@example.com")
    shared = client.post(f"{V1}/shares/accept", json={"token": shown}).json()["id"]
    s = client.get(f"{V1}/shared/{shared}/collection").json()
    assert (s["pnl"], s["known_cost_copies"], s["unknown_cost_copies"]) == (0.38, 7, 2)


# -- server price refresh -------------------------------------------------------------------------

SOL_RING = "736f0d31-9052-5d04-b5d5-727ebc7f5cc9"


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, f"calls left the twin universe: {u.escapes}"


@pytest.fixture
def twin_client(database_url, universe):
    settings = Settings(database_url=database_url, session_secret="test", dev_login=True,
                        base_url="http://testserver", refresh_rate_limit=3)
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        yield c
    app.state.db.engine.dispose()


def collection_calls(universe):
    return [c for c in universe.scryfall.calls if c.path == "/cards/collection"]


def test_refresh_fetches_prices_in_chunks_until_nothing_remains(twin_client, universe, monkeypatch):
    c = twin_client
    # the Vault learns the printings, so the import matches the rows to them
    known = [{"set": "mkm", "collector_number": "167"}, {"set": "c21", "collector_number": "263"},
             {"set": "mh3", "collector_number": "512"}]
    assert c.post(f"{V1}/cards/lookup", json={"identifiers": known}).status_code == 200
    upload(c)
    before = c.get(f"{V1}/collection").json()
    calls = len(collection_calls(universe))
    done = c.post(f"{V1}/collection/refresh").json()  # every printing already has today's price
    assert (done["total"], done["remaining"], done["processed"], done["cursor"]) == (3, 0, 0, None)
    assert done["unmatched_rows"] == 2 and len(collection_calls(universe)) == calls

    universe.scryfall.set_price(SOL_RING, usd=7.0, usd_foil=9.0)
    monkeypatch.setattr(analytics, "REFRESH_CHUNK", 2)
    first = c.post(f"{V1}/collection/refresh", json={"force": True}).json()
    assert (first["processed"], first["remaining"], first["done"]) == (2, 1, 2) and first["cursor"]
    second = c.post(f"{V1}/collection/refresh", json={"force": True, "cursor": first["cursor"]}).json()
    assert (second["processed"], second["remaining"], second["done"], second["cursor"]) == (1, 0, 3, None)
    after = c.get(f"{V1}/collection").json()
    assert after["version"] != before["version"] and second["version"] == after["version"]
    sol = c.get(f"{V1}/collection/cards", params={"q": "sol ring"}).json()["items"][0]
    assert sol["price"]["market"] == 9.0 and sol["price"]["source"] == "scryfall"
    history = c.get(f"{V1}/collection/history").json()["items"]
    assert history[-1]["market"] == after["market_value"]  # today's value written at the new prices

    res = c.post(f"{V1}/collection/refresh")  # the fourth call this minute
    assert res.status_code == 429 and int(res.headers["retry-after"]) >= 1


def test_refresh_reports_scryfall_outages(twin_client, universe):
    c = twin_client
    assert c.post(f"{V1}/cards/lookup", json={"identifiers": [{"set": "c21", "collector_number": "263"}]}).status_code == 200
    upload(c)
    universe.scryfall.fail_next("/cards/collection", 503, times=5)
    res = c.post(f"{V1}/collection/refresh", json={"force": True})
    assert res.status_code == 503 and res.headers["retry-after"]


def test_refresh_needs_write_scope_and_is_per_user(twin_client):
    c = twin_client
    upload(c)
    read = c.post(f"{V1}/me/tokens", json={"name": "r", "scopes": ["read"]}).json()["token"]
    c.cookies.clear()
    res = c.post(f"{V1}/collection/refresh", headers={"Authorization": f"Bearer {read}"})
    assert res.status_code == 403
    assert c.post(f"{V1}/collection/refresh").status_code == 401
    login(c, "bob@example.com")
    body = c.post(f"{V1}/collection/refresh").json()
    assert body["total"] == 0 and body["unmatched_rows"] == 0  # Bob's own, empty collection


# -- MCP tools ------------------------------------------------------------------------------------

def test_mcp_analytics_tools(app, synced):
    from test_agents import call_tool, make_token, rpc

    read = make_token(synced)
    with TestClient(app) as bot:
        names = {t["name"] for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}
        assert {"get_collection_breakdowns", "get_valuation", "list_card_names"} <= names
        assert "refresh_prices" not in names  # a write tool: not for a read-only token
        b = call_tool(bot, read, "get_collection_breakdowns")["structuredContent"]
        assert b["totals"]["copies"] == 9
        assert call_tool(bot, read, "get_valuation")["structuredContent"]["totals"]["copies"] == 7
        top = call_tool(bot, read, "list_card_names", limit=2, colors="C,G")["structuredContent"]
        assert [n["name"] for n in top["items"]] == ["Sol Ring", "A Killer Among Us"]
        stats = call_tool(bot, read, "get_collection_stats", limit=1)["structuredContent"]
        assert len(stats["most_copies"]) == 1 and stats["most_copies"][0]["printings"] == 1
        sets = call_tool(bot, read, "list_sets", query="mkm", sort="code")["structuredContent"]
        assert [s["code"] for s in sets["items"]] == ["MKM"] and sets["items"][0]["colors"]["G"] == 4
        foil = call_tool(bot, read, "search_cards", printing="Foil")["structuredContent"]
        assert [c["name"] for c in foil["items"]] == ["Sol Ring"]
        assert call_tool(bot, read, "refresh_prices")["isError"] is True
        bad = rpc(bot, "tools/call", {"name": "list_card_names", "arguments": {"colors": "X"}}, read).json()
        assert bad["error"]["code"] == -32602


def test_sets_sort_by_release_date_from_the_catalog(twin_client, universe):
    c = twin_client
    upload(c)
    released = {card["set"]: card.get("released_at") for card in universe.scryfall.cards.values()}
    page = c.get(f"{V1}/collection/sets", params={"sort": "release"}).json()["items"]
    dates = [s["released_at"] for s in page]
    known = [d for d in dates if d]
    assert known and known == sorted(known) and dates[:len(known)] == known  # unknown dates last
    assert next(s for s in page if s["code"] == "MKM")["released_at"] == released["mkm"]
    newest = c.get(f"{V1}/collection/sets", params={"sort": "-release"}).json()["items"]
    assert [s["released_at"] for s in newest if s["released_at"]] == sorted(known, reverse=True)
    # the set list is now cached, so the default order shows the dates too, without another call
    calls = len([x for x in universe.scryfall.calls if x.path == "/sets"])
    assert next(s for s in c.get(f"{V1}/collection/sets").json()["items"] if s["code"] == "MKM")["released_at"]
    assert len([x for x in universe.scryfall.calls if x.path == "/sets"]) == calls
