"""A collection file that names no set (a Moxfield export without an edition) is matched by card name only.
Found by importing such a file and looking at the app: every card was filed under one arbitrary set
("1 sets", "Secrets of Strixhaven Commander: 447 cards"). The Vault now says the printing is not specified."""

from datetime import date

from tests.test_analytics import BULK
from vault.sync import sync

V1 = "/api/v1"
MOXFIELD = (b"Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number,Alter,Proxy,Purchase Price\r\n"
            b"2,0,\"Sol Ring\",,NM,English,,,2024-02-18,,,,1.50\r\n"
            b"1,0,\"A Killer Among Us\",mkm,NM,English,,,2024-02-18,167,,,0.10\r\n")


def test_cards_without_a_set_are_not_filed_under_an_arbitrary_one(app, signed_in):
    assert signed_in.post(f"{V1}/imports", files={"file": ("moxfield.csv", MOXFIELD, "text/csv")}).status_code == 201
    with app.state.db.sessions() as db:
        sync(db, BULK, day=date(2026, 9, 27))
    summary = signed_in.get(f"{V1}/collection").json()
    assert summary["copies"] == 3 and summary["sets"] == 1  # only the card whose file named a set counts as one
    sets = {s["code"]: s for s in signed_in.get(f"{V1}/collection/sets").json()["items"]}
    assert sets[""]["name"] == "Printing not specified" and sets[""]["copies"] == 2
    assert "Commander 2021" not in {s["name"] for s in sets.values()}  # Sol Ring's matched printing is C21: not claimed
    assert sets["MKM"]["copies"] == 1 and sets["MKM"]["name"] != "Printing not specified"
