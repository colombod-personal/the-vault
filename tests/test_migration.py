"""Moving collections in and out: Moxfield (and generic CSV) import, exports in every format."""

import csv
import io
import zipfile
from datetime import date

from test_api import BULK, CSV, V1, all_cards, upload

from tests.ids import sid
from vault.sync import sync

MOXFIELD = (
    "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number,Alter,Proxy,Purchase Price\n"
    "4,0,A Killer Among Us,mkm,Mint,English,,my cards,2024-02-17 10:00:00.000000,167,False,False,0.06\n"
    "1,0,Sol Ring,c21,Near Mint,English,foil,my cards,2023-01-10 10:00:00.000000,263,False,False,2.00\n"
    "1,0,Accursed Marauder,mh3,Mint,English,etched,my cards,2024-06-20 10:00:00.000000,512,False,False,0.50\n"
    "1,0,Belfry Spirit,gk2,Mint,English,,my cards,2022-11-20 10:00:00.000000,29,False,False,0.10\n"
).encode()


def synced(app, day=date(2026, 9, 27)):
    with app.state.db.sessions() as db:
        return sync(db, BULK, day=day)


def download(client, fmt):
    res = client.get(f"{V1}/collection/export/{fmt}")
    assert res.status_code == 200, res.text
    return res


def test_moxfield_import(signed_in):
    res = upload(signed_in, MOXFIELD, "moxfield.csv")
    assert res.status_code == 201 and res.json()["source"] == "moxfield"
    assert (res.json()["rows"], res.json()["copies"]) == (4, 7)
    summary = signed_in.get(f"{V1}/collection").json()
    assert (summary["copies"], summary["printings"], summary["paid"], summary["source"]) == (7, 4, 2.84, "moxfield")
    cards = {c["name"]: c for c in all_cards(signed_in)}
    assert (cards["Sol Ring"]["finish"], cards["Accursed Marauder"]["finish"]) == ("foil", "etched")
    assert cards["A Killer Among Us"]["acquired"]["first"] == "2024-02-17"


def test_known_printings_are_priced_at_import_time(app, signed_in):
    upload(signed_in)  # someone's Dragon Shield file...
    synced(app)        # ...so the server knows these printings and their prices
    signed_in.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"})
    signed_in.post("/api/auth/dev-login", params={"email": "new@example.com"})
    upload(signed_in, MOXFIELD, "moxfield.csv")  # a new user arriving from Moxfield
    cards = {c["name"]: c for c in all_cards(signed_in)}
    assert cards["Sol Ring"]["price"]["source"] == "scryfall" and cards["Sol Ring"]["price"]["market"] == 3.0
    assert {c["name"]: c["scryfall_id"] for c in cards.values()} == {
        "A Killer Among Us": sid("kil"), "Sol Ring": sid("sol"), "Accursed Marauder": sid("acc"), "Belfry Spirit": sid("bel")}


def test_unknown_files_name_the_supported_formats(signed_in):
    res = upload(signed_in, b"some,other\n1,2\n")
    assert res.status_code == 400 and "Dragon Shield, Moxfield, Generic CSV" in res.json()["detail"]


def test_export_formats_are_listed_and_linked(signed_in):
    upload(signed_in)
    formats = signed_in.get(f"{V1}/collection/exports").json()["items"]
    assert [f["format"] for f in formats] == ["dragonshield", "moxfield", "archidekt", "csv", "text"]
    assert {f["format"]: f["reimportable"] for f in formats}["moxfield"] is True
    assert signed_in.get(f"{V1}/collection").json()["_links"]["exports"]["href"] == f"{V1}/collection/exports"
    for f in formats:
        res = signed_in.get(f["_links"]["download"]["href"])
        assert res.status_code == 200 and f"vault-collection-{f['format']}-" in res.headers["content-disposition"]
    assert signed_in.get(f"{V1}/collection/export/nope").status_code == 404


def test_exports_use_scryfall_codes_and_real_finishes(app, signed_in):
    upload(signed_in)
    synced(app)
    assert download(signed_in, "dragonshield").content == CSV  # still byte for byte
    mox = list(csv.DictReader(io.StringIO(download(signed_in, "moxfield").text)))
    by_name = {r["Name"]: r for r in mox}
    assert by_name["Belfry Spirit"]["Edition"] == "gk2"  # Dragon Shield's GK2_ORZHOV means nothing to Moxfield
    assert by_name["Accursed Marauder"]["Foil"] == "etched"  # blank Printing in Dragon Shield, etched-only card
    assert by_name["Sol Ring"]["Foil"] == "foil" and by_name["Sol Ring"]["Tags"] == "my cards"
    generic = list(csv.DictReader(io.StringIO(download(signed_in, "csv").text)))
    assert {r["name"]: r["scryfall_id"] for r in generic}["Sol Ring"] == sid("sol")
    archidekt = download(signed_in, "archidekt").text
    assert "Scryfall ID" in archidekt.splitlines()[0] and "," + sid("sol") in archidekt
    assert download(signed_in, "text").text.splitlines()[0] == "4 A Killer Among Us (MKM) 167"
    assert download(signed_in, "text").headers["content-type"].startswith("text/plain")


def test_round_trip_through_moxfield_keeps_the_collection(app, signed_in):
    upload(signed_in)
    synced(app)
    before = signed_in.get(f"{V1}/collection").json()
    res = upload(signed_in, download(signed_in, "moxfield").content, "from-vault.csv").json()
    assert res["source"] == "moxfield" and res["copies"] == before["copies"]
    after = signed_in.get(f"{V1}/collection").json()
    assert (after["copies"], after["printings"], after["market_value"], after["paid"]) == (
        before["copies"], before["printings"], before["market_value"], before["paid"])
    assert all(c["price"]["source"] == "scryfall" for c in all_cards(signed_in))  # every printing still matched


def test_generic_csv_round_trip_is_lossless(app, signed_in):
    upload(signed_in)
    synced(app)
    generic = download(signed_in, "csv").content
    assert upload(signed_in, generic, "generic.csv").json()["source"] == "csv"
    assert download(signed_in, "csv").content == generic


def test_exports_are_owner_only_and_in_the_data_download(signed_in, client):
    upload(signed_in)
    share = signed_in.post(f"{V1}/shares", json={"kind": "collection"}).json()
    assert signed_in.get(f"/api/v1/shared/{share['id']}/collection/exports").status_code == 404
    z = zipfile.ZipFile(io.BytesIO(signed_in.get(f"{V1}/me/export").content))
    assert {"collection.csv", "collection-moxfield.csv", "collection-generic.csv"} <= set(z.namelist())


def test_a_fresh_generic_import_keeps_its_scryfall_ids_before_any_sync(signed_in):
    """Before the first daily sync the Vault has no card records yet; an id the file carried is
    still the file's, and exporting must give it back."""
    from mtg_toolkits.formats import GENERIC_COLUMNS

    row = {"quantity": "1", "trade_quantity": "0", "name": "Sol Ring", "set_code": "c21", "collector_number": "263",
           "finish": "nonfoil", "scryfall_id": "9a1b2c3d-0000-4000-8000-000000000001"}
    content = (",".join(GENERIC_COLUMNS) + "\n" + ",".join(row.get(c, "") for c in GENERIC_COLUMNS) + "\n").encode()
    assert upload(signed_in, content, "generic.csv").json()["source"] == "csv"
    generic = list(csv.DictReader(io.StringIO(download(signed_in, "csv").text)))
    assert generic[0]["scryfall_id"] == "9a1b2c3d-0000-4000-8000-000000000001"
