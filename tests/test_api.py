import base64
import json
from datetime import date
from pathlib import Path

from mtg_toolkits.scryfall import Card

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
    synced = {"day": "2026-09-27", "market": 4.53, "cost": 2.83, "copies": 7, "priced": 7}
    assert history["items"][0] == synced  # the import's own day (today, at the file's prices) follows it
    st = signed_in.get(f"{V1}/collection/stats").json()
    assert st["most_valuable"][0]["name"] == "Sol Ring" and st["biggest_gains"][0]["name"] == "Sol Ring"
    months = signed_in.get(f"{V1}/collection/timeline").json()["months"]
    assert [m["month"] for m in months] == ["2022-11", "2023-01", "2024-02", "2024-03", "2024-06"]


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


def test_deck_coverage_and_parsing(signed_in):
    upload(signed_in)
    res = signed_in.post(f"{V1}/decks/coverage", json={"text": "1 Sol Ring\n4 A Killer Among Us\n1 Rhystic Study"})
    status = {c["name"]: (c["status"], c["missing"]) for c in res.json()["cards"]}
    assert status == {"Sol Ring": ("owned", 0), "A Killer Among Us": ("owned", 0), "Rhystic Study": ("missing", 1)}
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


def test_the_bulk_prefilter_keeps_printings_matched_by_set_and_number(app, signed_in):
    upload(signed_in)  # Belfry Spirit is GK2_ORZHOV 29 in the file
    bulk = [{"object": "card", "id": "bel2", "name": "Belfry Spirit (Errata)", "set": "gk2", "collector_number": "29"},
            {"object": "card", "id": "x", "name": "Unrelated", "set": "gk2", "collector_number": "30"}]
    with app.state.db.sessions() as db:
        kept = [c.id for c in wanted_cards(db, bulk)]
        assert kept == ["bel2"]
        stats = sync(db, wanted_cards(db, bulk), day=date(2026, 9, 27))
    assert stats["methods"].get("set_number") == 1
