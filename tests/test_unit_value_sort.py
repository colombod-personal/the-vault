"""Sort the collection by one copy's price, not only by a printing's total (bead vault-wxy.10).

The owner's words: sorting by value in Browse sorted by combined value (quantity times price), so 2 Smothering Tithe ranked above
1 Sliver Queen. `unit_value` / `-unit_value` order by the price of one copy; `value` / `-value` keep meaning the total, so no
existing caller changes. A printing with no price is last in both unit orders; ties are by name. Browse, the dashboard's top cards and
the assistant's `search_cards` read the same sort, and Browse makes single card value its default.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient
from vault.collection_view import SORTS

V1 = "/api/v1"
CARDS = f"{V1}/collection/cards"
PUBLIC = Path(__file__).parent.parent / "public"
BROWSE = (PUBLIC / "views" / "browse.jsx").read_text(encoding="utf-8")
DASHBOARD = (PUBLIC / "views" / "dashboard.jsx").read_text(encoding="utf-8")

HEADER = ("Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,"
          "Price Bought,Date Bought,LOW,MID,MARKET")


def collection(*rows: tuple[str, int, str]) -> bytes:
    """A Dragon Shield file: (name, copies, market price or '' for none), each its own printing."""
    lines = ['"sep=,"', HEADER]
    for i, (name, copies, market) in enumerate(rows, start=1):
        lines.append(f"my cards,{copies},0,{name},TST,Test Set,{i},Mint,Normal,English,,,,,{market}")
    return ("\n".join(lines) + "\n").encode()


# The case that made the owner ask: 3 copies at $10 (total $30) against 1 copy at $25. The two orders are opposite.
FILE = collection(("Smothering Tithe", 3, "10.00"), ("Sliver Queen", 1, "25.00"),
                  ("Counterspell", 2, "5.00"), ("Brainstorm", 1, "5.00"),  # the same single price: ties by name
                  ("Zzz Mystery", 4, ""), ("Aaa Unpriced", 1, ""))         # no price at all


def names(client, **params):
    items, res = [], client.get(CARDS, params=params)
    assert res.status_code == 200, res.text
    while True:
        body = res.json()
        items += [i["name"] for i in body["items"]]
        if "next" not in body["_links"]:
            return items
        res = client.get(body["_links"]["next"]["href"])


def load(client):
    res = client.post(f"{V1}/imports", files={"file": ("e.csv", FILE, "text/csv")})
    assert res.status_code == 201, res.text


def test_single_card_value_and_total_value_put_the_same_cards_in_opposite_orders(signed_in):
    load(signed_in)
    # 3 x $10 = $30 is worth more in total than 1 x $25, but the single card is the dearer one
    assert names(signed_in, sort="-unit_value")[:2] == ["Sliver Queen", "Smothering Tithe"]
    assert names(signed_in, sort="-value")[:2] == ["Smothering Tithe", "Sliver Queen"]
    assert names(signed_in, sort="unit_value")[-4:-2] == ["Smothering Tithe", "Sliver Queen"]
    assert names(signed_in, sort="value")[-2:] == ["Sliver Queen", "Smothering Tithe"]
    rows = {c["name"]: c for c in signed_in.get(CARDS, params={"limit": 100}).json()["items"]}
    assert rows["Smothering Tithe"]["quantity"] == 3 and rows["Smothering Tithe"]["price"]["market"] == 10.0
    assert rows["Smothering Tithe"]["value"] == 30.0 and rows["Sliver Queen"]["value"] == 25.0  # what each order reads


def test_unit_value_ties_by_name_and_unpriced_cards_are_last_in_both_directions(signed_in):
    load(signed_in)
    assert names(signed_in, sort="-unit_value") == ["Sliver Queen", "Smothering Tithe", "Brainstorm", "Counterspell",
                                                    "Aaa Unpriced", "Zzz Mystery"]
    assert names(signed_in, sort="unit_value") == ["Brainstorm", "Counterspell", "Smothering Tithe", "Sliver Queen",
                                                   "Aaa Unpriced", "Zzz Mystery"]
    # Counterspell has two copies at the same $5 as Brainstorm's one: the total ($10 against $5) plays no part in a unit sort
    assert names(signed_in, sort="-value") == ["Smothering Tithe", "Sliver Queen", "Counterspell", "Brainstorm",
                                               "Aaa Unpriced", "Zzz Mystery"]


def test_the_unit_value_sort_pages_through_the_cursor_without_repeating_or_skipping(signed_in):
    load(signed_in)
    for sort, expected in (("-unit_value", ["Sliver Queen", "Smothering Tithe", "Brainstorm", "Counterspell", "Aaa Unpriced", "Zzz Mystery"]),
                           ("unit_value", ["Brainstorm", "Counterspell", "Smothering Tithe", "Sliver Queen", "Aaa Unpriced", "Zzz Mystery"])):
        first = signed_in.get(CARDS, params={"sort": sort, "limit": 1}).json()
        assert "sort=" + sort in first["_links"]["next"]["href"]  # the link keeps the sort
        assert names(signed_in, sort=sort, limit=1) == expected
        assert names(signed_in, sort=sort, limit=4) == expected


def test_the_endpoint_accepts_the_new_sorts_and_still_refuses_unknown_ones(signed_in):
    load(signed_in)
    for sort in ("unit_value", "-unit_value", "value", "-value"):
        assert signed_in.get(CARDS, params={"sort": sort}).status_code == 200, sort
    for sort in ("unit", "-unit", "price", "unit_value ", "UNIT_VALUE"):
        res = signed_in.get(CARDS, params={"sort": sort})
        assert res.status_code == 400 and "sort must be one of" in res.text, sort
    assert "-unit_value" in signed_in.get(CARDS, params={"sort": "price"}).text  # the refusal lists what is allowed
    assert {"unit_value", "-unit_value", "value", "-value"} <= set(SORTS)


def test_the_collection_links_the_most_valuable_cards_to_the_single_card_sort(signed_in):
    load(signed_in)
    link = signed_in.get(f"{V1}/collection").json()["_links"]["most_valuable"]["href"]
    assert "sort=-unit_value" in link
    assert [c["name"] for c in signed_in.get(link).json()["items"]][0] == "Sliver Queen"


def test_the_openapi_description_names_both_sorts(app):
    with TestClient(app) as c:
        spec = c.get("/api/openapi.json").json()
    params = spec["paths"]["/api/v1/collection/cards"]["get"]["parameters"]
    text = next(p for p in params if p["name"] == "sort")["description"]
    assert "-unit_value" in text and "price of one copy" in text and "total" in text


def test_an_assistant_can_sort_by_single_card_value_and_the_tool_says_which_sort_is_which(app, signed_in):
    from test_agents import call_tool, make_token, rpc

    load(signed_in)
    token = make_token(signed_in)
    with TestClient(app) as bot:
        tool = next(t for t in rpc(bot, "tools/list", token=token).json()["result"]["tools"] if t["name"] == "search_cards")
        sort = tool["inputSchema"]["properties"]["sort"]
        assert {"unit_value", "-unit_value", "value", "-value"} <= set(sort["enum"])
        assert "'-unit_value' = the price of one copy" in sort["description"] and "'-value' = the printing's total" in sort["description"]
        assert sort["default"] == "name"  # what a caller gets without asking is unchanged
        single = call_tool(bot, token, "search_cards", sort="-unit_value", limit=2)["structuredContent"]
        assert [c["name"] for c in single["items"]] == ["Sliver Queen", "Smothering Tithe"]
        total = call_tool(bot, token, "search_cards", sort="-value", limit=2)["structuredContent"]
        assert [c["name"] for c in total["items"]] == ["Smothering Tithe", "Sliver Queen"]  # '-value' keeps its meaning
        bad = rpc(bot, "tools/call", {"name": "search_cards", "arguments": {"sort": "price"}}, token).json()
        assert "error" in bad or bad["result"].get("isError")


# -- Browse is a front end: its source says which sort it starts with -------------------------------------------------------------

def browse_sorts() -> dict[str, str]:
    block = re.search(r"const BROWSE_SORTS = \{(.*?)\};", BROWSE, re.S).group(1)
    return dict(re.findall(r"(\w+): '([^']+)'", block))


def test_browse_starts_on_single_card_value_and_offers_the_total_too():
    sorts = browse_sorts()
    default = re.search(r"const BROWSE_DEFAULT_SORT = '(\w+)';", BROWSE).group(1)
    assert sorts[default] == "-unit_value"
    assert "useStateB(initialQuery?.sort || BROWSE_DEFAULT_SORT)" in BROWSE  # what a visitor gets with no sort asked for
    assert "BROWSE_SORTS[sort] || BROWSE_SORTS[BROWSE_DEFAULT_SORT]" in BROWSE
    menu = re.search(r'aria-label="Sort by".*?</select>', BROWSE, re.S).group(0)
    options = dict(re.findall(r'<option value="(\w+)">([^<]+)</option>', menu))
    assert next(iter(options)) == default  # first in the menu
    assert options[default].startswith("Sort: single card value")
    assert any(label.startswith("Sort: total value ↓") for label in options.values())
    assert options["unitValueAsc"].startswith("Sort: single card value ↑") and options["valueAsc"].startswith("Sort: total value ↑")
    assert set(options) == set(sorts)  # every choice the menu shows is one the view can send, and the reverse


def test_every_sort_browse_can_send_is_one_the_server_accepts():
    for key, sort in browse_sorts().items():
        assert sort in SORTS, (key, sort)


def test_the_dashboard_top_cards_and_its_link_into_browse_use_single_card_value():
    sorts = browse_sorts()
    assert "api.cards({ sort: '-unit_value', limit: 12 })" in DASHBOARD
    key = re.search(r"gotoBrowse\(\{ sort: '(\w+)' \}\)", DASHBOARD).group(1)
    assert sorts[key] == "-unit_value"  # "Browse all" opens the list the cards above came from
    assert "api.cards({ sort: '-value'" not in DASHBOARD


def test_a_tile_with_several_copies_shows_the_price_of_one_next_to_the_total():
    tile = DASHBOARD.split("function CardTile", 1)[1]
    assert "c.q > 1 && c.mk != null" in tile and "each)" in tile
    assert 'title="The price of one copy">Market' in BROWSE and "The price of one copy times the copies you own" in BROWSE
