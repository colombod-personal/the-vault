"""Type and mana value filters (and the mana value sort) on the collection's cards, for the app and for assistants (#284).

The signed-off review (docs/graph-and-lab-review.md) said two questions lose their only home when the old Graph is cut: the dearest
cards of one type, and mana value against price. This is the change it names: a type filter and a mana value filter (and sort) on
`/collection/cards`, which `search_cards` also reads. They use the definitions the rest of the app already uses (`vault.analytics`: one
main type per card, mana value buckets 0 to 7 and 8+), so Browse, the assistant and the breakdowns never disagree.
"""

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from mtg_toolkits.scryfall import Card
from tests.ids import sid
from vault import analytics
from vault.sync import sync

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
V1 = "/api/v1"
CARDS = f"{V1}/collection/cards"


def card(id, name, set_code, number, type_line, cmc, finishes=("nonfoil",), **prices):
    return Card.from_json({
        "id": sid(id), "name": name, "set": set_code, "collector_number": number, "finishes": list(finishes),
        "prices": {k: str(v) for k, v in prices.items()}, "type_line": type_line, "artist": "A", **({"cmc": cmc} if cmc is not None else {}),
        "image_uris": {"small": f"https://img.test/{id}-s.jpg", "normal": f"https://img.test/{id}.jpg"},
    })


# Type lines that exercise the rules: several card types on one line, and a fractional mana value.
BULK = [
    card("kil", "A Killer Among Us", "mkm", "167", "Legendary Artifact Creature — Golem", 4, usd=0.12),       # a Creature, not an Artifact
    card("sol", "Sol Ring", "c21", "263", "Artifact", 1, ("nonfoil", "foil"), usd=1.0, usd_foil=3.0),       # an Artifact
    card("acc", "Accursed Marauder", "mh3", "512", "Artifact Land", None, ("etched",), usd_etched=0.8),     # a Land, not an Artifact; no mana value stored: 0
    card("bel", "Belfry Spirit", "gk2", "29", "Enchantment Creature — Spirit", 3.5, usd=0.25),              # a Creature; 3.5 counts as 3
]


def names(client, **params):
    items, res = [], client.get(CARDS, params=params)
    assert res.status_code == 200, res.text
    while True:
        body = res.json()
        items += [i["name"] for i in body["items"]]
        if "next" not in body["_links"]:
            return items
        res = client.get(body["_links"]["next"]["href"])


def all_items(client, **params):
    items, res = [], client.get(CARDS, params=params)
    assert res.status_code == 200, res.text
    while True:
        body = res.json()
        items += body["items"]
        if "next" not in body["_links"]:
            return items
        res = client.get(body["_links"]["next"]["href"])


def synced(app, client, bulk=BULK):
    assert client.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")}).status_code == 201
    with app.state.db.sessions() as db:
        sync(db, bulk, day=date(2026, 9, 27))


def test_type_is_the_main_type_the_breakdowns_use_in_any_case(app, signed_in):
    synced(app, signed_in)
    assert names(signed_in, type="Creature") == ["A Killer Among Us", "Belfry Spirit"]  # Artifact Creature, Enchantment Creature
    assert names(signed_in, type="creature") == ["A Killer Among Us", "Belfry Spirit"]
    assert names(signed_in, type="Artifact") == ["Sol Ring"]  # not the Artifact Creature, not the Artifact Land
    assert names(signed_in, type="Land") == ["Accursed Marauder"]
    assert names(signed_in, type="Enchantment") == []  # an Enchantment Creature is a Creature
    assert names(signed_in, type="Other") == []
    body = signed_in.get(CARDS, params={"type": "Creature"}).json()
    assert body["total"] == 2 and body["value_total"] == round(sum(i["value"] for i in body["items"]), 2)


def test_mana_value_is_the_breakdowns_bucket_and_a_fraction_counts_down(app, signed_in):
    synced(app, signed_in)
    assert names(signed_in, mana_value="0") == ["Accursed Marauder"]
    assert names(signed_in, mana_value="1") == ["Sol Ring"]
    assert names(signed_in, mana_value="3") == ["Belfry Spirit"]  # 3.5
    assert names(signed_in, mana_value="4") == ["A Killer Among Us"]
    assert names(signed_in, mana_value="2") == [] and names(signed_in, mana_value="8+") == []
    assert names(signed_in, type="Creature", mana_value="3") == ["Belfry Spirit"]
    assert names(signed_in, type="Creature", mana_value="1") == []
    assert names(signed_in, type="Creature", set="mkm") == ["A Killer Among Us"]
    assert names(signed_in, q="spirit", mana_value="3") == ["Belfry Spirit"]


def test_the_filters_agree_with_the_breakdowns_for_every_type_and_every_mana_value(app, signed_in):
    """The Browse numbers and the Lab's breakdown numbers are the same cards: a disagreement would be two answers to one question."""
    synced(app, signed_in)
    breakdown = signed_in.get(f"{V1}/collection/breakdowns").json()
    for bucket in breakdown["types"]:
        if bucket["key"] == analytics.UNKNOWN:
            continue
        items = all_items(signed_in, type=bucket["key"])
        assert sum(i["quantity"] for i in items) == bucket["copies"], bucket["key"]
        assert len(items) == bucket["printings"], bucket["key"]
    for bucket in breakdown["mana_values"]:
        if bucket["key"] == analytics.UNKNOWN:
            continue
        items = all_items(signed_in, mana_value=bucket["key"])
        assert sum(i["quantity"] for i in items) == bucket["copies"], bucket["key"]
    top = signed_in.get(f"{V1}/collection/names", params={"type": "Creature"}).json()
    assert sorted(n["name"] for n in top["items"]) == sorted(names(signed_in, type="Creature"))  # list_card_names says the same


def test_the_mana_value_sort_pages_in_order_both_ways_and_keeps_the_filters_in_its_links(app, signed_in):
    synced(app, signed_in)
    assert names(signed_in, sort="mana_value") == ["Accursed Marauder", "Sol Ring", "Belfry Spirit", "A Killer Among Us"]
    assert names(signed_in, sort="-mana_value") == ["A Killer Among Us", "Belfry Spirit", "Sol Ring", "Accursed Marauder"]
    first = signed_in.get(CARDS, params={"sort": "mana_value", "limit": 1, "type": "Creature"}).json()
    assert [i["name"] for i in first["items"]] == ["Belfry Spirit"]
    href = first["_links"]["next"]["href"]
    assert "type=Creature" in href and "sort=mana_value" in href
    assert [i["name"] for i in signed_in.get(href).json()["items"]] == ["A Killer Among Us"]
    paged = []
    res = signed_in.get(CARDS, params={"sort": "-mana_value", "limit": 1})
    while True:  # one at a time, through the cursor, to the end: no card repeated or skipped
        body = res.json()
        paged += [i["name"] for i in body["items"]]
        if "next" not in body["_links"]:
            break
        res = signed_in.get(body["_links"]["next"]["href"])
    assert paged == ["A Killer Among Us", "Belfry Spirit", "Sol Ring", "Accursed Marauder"]


def test_printings_without_card_data_are_left_out_of_a_filter_and_sorted_last(app, signed_in):
    synced(app, signed_in, bulk=BULK[:2])  # only A Killer Among Us and Sol Ring have card data
    assert names(signed_in, type="Creature") == ["A Killer Among Us"]
    assert names(signed_in, type="Land") == []  # Accursed Marauder is a land in the data, but its card is not stored: not guessed
    assert names(signed_in, mana_value="0") == []
    assert names(signed_in, sort="mana_value") == ["Sol Ring", "A Killer Among Us", "Accursed Marauder", "Belfry Spirit"]
    assert names(signed_in, sort="-mana_value") == ["A Killer Among Us", "Sol Ring", "Accursed Marauder", "Belfry Spirit"]


def test_a_value_that_is_not_a_type_or_a_bucket_is_refused_and_says_what_is_allowed(app, signed_in):
    synced(app, signed_in)
    for params, word in (({"type": "Sliver"}, "type must be one of"), ({"type": ""}, "type must be one of"),
                         ({"mana_value": "9"}, "mana_value must be one of"), ({"mana_value": "-1"}, "mana_value must be one of"),
                         ({"mana_value": "many"}, "mana_value must be one of")):
        res = signed_in.get(CARDS, params=params)
        assert res.status_code == 400 and word in res.text, (params, res.text)
    assert "Creature" in signed_in.get(CARDS, params={"type": "Sliver"}).text and "8+" in signed_in.get(CARDS, params={"mana_value": "9"}).text
    assert signed_in.get(CARDS, params={"mana_value": "1000"}).status_code == 400  # no bucket is that
    assert signed_in.get(CARDS, params={"sort": "price"}).status_code == 400


def test_an_assistant_asks_the_same_two_questions_through_search_cards(app, signed_in):
    from test_agents import call_tool, make_token, rpc

    synced(app, signed_in)
    token = make_token(signed_in)
    with TestClient(app) as bot:
        tool = next(t for t in rpc(bot, "tools/list", token=token).json()["result"]["tools"] if t["name"] == "search_cards")
        props = tool["inputSchema"]["properties"]
        assert set(props["type"]["enum"]) == set(analytics.TYPES) and {"mana_value", "-mana_value"} <= set(props["sort"]["enum"])
        dear = call_tool(bot, token, "search_cards", type="Creature", sort="-value")["structuredContent"]
        assert [c["name"] for c in dear["items"]] == ["Belfry Spirit", "A Killer Among Us"] or \
               [c["name"] for c in dear["items"]] == ["A Killer Among Us", "Belfry Spirit"]
        assert [c["name"] for c in call_tool(bot, token, "search_cards", mana_value=4)["structuredContent"]["items"]] == ["A Killer Among Us"]
        assert [c["name"] for c in call_tool(bot, token, "search_cards", mana_value="3")["structuredContent"]["items"]] == ["Belfry Spirit"]
        ordered = call_tool(bot, token, "search_cards", sort="mana_value")["structuredContent"]
        assert [c["name"] for c in ordered["items"]][0] == "Accursed Marauder"
        for arguments in ({"type": "Sliver"}, {"mana_value": "nine"}, {"mana_value": 9}):  # refused by the tool's own schema, not answered empty
            bad = rpc(bot, "tools/call", {"name": "search_cards", "arguments": arguments}, token).json()
            assert bad["error"]["code"] == -32602, arguments
