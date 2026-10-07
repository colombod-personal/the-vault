"""Type and mana value filters (and the mana value sort) on the collection's cards, for the app and for assistants (#284).

The signed-off review (docs/graph-and-lab-review.md) said two questions lose their only home when the old Graph is cut: the dearest
cards of one type, and mana value against price. This is the change it names: a type filter and a mana value filter (and sort) on
`/collection/cards`, which `search_cards` also reads.
"""

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from mtg_toolkits.scryfall import Card
from tests.ids import sid
from vault.sync import sync

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
V1 = "/api/v1"
CARDS = f"{V1}/collection/cards"


def card(id, name, set_code, number, type_line, cmc, finishes=("nonfoil",), **prices):
    return Card.from_json({
        "id": sid(id), "name": name, "set": set_code, "collector_number": number, "finishes": list(finishes),
        "prices": {k: str(v) for k, v in prices.items()}, "type_line": type_line, "cmc": cmc, "artist": "A",
        "image_uris": {"small": f"https://img.test/{id}-s.jpg", "normal": f"https://img.test/{id}.jpg"},
    })


BULK = [
    card("kil", "A Killer Among Us", "mkm", "167", "Enchantment", 4, usd=0.12),
    card("sol", "Sol Ring", "c21", "263", "Artifact", 1, ("nonfoil", "foil"), usd=1.0, usd_foil=3.0),
    card("acc", "Accursed Marauder", "mh3", "512", "Creature — Human Warrior", 2, ("etched",), usd_etched=0.8),
    card("bel", "Belfry Spirit", "gk2", "29", "Creature — Spirit", 3, usd=0.25),
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


def synced(app, client, bulk=BULK):
    assert client.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")}).status_code == 201
    with app.state.db.sessions() as db:
        sync(db, bulk, day=date(2026, 9, 27))


def test_type_matches_the_words_of_the_type_line_in_any_case_and_only_whole_words(app, signed_in):
    synced(app, signed_in)
    assert names(signed_in, type="Creature") == ["Accursed Marauder", "Belfry Spirit"]
    assert names(signed_in, type="creature") == ["Accursed Marauder", "Belfry Spirit"]
    assert names(signed_in, type="Spirit") == ["Belfry Spirit"]  # a subtype is a word of the type line too
    assert names(signed_in, type="Artifact") == ["Sol Ring"]
    assert names(signed_in, type="Art") == []  # not a whole word
    assert names(signed_in, type="Land") == []
    body = signed_in.get(CARDS, params={"type": "Creature"}).json()
    assert body["total"] == 2 and body["value_total"] == round(sum(i["value"] for i in body["items"]), 2)


def test_mana_value_is_exact_and_combines_with_the_other_filters(app, signed_in):
    synced(app, signed_in)
    assert names(signed_in, mana_value=3) == ["Belfry Spirit"]
    assert names(signed_in, mana_value=1) == ["Sol Ring"]
    assert names(signed_in, mana_value=0) == []
    assert names(signed_in, type="Creature", mana_value=2) == ["Accursed Marauder"]
    assert names(signed_in, type="Creature", mana_value=4) == []
    assert names(signed_in, type="Creature", set="mh3") == ["Accursed Marauder"]
    assert names(signed_in, q="spirit", mana_value=3) == ["Belfry Spirit"]


def test_the_mana_value_sort_pages_in_order_both_ways_and_keeps_the_filters_in_its_links(app, signed_in):
    synced(app, signed_in)
    assert names(signed_in, sort="mana_value") == ["Sol Ring", "Accursed Marauder", "Belfry Spirit", "A Killer Among Us"]
    assert names(signed_in, sort="-mana_value") == ["A Killer Among Us", "Belfry Spirit", "Accursed Marauder", "Sol Ring"]
    first = signed_in.get(CARDS, params={"sort": "mana_value", "limit": 1, "type": "Creature"}).json()
    assert [i["name"] for i in first["items"]] == ["Accursed Marauder"]
    href = first["_links"]["next"]["href"]
    assert "type=Creature" in href and "sort=mana_value" in href
    assert [i["name"] for i in signed_in.get(href).json()["items"]] == ["Belfry Spirit"]
    paged = []
    res = signed_in.get(CARDS, params={"sort": "-mana_value", "limit": 1})
    while True:  # one at a time, through the cursor, to the end: no card repeated or skipped
        body = res.json()
        paged += [i["name"] for i in body["items"]]
        if "next" not in body["_links"]:
            break
        res = signed_in.get(body["_links"]["next"]["href"])
    assert paged == ["A Killer Among Us", "Belfry Spirit", "Accursed Marauder", "Sol Ring"]


def test_printings_without_card_data_are_left_out_of_a_filter_and_sorted_last(app, signed_in):
    synced(app, signed_in, bulk=BULK[:2])  # only A Killer Among Us and Sol Ring have card data
    assert names(signed_in, type="Creature") == []  # not guessed from the name
    assert names(signed_in, mana_value=2) == []
    assert names(signed_in, sort="mana_value") == ["Sol Ring", "A Killer Among Us", "Accursed Marauder", "Belfry Spirit"]
    assert names(signed_in, sort="-mana_value") == ["A Killer Among Us", "Sol Ring", "Accursed Marauder", "Belfry Spirit"]


def test_bad_values_are_refused_with_a_reason(app, signed_in):
    synced(app, signed_in)
    assert signed_in.get(CARDS, params={"mana_value": -1}).status_code == 422
    assert signed_in.get(CARDS, params={"mana_value": "many"}).status_code == 422
    assert signed_in.get(CARDS, params={"type": ""}).status_code == 422
    assert signed_in.get(CARDS, params={"type": "x" * 41}).status_code == 422
    assert signed_in.get(CARDS, params={"sort": "price"}).status_code == 400


def test_an_assistant_asks_the_same_two_questions_through_search_cards(app, signed_in):
    from test_agents import call_tool, make_token, rpc

    synced(app, signed_in)
    token = make_token(signed_in)
    with TestClient(app) as bot:
        tool = next(t for t in rpc(bot, "tools/list", token=token).json()["result"]["tools"] if t["name"] == "search_cards")
        props = tool["inputSchema"]["properties"]
        assert {"type", "mana_value"} <= set(props) and {"mana_value", "-mana_value"} <= set(props["sort"]["enum"])
        dear = call_tool(bot, token, "search_cards", type="Creature", sort="-value")["structuredContent"]
        assert [c["name"] for c in dear["items"]] == ["Accursed Marauder", "Belfry Spirit"]
        four = call_tool(bot, token, "search_cards", mana_value=4)["structuredContent"]
        assert [c["name"] for c in four["items"]] == ["A Killer Among Us"]
        ordered = call_tool(bot, token, "search_cards", sort="mana_value")["structuredContent"]
        assert [c["name"] for c in ordered["items"]][0] == "Sol Ring"
