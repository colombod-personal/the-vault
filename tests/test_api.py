import base64
import csv
import json
from datetime import date
from pathlib import Path

import pytest
from mtg_toolkits.scryfall import Card

from sqlalchemy import event, select
from vault.sync import sync, wanted_cards

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
V1 = "/api/v1"


def upload(client, content=CSV, name="export.csv"):
    return client.post(f"{V1}/imports", files={"file": (name, content, "text/csv")})


def all_cards(client, path=f"{V1}/collection/cards", **params):
    """Follow `next` links to the end, like a client would."""
    items = []
    res = client.get(path, params=params)
    while True:
        body = res.json()
        items += body["items"]
        if "next" not in body["_links"]:
            return items
        res = client.get(body["_links"]["next"]["href"])


def card(id, name, set_code, number, finishes=("nonfoil",), **prices):
    return Card.from_json({
        "id": id, "name": name, "set": set_code, "collector_number": number, "finishes": list(finishes),
        "prices": {k: str(v) for k, v in prices.items()}, "type_line": "Artifact", "artist": "Mark Tedin",
        "image_uris": {"small": f"https://img.test/{id}-s.jpg", "normal": f"https://img.test/{id}.jpg"},
        "scryfall_uri": f"https://scryfall.com/card/{set_code}/{number}",
    })


BULK = [
    card("kil", "A Killer Among Us", "mkm", "167", usd=0.12),
    card("sol", "Sol Ring", "c21", "263", ("nonfoil", "foil"), usd=1.0, usd_foil=3.0),
    card("acc", "Accursed Marauder", "mh3", "512", ("etched",), usd_etched=0.8),
    card("bel", "Belfry Spirit", "gk2", "29", usd=0.25),
]


def test_root_is_discoverable(client):
    body = client.get(V1).json()
    assert body["signed_in"] is False and "collection" not in body["_links"] and body["_links"]["auth"]
    client.post("/api/auth/dev-login")
    links = client.get(V1).json()["_links"]
    assert {"me", "collection", "cards", "imports", "decks", "shares", "shared"} <= set(links)


def test_requires_sign_in_with_problem_details(client):
    res = client.get(f"{V1}/collection")
    assert res.status_code == 401 and res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["title"] == "Sign-in required" and res.json()["status"] == 401
    assert client.get("/api/auth/providers").json() == {"providers": [], "dev_login": True, "passkeys": False}


def test_import_summary_and_cards(signed_in):
    res = upload(signed_in)
    assert res.status_code == 201
    assert res.json()["changes"]["added"] == 4 and res.json()["copies"] == 7

    summary = signed_in.get(f"{V1}/collection").json()
    assert (summary["copies"], summary["printings"], summary["sets"]) == (7, 4, 4)
    assert summary["paid"] == 2.83 and summary["costs_hidden"] is False
    assert {"cards", "sets", "timeline", "history", "stats", "imports", "export"} <= set(summary["_links"])

    cards = all_cards(signed_in)
    killer = next(c for c in cards if c["name"] == "A Killer Among Us")
    # same grouping and totals as the prototype: 3 + 1 copies over two purchases
    assert (killer["quantity"], killer["paid"], killer["acquired"]) == (4, 0.23, {"first": "2024-02-17", "last": "2024-03-05"})
    assert killer["price"] == {"market": 0.09, "low": 0.01, "mid": 0.2, "currency": "USD", "source": "file"}
    assert next(c for c in cards if c["name"] == "Belfry Spirit")["set"]["code"] == "GK2_ORZHOV"
    detail = signed_in.get(killer["_links"]["self"]["href"]).json()
    assert [c["quantity"] for c in detail["copies"]] == [3, 1] and detail["card"] is None


def test_cursor_pagination_filters_and_sorting(signed_in):
    upload(signed_in)
    first = signed_in.get(f"{V1}/collection/cards", params={"limit": 3}).json()
    assert first["count"] == 3 and first["total"] == 4 and "next" in first["_links"]
    rest = signed_in.get(first["_links"]["next"]["href"]).json()
    assert rest["count"] == 1 and "next" not in rest["_links"]
    names = [c["name"] for c in first["items"] + rest["items"]]
    assert names == sorted(names, key=str.lower)
    assert [c["name"] for c in all_cards(signed_in, sort="-value", limit=1)][0] == "Sol Ring"
    assert [c["name"] for c in all_cards(signed_in, set="MKM")] == ["A Killer Among Us"]
    assert [c["name"] for c in all_cards(signed_in, q="ring")] == ["Sol Ring"]
    assert signed_in.get(f"{V1}/collection/cards", params={"sort": "nope"}).status_code == 400
    assert signed_in.get(f"{V1}/collection/cards", params={"cursor": "%%%"}).status_code == 400
    for forged in (1, {}, [None], [1]):  # valid JSON, but not a cursor this endpoint made
        cursor = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
        assert signed_in.get(f"{V1}/collection/cards", params={"cursor": cursor}).status_code == 400, forged
    assert signed_in.get(f"{V1}/collection/cards", params={"limit": 100000}).json()["count"] == 4  # capped, not an error


def test_pages_stay_small_for_huge_collections(signed_in):
    """No response grows with the collection: pages are capped at 500 items."""
    header = CSV.decode().split("\r\n")[:2]
    rows = [f"my cards,1,0,Card {i},SET{i % 300},Set {i % 300},{i},Mint,Normal,English,0.10,2024-01-01,0.01,0.20,0.15"
            for i in range(3000)]
    assert upload(signed_in, ("\r\n".join(header + rows) + "\r\n").encode()).status_code == 201
    page = signed_in.get(f"{V1}/collection/cards", params={"limit": 500}, headers={"Accept-Encoding": "gzip"})
    assert page.json()["count"] == 500 and page.json()["total"] == 3000
    assert len(page.content) < 400_000 and int(page.headers["content-length"]) < 100_000
    assert len(signed_in.get(f"{V1}/collection").content) < 5_000  # the summary doesn't list cards
    assert signed_in.get(f"{V1}/collection/sets", params={"limit": 500}).json()["total"] == 300


def test_etags_give_304_until_the_collection_changes(signed_in):
    upload(signed_in)
    res = signed_in.get(f"{V1}/collection/cards")
    tag = res.headers["etag"]
    assert signed_in.get(f"{V1}/collection/cards", headers={"If-None-Match": tag}).status_code == 304
    upload(signed_in, CSV.replace(b",3,0,", b",5,0,"))
    assert signed_in.get(f"{V1}/collection/cards", headers={"If-None-Match": tag}).status_code == 200


def test_reimport_records_changes_and_export_round_trips(signed_in):
    upload(signed_in)
    lines = CSV.decode().split("\r\n")
    changed = "\r\n".join([lines[0], lines[1], lines[2].replace(",3,0,", ",5,0,")] + lines[3:]).encode()
    res = upload(signed_in, changed).json()
    assert res["changes"]["increased"] == 1 and res["changes"]["copies_in"] == 2
    imports = signed_in.get(f"{V1}/imports").json()
    assert imports["total"] == 2 and imports["items"][0]["id"] == res["id"]  # newest first
    assert signed_in.get(imports["items"][0]["_links"]["self"]["href"]).json()["rows"] == 5
    assert signed_in.get(f"{V1}/collection/export.csv").content == changed


def test_bad_upload(signed_in):
    res = upload(signed_in, b"hello")
    assert res.status_code == 400 and res.json()["detail"].startswith("No cards found")


def test_daily_sync_card_data_history_and_stats(app, signed_in):
    upload(signed_in)
    with app.state.db.sessions() as db:
        stats = sync(db, BULK, day=date(2026, 9, 27))
    assert stats["methods"] == {"set_number": 5} and stats["unmatched"] == 0

    cards = {c["name"]: c for c in all_cards(signed_in)}
    assert (cards["Sol Ring"]["price"]["market"], cards["Sol Ring"]["finish"], cards["Sol Ring"]["price"]["source"]) == (3.0, "foil", "scryfall")
    assert (cards["Accursed Marauder"]["price"]["market"], cards["Accursed Marauder"]["finish"]) == (0.8, "etched")

    detail = signed_in.get(cards["Sol Ring"]["_links"]["self"]["href"]).json()
    assert detail["card"]["type_line"] == "Artifact"
    assert detail["card"]["image"] == {"small": "https://img.test/sol-s.jpg", "normal": "https://img.test/sol.jpg",
                                       "artist": "Mark Tedin", "credit": "Image via Scryfall · © Wizards of the Coast"}
    assert detail["price_history"] == [{"day": "2026-09-27", "price": 3.0}]
    assert detail["_links"]["scryfall"]["href"] == "https://scryfall.com/card/c21/263"

    summary = signed_in.get(f"{V1}/collection").json()
    assert summary["market_value"] == round(4 * 0.12 + 3.0 + 0.8 + 0.25, 2) and summary["prices_as_of"] == "2026-09-27"
    history = signed_in.get(f"{V1}/collection/history").json()
    synced = {"day": "2026-09-27", "market": 4.53, "cost": 2.83, "copies": 7, "priced": 7, "imported": False}
    assert history["items"][0] == synced  # the import's own day (today, at the file's prices) follows it
    st = signed_in.get(f"{V1}/collection/stats").json()
    assert st["most_valuable"][0]["name"] == "Sol Ring" and st["biggest_gains"][0]["name"] == "Sol Ring"
    months = signed_in.get(f"{V1}/collection/timeline").json()["months"]
    assert [m["month"] for m in months] == ["2022-11", "2023-01", "2024-02", "2024-03", "2024-06"]


def test_collection_items_carry_card_data_without_a_query_per_card(app, signed_in):
    upload(signed_in)
    assert [c["card"] for c in all_cards(signed_in)] == [None] * 4  # not synced yet: no card data, never a lookup
    with app.state.db.sessions() as db:
        sync(db, BULK, day=date(2026, 9, 27))

    cards = {c["name"]: c for c in all_cards(signed_in)}
    sol = cards["Sol Ring"]["card"]
    assert (sol["scryfall_id"], sol["set_code"], sol["collector_number"], sol["type_line"]) == ("sol", "c21", "263", "Artifact")
    assert sol["image"]["normal"] == "https://img.test/sol.jpg" and sol["image"]["artist"] == "Mark Tedin"
    assert (sol["prices"]["usd"], sol["prices"]["usd_foil"], sol["prices"]["day"]) == (1.0, 3.0, "2026-09-27")
    assert all(c["card"] for c in cards.values())

    statements = []
    listen = lambda *args: statements.append(args[2])  # noqa: E731
    engine = app.state.db.engine
    event.listen(engine, "before_cursor_execute", listen)
    try:
        counts = []
        for limit in (1, 4):  # the view is cached by now: a page costs the same however many cards it has
            statements.clear()
            assert signed_in.get(f"{V1}/collection/cards", params={"limit": limit}).json()["count"] == limit
            counts.append(len(statements))
    finally:
        event.remove(engine, "before_cursor_execute", listen)
    assert counts[0] == counts[1], counts


def test_sync_keeps_imported_finish_and_does_not_lock_in_name_guesses(app, signed_in):
    upload(signed_in)
    bulk = [card("kil", "A Killer Among Us", "mkm", "999", usd=0.12)] + BULK[1:]  # number mismatch -> name_set
    with app.state.db.sessions() as db:
        sync(db, bulk, day=date(2026, 9, 27))
        again = sync(db, bulk, day=date(2026, 9, 28))
    assert again["methods"] == {"name_set": 2}  # re-resolved each day, never relabelled as exact
    cards = {c["name"]: c for c in all_cards(signed_in)}
    assert (cards["Accursed Marauder"]["finish"], cards["Accursed Marauder"]["printing"]) == ("etched", "")
    res = upload(signed_in).json()
    assert res["changes"]["unchanged"] == 4 and res["changes"]["added"] == res["changes"]["removed"] == 0
    assert signed_in.get(f"{V1}/collection/export.csv").content == CSV
    sources = {c["name"]: c["price"]["source"] for c in all_cards(signed_in)}
    assert sources["Sol Ring"] == "scryfall" and sources["A Killer Among Us"] == "file"


def near_qty(client, name):
    return sum(c["quantity"] for c in all_cards(client) if c["name"] == name)


def test_deck_coverage_and_parsing(signed_in):
    upload(signed_in)
    res = signed_in.post(f"{V1}/decks/coverage", json={"text": "1 Sol Ring\n4 A Killer Among Us\n1 Rhystic Study"})
    status = {c["name"]: (c["status"], c["missing"]) for c in res.json()["cards"]}
    assert status == {"Sol Ring": ("owned", 0), "A Killer Among Us": ("owned", 0), "Rhystic Study": ("missing", 1)}
    assert all(c["maybe_owned"] == [] for c in res.json()["cards"])
    # Written a little differently in the deck (case, punctuation, accents, an Alchemy "A-"): still
    # missing by name, but the near match is pointed out with the copies owned.
    res = signed_in.post(f"{V1}/decks/coverage", json={"text": "1 A-Sol-Ring\n1 a killer among üs\n1 Rhystic Study"})
    near = {c["name"]: (c["status"], c["maybe_owned"]) for c in res.json()["cards"]}
    assert near["A-Sol-Ring"][1] == [{"name": "Sol Ring", "quantity": near_qty(signed_in, "Sol Ring")}]
    assert near["a killer among üs"][1] == [{"name": "A Killer Among Us", "quantity": near_qty(signed_in, "A Killer Among Us")}]
    assert near["Rhystic Study"] == ("missing", [])
    text = "1x Sol Ring (c21) 263 [Ramp]\n1x Duress [Sideboard]\n1 Kenrith, the Returned King (CMM) 1 *F*"
    cards = signed_in.post(f"{V1}/decks/parse", json={"text": text}).json()["cards"]
    assert [(c["name"], c["set"], c["collector_number"], c["section"]) for c in cards] == [
        ("Sol Ring", "c21", "263", "main"), ("Duress", "", "", "sideboard"), ("Kenrith, the Returned King", "cmm", "1", "main"),
    ]


def test_openapi_documents_the_api(client):
    spec = client.get("/api/openapi.json").json()
    paths = spec["paths"]
    for p in ["/api/v1", "/api/v1/collection", "/api/v1/collection/cards", "/api/v1/collection/cards/{card_id}",
              "/api/v1/auth/native/{provider}", "/api/v1/auth/token", "/api/v1/me/sessions", "/api/v1/decks"]:
        assert p in paths, p
    assert "CardItem" in spec["components"]["schemas"] and "TokenResponse" in spec["components"]["schemas"]


def test_uploads_are_read_only_up_to_the_limit(signed_in, monkeypatch):
    from starlette.datastructures import UploadFile

    from vault.importer import MAX_UPLOAD_BYTES

    asked = []
    real = UploadFile.read

    async def read(self, size=-1):
        asked.append(size)
        return await real(self, size)

    monkeypatch.setattr(UploadFile, "read", read)
    assert upload(signed_in).status_code == 201
    assert asked == [MAX_UPLOAD_BYTES + 1]  # never the whole body, whatever its size


def test_an_import_starts_the_value_history(signed_in):
    signed_in.post("/api/v1/imports", files={"file": ("e.csv", CSV, "text/csv")})
    days = signed_in.get("/api/v1/collection/history").json()["items"]
    assert len(days) == 1 and days[0]["copies"] == 7 and days[0]["market"] > 0
    assert days[0]["imported"] is True


def test_an_import_that_keeps_the_card_count_is_still_marked(app, signed_in):
    from datetime import timedelta
    from sqlalchemy import update
    from vault.models import CollectionValue, Import
    upload(signed_in)
    yesterday = date.today() - timedelta(days=1)
    with app.state.db.sessions() as db:  # yesterday's import, and a quiet day before it
        for imp in db.scalars(select(Import)):
            imp.created_at -= timedelta(days=1)
        db.execute(update(CollectionValue).values(day=yesterday))
        db.add(CollectionValue(user_id=db.scalar(select(CollectionValue.user_id)), day=yesterday - timedelta(days=1),
                               market_usd=1.0, cost_usd=1.0, copies=7, priced_copies=7))
        db.commit()
    upload(signed_in, CSV.replace(b"Sol Ring,C21,Commander 2021,263", b"Sol Ring,C21,Commander 2021,263 "))  # same count
    days = signed_in.get(f"{V1}/collection/history").json()["items"]
    assert [(d["copies"], d["imported"]) for d in days] == [(7, False), (7, True), (7, True)]


def test_the_bulk_prefilter_keeps_printings_matched_by_set_and_number(app, signed_in):
    upload(signed_in)  # Belfry Spirit is GK2_ORZHOV 29 in the file
    bulk = [{"object": "card", "id": "bel2", "name": "Belfry Spirit (Errata)", "set": "gk2", "collector_number": "29"},
            {"object": "card", "id": "x", "name": "Unrelated", "set": "gk2", "collector_number": "30"}]
    with app.state.db.sessions() as db:
        kept = [c.id for c in wanted_cards(db, bulk)]
        assert kept == ["bel2"]
        stats = sync(db, wanted_cards(db, bulk), day=date(2026, 9, 27))
    assert stats["methods"].get("set_number") == 1


ID_TOO_BIG = [2**31, 10**30]  # ids are 32-bit INTEGER columns on Postgres


@pytest.mark.parametrize("big", ID_TOO_BIG)
@pytest.mark.parametrize("method, path", [
    ("GET", "/decks/{}"), ("GET", "/imports/{}"), ("GET", "/shared/{}/deck"), ("GET", "/shared/{}/collection"),
    ("GET", "/shared/{}/collection/cards"), ("DELETE", "/me/sessions/{}"), ("DELETE", "/me/tokens/{}"),
    ("DELETE", "/me/passkeys/{}"), ("DELETE", "/shares/{}"), ("DELETE", "/decks/{}"), ("GET", "/archidekt/decks/{}"),
])
def test_ids_too_big_for_the_database_are_invalid_not_server_errors(signed_in, method, path, big):
    res = signed_in.request(method, V1 + path.format(big))
    assert res.status_code == 422, res.text


@pytest.mark.parametrize("big", ID_TOO_BIG + [1e30])
def test_body_ids_too_big_for_the_database_are_invalid(signed_in, big):
    assert signed_in.put(f"{V1}/decks/{2**31}", json={"name": "a", "text": "1 Sol Ring"}).status_code == 422
    res = signed_in.post(f"{V1}/shares", json={"kind": "deck", "deck_id": big})
    assert res.status_code == 422, res.text


@pytest.mark.parametrize("path, extra", [("/decks/parse", {}), ("/decks/coverage", {}), ("/decks", {"name": "x"})],
                         ids=["parse", "coverage", "save"])
def test_a_decklist_the_parser_cannot_read_is_a_bad_request(signed_in, monkeypatch, path, extra):
    res = signed_in.post(V1 + path, json={"text": "9" * 5000 + " Sol Ring", **extra})
    assert res.status_code in (200, 400), res.text  # mtg-toolkits >= 0.2.0: an unparsed line; before: ValueError

    from vault.api import v1

    def unreadable(text):  # whatever the library version raises it for
        raise ValueError("Exceeds the limit (4300 digits) for integer string conversion")

    monkeypatch.setattr(v1.decklist, "parse_text", unreadable)
    res = signed_in.post(V1 + path, json={"text": "1 Sol Ring", **extra})
    assert res.status_code == 400, res.text


DS_HEADER = ("Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,"
             "Language,Price Bought,Date Bought,LOW,MID,MARKET\n")
GENERIC_HEADER = "quantity,name,set_code,finish,condition,language,scryfall_id,source_prices,extra\n"


def ds_row(folder="f", qty="1", trade="0", name="Sol Ring", set_code="C21", set_name="Commander 2021", number="263",
           price="1", market="1"):
    return f"{folder},{qty},{trade},{name},{set_code},{set_name},{number},NearMint,Foil,English,{price},2023-01-10,1,1,{market}\n"


@pytest.mark.parametrize("content", [
    DS_HEADER + ds_row(qty="inf"),
    DS_HEADER + ds_row(qty="1e30"),
    DS_HEADER + ds_row(qty="-5"),
    DS_HEADER + ds_row(qty="1000001"),
    DS_HEADER + ds_row(trade="inf"),
    DS_HEADER + ds_row(trade="-1"),
    DS_HEADER + ds_row(name="N" * 200_000),  # larger than the csv module's field limit
    DS_HEADER + ds_row(set_code="S" * 21),  # identity: never clipped into another printing's code
    DS_HEADER + ds_row(number="9" * 31),
    GENERIC_HEADER + '1,Sol Ring,c21,nonfoil,near_mint,en,,"[1]",\n',
    GENERIC_HEADER + '1,Sol Ring,c21,nonfoil,near_mint,en,,,"[1]"\n',
    GENERIC_HEADER + '1,Sol Ring,c21,nonfoil,near_mint,en,,"{""a"": null}",\n',
    GENERIC_HEADER + "99999999999999999999999,Sol Ring,c21,nonfoil,near_mint,en,,,\n",
    GENERIC_HEADER + "1,Sol Ring,c21,nonfoil,near_mint,english-long,,,\n",
    GENERIC_HEADER + "1,Sol Ring,c21,nonfoil,near_mint,en," + "z" * 37 + ",,\n",
], ids=["qty-inf", "qty-1e30", "qty-negative", "qty-over-a-million", "trade-inf", "trade-negative", "huge-field",
        "long-set-code", "long-number", "prices-list", "extra-list", "price-null", "qty-huge", "long-language",
        "long-scryfall-id"])
def test_a_collection_file_with_values_the_vault_cannot_store_is_a_bad_request(signed_in, content):
    assert upload(signed_in).status_code == 201
    res = upload(signed_in, content.encode())
    assert res.status_code == 400, res.text
    assert signed_in.get(f"{V1}/collection").json()["copies"] == 7  # the collection is left as it was


@pytest.mark.parametrize("price, market", [("1e309", "1"), ("1", "1e309"), ("nan", "1"), ("1", "nan"),
                                           ("inf", "-inf"), ("1e300", "1e300")])
def test_prices_that_are_not_finite_numbers_are_dropped_on_import(signed_in, price, market):
    res = upload(signed_in, (DS_HEADER + ds_row(price=price, market=market) + ds_row(number="264")).encode())
    assert res.status_code == 201, res.text
    for path in ("", "/cards", "/stats", "/history", "/sets", "/timeline"):
        assert signed_in.get(f"{V1}/collection{path}").status_code == 200, path
    summary = signed_in.get(f"{V1}/collection").json()
    assert summary["copies"] == 2 and summary["market_value"] < 1e6 and (summary["paid"] or 0) < 1e6


def test_long_free_text_is_clipped_to_fit_on_import(app, signed_in):
    from vault.models import Entry
    content = DS_HEADER + ds_row(folder="D" * 300, name="N" * 400, set_name="X" * 300)
    assert upload(signed_in, content.encode()).status_code == 201
    with app.state.db.sessions() as db:
        e = db.scalar(select(Entry))
        assert (len(e.name), len(e.set_name), len(e.folder)) == (300, 200, 200)


def test_prices_already_stored_out_of_range_do_not_break_reading(app, signed_in):
    from vault.models import CollectionValue, Entry
    upload(signed_in)
    with app.state.db.sessions() as db:
        for e in db.scalars(select(Entry)):
            # Postgres floats hold infinity; its JSON can't, so the file's prices get the largest finite value
            e.purchase_price, e.source_prices = float("inf"), {"low": 1.7e308, "mid": 1.0, "market": 1.7e308}
        for v in db.scalars(select(CollectionValue)):
            v.market_usd, v.cost_usd = float("inf"), float("-inf")
        db.commit()
    for path in ("", "/cards", "/stats", "/history", "/sets", "/timeline"):
        res = signed_in.get(f"{V1}/collection{path}")
        assert res.status_code == 200 and "Infinity" not in res.text, path


def test_postgres_never_sees_a_value_too_big_for_its_column(database_url):
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from vault.app import create_app
    from vault.config import Settings
    from vault.db import Base, Database

    url = database_url
    with Database(url).engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    settings = Settings(database_url=url, session_secret="s" * 32, dev_login=True, base_url="http://testserver")
    with TestClient(create_app(settings, serve_static=False)) as client:
        assert client.post("/api/auth/dev-login").status_code == 200
        long_text = DS_HEADER + ds_row(folder="D" * 300, name="N" * 400, set_name="X" * 300)
        assert upload(client, long_text.encode(), name="F" * 400).status_code == 201
        assert upload(client, (DS_HEADER + ds_row(set_code="S" * 40)).encode()).status_code == 400
        assert upload(client, (GENERIC_HEADER + "1,Sol Ring,c21,nonfoil,near_mint,english-long,,,\n").encode()).status_code == 400
        assert client.get(f"{V1}/decks/{2**31}").status_code == 422
        assert client.post(f"{V1}/shares", json={"kind": "deck", "deck_id": 2**31}).status_code == 422
        deck = {"name": "x", "text": "1 Sol Ring", "source_url": "https://example.com/" + "a" * 600}
        assert client.post(f"{V1}/decks", json=deck).status_code == 422


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "ftp://example.com/deck", "https://",
                                 "https://archidekt.com/decks/" + "1" * 500, "//example.com/deck"],
                         ids=["javascript", "data", "ftp", "no-host", "too-long", "no-scheme"])
def test_a_deck_source_url_is_an_http_link_that_fits(signed_in, url):
    res = signed_in.post(f"{V1}/decks", json={"name": "x", "text": "1 Sol Ring", "source_url": url})
    assert res.status_code == 422, res.text
    deck = signed_in.post(f"{V1}/decks", json={"name": "x", "text": "1 Sol Ring"}).json()
    assert signed_in.put(f"{V1}/decks/{deck['id']}", json={"name": "x", "text": "1 Sol Ring",
                                                           "source_url": url}).status_code == 422


def test_updating_a_deck_keeps_its_source_url_unless_given(signed_in):
    url = "https://archidekt.com/decks/123"
    deck = signed_in.post(f"{V1}/decks", json={"name": "x", "text": "1 Sol Ring", "source_url": url}).json()
    assert deck["source_url"] == url
    path = f"{V1}/decks/{deck['id']}"
    assert signed_in.put(path, json={"name": "y", "text": "2 Sol Ring"}).json()["source_url"] == url
    other = "http://moxfield.com/decks/abc"
    assert signed_in.put(path, json={"name": "y", "text": "2 Sol Ring", "source_url": other}).json()["source_url"] == other
    assert signed_in.put(path, json={"name": "y", "text": "2 Sol Ring", "source_url": None}).json()["source_url"] is None
    assert signed_in.get(path).json()["source_url"] is None


@pytest.mark.parametrize("text", ["", "no cards here", "9" * 5000 + " Sol Ring"], ids=["empty", "no-cards", "huge-quantity"])
def test_updating_a_deck_needs_cards_like_creating_one(signed_in, text):
    deck = signed_in.post(f"{V1}/decks", json={"name": "x", "text": "1 Sol Ring"}).json()
    assert signed_in.put(f"{V1}/decks/{deck['id']}", json={"name": "x", "text": text}).status_code == 400
    assert signed_in.get(f"{V1}/decks/{deck['id']}").json()["text"] == "1 Sol Ring"


def test_a_deck_deleted_while_it_is_being_updated_is_not_found(app, signed_in, monkeypatch):
    from vault.api import v1
    from vault.models import Deck

    deck = signed_in.post(f"{V1}/decks", json={"name": "x", "text": "1 Sol Ring"}).json()
    real = v1.owned_deck

    def deleted_meanwhile(db, user, deck_id):
        found = real(db, user, deck_id)
        with app.state.db.sessions() as other:  # another request deletes it now
            other.delete(other.get(Deck, deck_id))
            other.commit()
        return found

    monkeypatch.setattr(v1, "owned_deck", deleted_meanwhile)
    res = signed_in.put(f"{V1}/decks/{deck['id']}", json={"name": "y", "text": "1 Sol Ring"})
    assert res.status_code == 404, res.text


@pytest.mark.parametrize("error", [ValueError("bad"), TypeError("bad"), AttributeError("bad"), OverflowError("bad"),
                                   csv.Error("field larger than field limit")])
def test_any_error_the_collection_parser_raises_is_a_bad_request(signed_in, monkeypatch, error):
    from vault import importer

    def broken(text):
        raise error

    monkeypatch.setattr(importer.formats, "parse", broken)
    res = upload(signed_in)
    assert res.status_code == 400 and res.json()["detail"].startswith("No cards found"), res.text


def entry(**overrides):
    from mtg_toolkits.models import CollectionEntry
    return CollectionEntry(**{"name": "Sol Ring", "quantity": 1, "set_code": "c21", "collector_number": "263",
                              **overrides})


@pytest.mark.parametrize("overrides", [
    {"quantity": 10**30}, {"quantity": -1}, {"quantity": 1_000_001}, {"quantity": float("inf")}, {"quantity": 2.5},
    {"quantity": "1"}, {"trade_quantity": float("nan")}, {"trade_quantity": -1}, {"name": ""}, {"name": None},
    {"set_code": "s" * 21}, {"collector_number": "1" * 31}, {"scryfall_id": "z" * 37}, {"language": "english"},
    {"set_name": 5},
], ids=lambda o: "-".join(f"{k}={str(v)[:12]}" for k, v in o.items()))
def test_the_importer_refuses_entries_it_cannot_store_whatever_the_parser_returns(overrides):
    """The app's own checks, independent of the mtg-toolkits version that parsed the file."""
    from vault.importer import ImportError_, _clean
    with pytest.raises(ImportError_):
        _clean(entry(**overrides), 1)


def test_the_importer_drops_bad_prices_and_clips_free_text_whatever_the_parser_returns():
    from vault.importer import _clean
    e = entry(quantity=3.0, purchase_price=float("inf"), name="N" * 400, set_name="X" * 300, folder="F" * 300,
              source_prices={"low": float("nan"), "mid": 1e300, "market": 2.5, "high": None, 5: 1.0},
              extra={"Printing": "Foil", "n": 1, "l": [1]})
    _clean(e, 1)
    assert (e.quantity, e.purchase_price, e.source_prices, e.extra) == (3, None, {"market": 2.5}, {"Printing": "Foil"})
    assert (len(e.name), len(e.set_name), len(e.folder)) == (300, 200, 200)
    bad = entry(source_prices=[1], extra=None)
    _clean(bad, 1)
    assert (bad.source_prices, bad.extra) == ({}, {})


def test_an_import_matches_known_printings_the_way_the_library_does(app, client):
    """Set aliases, letter case and zero-padding in a file's set and number still find a
    printing the server already knows, right at import (not only after the next sync)."""
    client.post("/api/auth/dev-login", params={"email": "first@example.com"})
    upload(client)
    with app.state.db.sessions() as db:
        sync(db, BULK, day=date(2026, 9, 27))
    client.cookies.clear()
    client.post("/api/auth/dev-login", params={"email": "second@example.com"})
    padded = CSV.replace(b"Sol Ring,C21,Commander 2021,263,", b"Sol Ring,c21,Commander 2021,0263,") \
                .replace(b",GK2,", b",GK2_ORZHOV,")
    assert upload(client, padded).status_code == 201
    cards = {c["name"]: c for c in all_cards(client)}
    assert cards["Sol Ring"]["price"]["source"] == "scryfall"


def test_pnl_counts_only_copies_with_a_known_cost(signed_in):
    # One printing, two rows: 1 copy bought at $1.00, 3 copies with no price paid, market $2.00.
    rows = ("Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,"
            "Price Bought,Date Bought,LOW,MID,MARKET\n"
            "a,1,0,Sol Ring,C21,Commander 2021,263,Mint,Normal,English,1.00,2024-01-01,1,2,2.00\n"
            "a,3,0,Sol Ring,C21,Commander 2021,263,Mint,Normal,English,,2024-01-02,1,2,2.00\n")
    assert upload(signed_in, rows.encode()).status_code in (200, 201)
    [sol] = all_cards(signed_in)
    assert (sol["quantity"], sol["paid"], sol["paid_quantity"], sol["gain"]) == (4, 1.0, 1, 1.0)
    st = signed_in.get(f"{V1}/collection/stats").json()
    assert st["biggest_gains"][0]["gain"] == 1.0  # 1 copy × $2 − $1, not 4 copies × $2 − $1


def test_the_card_schema_documents_paid_quantity(client):
    # /collection/cards answers with its own Response (for ETags), so the model never filters it;
    # the published contract has to list the field the front end relies on.
    item = client.get("/api/openapi.json").json()["components"]["schemas"]["CardItem"]["properties"]
    assert {"paid", "paid_quantity", "gain", "card"} <= set(item)


def test_daily_values_ignore_prices_no_import_would_accept(app, signed_in):
    """The daily totals read the same stored prices as the collection pages: a price stored
    before imports bounded them (infinite, or finite but huge) must not make a total infinite."""
    from vault.models import CollectionValue, Entry
    from vault.prices import compute_values
    upload(signed_in)
    with app.state.db.sessions() as db:
        for e in db.scalars(select(Entry)):
            e.purchase_price, e.source_prices = float("inf"), {"low": 1.0, "mid": 1.0, "market": 1.7e308}
        db.commit()
        compute_values(db, date(2026, 9, 28))
        [value] = db.scalars(select(CollectionValue).where(CollectionValue.day == date(2026, 9, 28))).all()
        assert (value.market_usd, value.cost_usd) == (0.0, 0.0)


@pytest.mark.parametrize("stored", [float("inf"), 1.7e308])
def test_an_implausible_scryfall_price_falls_back_to_the_files_price(stored):
    from types import SimpleNamespace

    from vault.prices import unit_price
    row = SimpleNamespace(price_finish=None, finish="nonfoil", source_prices={"market": 2.5})
    snap = SimpleNamespace(for_finish=lambda finish: stored)
    assert unit_price(row, snap) == (2.5, False)


def test_an_implausible_stored_price_does_not_break_a_cards_history(app, signed_in):
    from vault.models import PriceSnapshot
    upload(signed_in)
    with app.state.db.sessions() as db:
        sync(db, BULK, day=date(2026, 9, 27))
        db.add(PriceSnapshot(scryfall_id="sol", day=date(2026, 9, 28), usd=float("inf"), usd_foil=float("inf")))
        db.commit()
    sol = {c["name"]: c for c in all_cards(signed_in)}["Sol Ring"]
    res = signed_in.get(sol["_links"]["self"]["href"])
    assert res.status_code == 200 and "Infinity" not in res.text
    assert res.json()["price_history"] == [{"day": "2026-09-27", "price": 3.0}, {"day": "2026-09-28", "price": None}]


def test_the_data_export_has_only_finite_values_in_its_history(app, signed_in):
    import io
    import json
    import zipfile

    from vault.models import CollectionValue
    upload(signed_in)
    with app.state.db.sessions() as db:
        for v in db.scalars(select(CollectionValue)):
            v.market_usd, v.cost_usd = float("inf"), float("nan")
        db.commit()
    z = zipfile.ZipFile(io.BytesIO(signed_in.get(f"{V1}/me/export").content))
    text = z.read("value_history.json").decode()
    rows = json.loads(text, parse_constant=lambda c: pytest.fail(f"{c} in value_history.json"))
    assert rows and all(r["market"] is None and r["cost"] is None for r in rows)


def test_the_timeline_puts_each_copys_value_in_the_month_it_was_bought(signed_in):
    """A printing bought in several months (3 copies in February, 1 in March) adds each copy's
    market value and cost to its own month, not all of them to the first purchase."""
    upload(signed_in)
    months = {m["month"]: m for m in signed_in.get(f"{V1}/collection/timeline").json()["months"]}
    assert (months["2024-02"]["copies"], months["2024-02"]["market"], months["2024-02"]["paid"]) == (3, 0.27, 0.18)
    assert (months["2024-03"]["copies"], months["2024-03"]["market"], months["2024-03"]["paid"]) == (1, 0.09, 0.05)


def test_a_cards_detail_lists_a_bounded_number_of_copy_rows(signed_in):
    """One printing can come from many rows (folders, dates): the detail lists the first 500 and
    says how many there are, so the response stays small whatever the import holds."""
    rows = "".join(ds_row(folder=f"box {i}") for i in range(501))
    assert upload(signed_in, (DS_HEADER + rows).encode()).status_code == 201
    [sol] = all_cards(signed_in)
    detail = signed_in.get(sol["_links"]["self"]["href"]).json()
    assert (len(detail["copies"]), detail["copies_total"], detail["quantity"]) == (500, 501, 501)
