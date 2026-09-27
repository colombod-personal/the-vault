from datetime import date
from pathlib import Path

from mtg_toolkits.scryfall import Card

from vault.sync import sync

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()


def upload(client, content=CSV, name="export.csv"):
    return client.post("/api/imports", files={"file": (name, content, "text/csv")})


def test_requires_sign_in(client):
    assert client.get("/api/collection").status_code == 401
    assert client.get("/api/auth/providers").json() == {"providers": [], "dev_login": True}


def test_import_and_collection_json(signed_in):
    res = upload(signed_in)
    assert res.status_code == 200
    assert res.json()["changes"]["added"] == 4 and res.json()["copies"] == 7

    data = signed_in.get("/api/collection").json()
    assert set(data) >= {"meta", "sets", "timeline", "cards", "byName", "history"}
    killer = next(c for c in data["cards"] if c["n"] == "A Killer Among Us")
    # same grouping and totals as the prototype's collection.json
    assert (killer["q"], killer["pd"], killer["fd"], killer["ld"], killer["mk"]) == (4, 0.23, "2024-02-17", "2024-03-05", 0.09)
    assert killer["src"] == "file"
    belfry = next(c for c in data["cards"] if c["n"] == "Belfry Spirit")
    assert belfry["s"] == "GK2_ORZHOV"  # shown with the file's set code
    assert data["meta"]["totalQty"] == 7 and data["meta"]["uniqueEntries"] == 4
    assert data["byName"]["a killer among us"]["total"] == 4


def test_reimport_records_changes_and_export_round_trips(signed_in):
    upload(signed_in)
    lines = CSV.decode().split("\r\n")
    changed = "\r\n".join([lines[0], lines[1], lines[2].replace(",3,0,", ",5,0,")] + lines[3:]).encode()
    res = upload(signed_in, changed).json()
    assert res["changes"]["increased"] == 1 and res["changes"]["copies_in"] == 2
    assert len(signed_in.get("/api/imports").json()) == 2
    assert signed_in.get("/api/collection/export.csv").content == changed


def test_bad_upload(signed_in):
    assert upload(signed_in, b"hello").status_code == 400


def card(id, name, set_code, number, finishes=("nonfoil",), **prices):
    return Card.from_json({
        "id": id, "name": name, "set": set_code, "collector_number": number, "finishes": list(finishes),
        "prices": {k: str(v) for k, v in prices.items()}, "type_line": "Artifact",
    })


def test_daily_sync_prices_and_history(app, signed_in):
    upload(signed_in)
    bulk = [
        card("kil", "A Killer Among Us", "mkm", "167", usd=0.12),
        card("sol", "Sol Ring", "c21", "263", ("nonfoil", "foil"), usd=1.0, usd_foil=3.0),
        card("acc", "Accursed Marauder", "mh3", "512", ("etched",), usd_etched=0.8),
        card("bel", "Belfry Spirit", "gk2", "29", usd=0.25),
    ]
    with app.state.db.sessions() as db:
        stats = sync(db, bulk, day=date(2026, 9, 27))
    assert stats["methods"] == {"set_number": 5} and stats["unmatched"] == 0

    data = signed_in.get("/api/collection").json()
    prices = {c["n"]: (c["mk"], c["fin"], c["src"]) for c in data["cards"]}
    assert prices["Sol Ring"] == (3.0, "foil", "scryfall")
    assert prices["Accursed Marauder"] == (0.8, "etched", "scryfall")  # blank Printing fixed to etched
    assert data["meta"]["totalMarket"] == round(4 * 0.12 + 3.0 + 0.8 + 0.25, 2)
    assert data["history"] == [{"day": "2026-09-27", "market": 4.53, "cost": 2.83, "copies": 7, "priced": 7}]


def test_deck_coverage(signed_in):
    upload(signed_in)
    res = signed_in.post("/api/decks/coverage", json={"text": "1 Sol Ring\n4 A Killer Among Us\n1 Rhystic Study"})
    status = {c["name"]: (c["status"], c["missing"]) for c in res.json()["cards"]}
    assert status == {"Sol Ring": ("owned", 0), "A Killer Among Us": ("owned", 0), "Rhystic Study": ("missing", 1)}




def test_sync_keeps_imported_finish_and_does_not_lock_in_name_guesses(app, signed_in):
    upload(signed_in)
    bulk = [
        card("kil", "A Killer Among Us", "mkm", "999", usd=0.12),          # number mismatch -> name_set match
        card("sol", "Sol Ring", "c21", "263", ("nonfoil", "foil"), usd=1.0, usd_foil=3.0),
        card("acc", "Accursed Marauder", "mh3", "512", ("etched",), usd_etched=0.8),
        card("bel", "Belfry Spirit", "gk2", "29", usd=0.25),
    ]
    with app.state.db.sessions() as db:
        sync(db, bulk, day=date(2026, 9, 27))
        again = sync(db, bulk, day=date(2026, 9, 28))
    # the name_set guess is re-resolved each day, never relabelled as an exact "id" match
    assert again["methods"] == {"name_set": 2}
    data = signed_in.get("/api/collection").json()
    acc = next(c for c in data["cards"] if c["n"] == "Accursed Marauder")
    assert (acc["fin"], acc["mk"], acc["p"]) == ("etched", 0.8, "")  # priced as etched, imported Printing kept
    # re-importing the unchanged file reports no changes and keeps the exact matches
    res = upload(signed_in).json()
    assert res["changes"]["unchanged"] == 4 and res["changes"]["added"] == res["changes"]["removed"] == 0
    assert signed_in.get("/api/collection/export.csv").content == CSV
    prices = {c["n"]: c["src"] for c in signed_in.get("/api/collection").json()["cards"]}
    assert prices["Sol Ring"] == "scryfall" and prices["A Killer Among Us"] == "file"  # guess not carried over


def test_parse_deck_uses_library_parser(signed_in):
    text = "1x Sol Ring (c21) 263 [Ramp]\n1x Duress [Sideboard]\n1 Kenrith, the Returned King (CMM) 1 *F*"
    cards = signed_in.post("/api/decks/parse", json={"text": text}).json()["cards"]
    assert [(c["name"], c["set"], c["collector_number"], c["section"]) for c in cards] == [
        ("Sol Ring", "c21", "263", "main"), ("Duress", "", "", "sideboard"), ("Kenrith, the Returned King", "cmm", "1", "main"),
    ]
