"""A file from a known app is never handed back to the person to fix (vault-0n9.1).

The shapes of a real Dragon Shield export whose 17 unmatched rows made an assistant ask three times for the file to be
fixed and exported again: a code that is the app's own (VTHB), a retired code with numbers the catalog does not have
(MB1 up to 1607, PHED, PAGL), a code that is no set at all (FWB), and a card the catalog does not hold. Every name here is
invented: no collection data is committed (CLAUDE.md)."""

import html
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.ids import sid
from vault.models import Card, Entry, OracleCard, OraclePrinting

V1 = "/api/v1"
HEADER = ("Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,"
          "Price Bought,Date Bought,LOW,MID,MARKET\r\n")


def row(name, code, number, qty=1):
    return f"Binder,{qty},0,{name},{code},Some Set,{number},NearMint,Normal,English,,,0.50,0.90,1.00\r\n"


def oracle(n, name, **kw):
    return OracleCard(oracle_id=sid(f"oracle-{n}"), name=name, content_hash="x", **kw)


def printing(n, key, set_code, number, usd, foil=None, lang="en"):
    return OraclePrinting(scryfall_id=sid(key), oracle_id=sid(f"oracle-{n}"), set_code=set_code, set_name=set_code.upper(),
                          collector_number=number, lang=lang, usd=usd, usd_foil=foil)


@pytest.fixture
def catalog(app):
    with app.state.db.sessions() as db:
        db.add_all([oracle(1, "Test Bolt"), oracle(2, "Test Daxos"), oracle(3, "Test List Card One"),
                    oracle(4, "Test List Card Two"), oracle(5, "Test List Card Three"), oracle(6, "Test Old Border"),
                    oracle(7, "Test Known But Unpriced")])
        db.add_all([
            printing(1, "bolt-m11", "m11", "149", 1.0), printing(1, "bolt-2xm", "2xm", "117", 2.0),
            printing(2, "dax-258", "thb", "258", 0.4), printing(2, "dax-7", "thb", "7", 0.1),
            printing(3, "list1", "plst", "CMR-395", 1.2), printing(4, "list2", "plst", "DOM-12", 0.7),
            printing(5, "list3-a", "plst", "A25-50", 3.0), printing(5, "list3-b", "plst", "MH2-267", 1.5),
            printing(6, "old-2ed", "2ed", "76", 5.0), printing(6, "old-3ed", "3ed", "76", 4.0),
        ])
        # a card someone owns already: the exact printing is found the way it always was, in `cards`
        db.add(Card(scryfall_id=sid("bolt-m11"), oracle_id=sid("oracle-1"), name="Test Bolt", set_code="m11",
                    collector_number="149"))
        db.commit()


FILE = (HEADER
        + row("Test Bolt", "M11", "149")  # the printing the file names, a card the Vault holds
        + row("Test Daxos", "VTHB", "258")  # Dragon Shield's own code: thb
        + row("Test List Card One", "MB1", "105") + row("Test List Card One", "MB1", "1607")
        + row("Test List Card Two", "MB1", "800") + row("Test List Card Three", "MB1", "1201")
        + row("Test List Card Two", "PHED", "74")
        + row("Test List Card Three", "PAGL", "45")  # two printings in The List: the rule picks one
        + row("Test Old Border", "FWB", "76")  # no such set, the number is in two
        + row("Test Known But Unpriced", "XYZ", "1")  # the catalog knows the card, has no printing to give
        + row("Test Nonexistent Card", "FWB", "9")).encode()

FORBIDDEN = re.compile(r"fix (the|them|it|those|these|your)|fixing in the file|re-?export|re-?upload|upload(ed)?( it| the file)? "
                       r"again|new upload|edit the file|correct the file", re.I)


def preview(client, content=FILE):
    res = client.post(f"{V1}/imports/preview", files={"file": ("my_cards.csv", content, "text/csv")})
    assert res.status_code == 200, res.text
    return res.json()


def test_every_row_of_the_17_row_shapes_is_matched_or_kept_and_none_is_asked_for(signed_in, catalog):
    p = preview(signed_in)
    assert p["rows"] == 11 and p["matched_rows"] + p["unmatched_rows"] == p["rows"]
    # exact: the card the Vault holds, and VTHB through its fallback; the rest found by name (6 rows in The List, FWB by number)
    assert p["matched_by"] == {"set_number": 2, "name_set": 6, "name_number": 1}
    assert p["printing_not_in_file_rows"] == 7 and p["matched_rows"] == 9
    assert (p["not_found_rows"], p["left_for_daily_update_rows"], p["unmatched_rows"]) == (1, 1, 2)
    assert {u["name"]: u["why"] for u in p["unmatched"]} == {"Test Known But Unpriced": "later", "Test Nonexistent Card": "not_found"}
    assert p["matching_line"] == (
        "All 11 rows are kept: 2 rows name the printing; 7 were found by card name or collector number because the file's "
        "set or number does not identify a printing the catalog has (their value follows the file's own price where it has "
        "one, until the printing is named; 3 of them could be more than one printing); 1 row is left for the daily update; "
        "1 row is not in the Vault's card list yet, kept as written and priced from the file where it has a price.")
    # the cards that could be several printings say so, with the range, never one price as if it were certain
    ruled = {r["name"]: r for r in p["found_by_rule"]}
    assert p["several_printings_rows"] == 3 and len(ruled) == 4
    assert ruled["Test Old Border"]["range"] == "could be any of 2 printings, between $4.00 and $5.00"
    assert (ruled["Test Old Border"]["candidates"], ruled["Test Old Border"]["highest_usd"]) == (2, 5.0)
    assert ruled["Test List Card One"]["candidates"] == 1 and ruled["Test List Card One"]["range"] == "printing priced $1.20"
    # the answer never tells the person (or the assistant) to fix the file
    assert not FORBIDDEN.search(p["note"]) and not FORBIDDEN.search(p["matching_line"]) and not FORBIDDEN.search(str(p))


def test_import_keeps_every_row_as_written_and_the_sync_ties_each_to_a_printing_by_the_stated_rule(signed_in, catalog, app):
    from datetime import date

    from mtg_toolkits.scryfall import Card as LibraryCard
    from vault.sync import sync

    assert signed_in.post(f"{V1}/imports", files={"file": ("my_cards.csv", FILE, "text/csv")}).status_code == 201

    def entries():
        with app.state.db.sessions() as db:
            rows = list(db.scalars(select(Entry).order_by(Entry.position)))
        assert len(rows) == 11  # nothing dropped
        return {(r.name, r.set_code, r.collector_number): (r.scryfall_id, r.match_method) for r in rows}

    by = entries()
    # at import what was always stored: the printing the Vault already holds. The rest (even VTHB/258, which names thb/258)
    # is left to the sync, which also knows the finish a printing exists in.
    assert by[("Test Bolt", "m11", "149")] == (sid("bolt-m11"), "set_number")
    assert all(v == (None, None) for k, v in by.items() if k[0] != "Test Bolt")

    def lib(key, name, set_code, number, usd):
        return LibraryCard.from_json({"id": sid(key), "name": name, "set": set_code, "collector_number": number,
                                      "prices": {"usd": str(usd)}, "finishes": ["nonfoil"]})

    bulk = [lib("bolt-m11", "Test Bolt", "m11", "149", 1.0), lib("dax-258", "Test Daxos", "thb", "258", 0.4),
            lib("list1", "Test List Card One", "plst", "CMR-395", 1.2), lib("list2", "Test List Card Two", "plst", "DOM-12", 0.7),
            lib("list3-a", "Test List Card Three", "plst", "A25-50", 3.0), lib("list3-b", "Test List Card Three", "plst", "MH2-267", 1.5),
            lib("old-2ed", "Test Old Border", "2ed", "76", 5.0), lib("old-3ed", "Test Old Border", "3ed", "76", 4.0)]
    with app.state.db.sessions() as db:
        sync(db, bulk, day=date(2026, 10, 12))  # the daily sync: the same ladder and the same rule
    by = entries()
    assert by[("Test Daxos", "vthb", "258")] == (sid("dax-258"), "set_number")  # as written: VTHB stays VTHB
    assert by[("Test List Card One", "mb1", "1607")] == (sid("list1"), "name_set")
    assert by[("Test List Card Two", "phed", "74")] == (sid("list2"), "name_set")
    assert by[("Test List Card Three", "pagl", "45")] == (sid("list3-b"), "name_set")  # the cheaper of its two printings
    assert by[("Test Old Border", "fwb", "76")] == (sid("old-3ed"), "name_number")  # cheaper of the two with that number
    assert by[("Test Known But Unpriced", "xyz", "1")] == (None, None)
    assert by[("Test Nonexistent Card", "fwb", "9")] == (None, None)
    # the file's own set codes and numbers are what an export writes back
    exported = signed_in.get(f"{V1}/collection/export.csv").text
    for code in ("VTHB", "MB1", "PHED", "PAGL", "FWB"):
        assert f",{code}," in exported


def test_a_second_import_of_the_same_file_changes_nothing_and_asks_nothing(signed_in, catalog):
    assert signed_in.post(f"{V1}/imports", files={"file": ("my_cards.csv", FILE, "text/csv")}).status_code == 201
    again = preview(signed_in)
    assert again["changes"]["added"] == 0 and again["changes"]["removed"] == 0
    assert again["matched_rows"] + again["unmatched_rows"] == again["rows"]
    assert not FORBIDDEN.search(str(again))


def test_a_file_with_nothing_in_the_catalog_is_still_kept_and_still_not_homework(signed_in):
    p = preview(signed_in, (HEADER + row("Test Bolt", "M11", "149") + row("Test Daxos", "VTHB", "258")).encode())
    assert p["matched_rows"] == 0 and p["unmatched_rows"] == 2 and p["not_found_rows"] == 2
    assert "kept" in p["matching_line"] and not FORBIDDEN.search(str(p))


def test_the_staged_upload_page_says_it_in_one_line_and_lists_set_aside_rows_as_optional(signed_in, catalog):
    started = signed_in.post(f"{V1}/uploads").json()
    ticket = started["url"].split("ticket=")[1]
    assert "nothing in it is for the person to fix" in started["next"]
    res = signed_in.post("/upload", data={"ticket": ticket}, files={"file": ("my_cards.csv", FILE, "text/csv")})
    text = html.unescape(res.text)
    assert res.status_code == 200 and "All 11 rows are kept" in text
    assert "nothing for you to do" in text and "Test Nonexistent Card" in text
    assert not FORBIDDEN.search(text)
    # what the assistant reads is the same preview
    staged = signed_in.get(f"{V1}/uploads/{started['id']}").json()
    assert staged["matching_line"].startswith("All 11 rows are kept") and not FORBIDDEN.search(str(staged))


def test_the_page_escapes_a_name_from_the_file(signed_in, catalog):
    evil = (HEADER + row("<script>alert(1)</script>", "ZZZ", "1")).encode()
    started = signed_in.post(f"{V1}/uploads").json()
    res = signed_in.post("/upload", data={"ticket": started["url"].split("ticket=")[1]},
                         files={"file": ("x.csv", evil, "text/csv")})
    assert "<script>alert(1)" not in res.text and "&lt;script&gt;" in res.text


def test_no_tool_description_or_skill_asks_the_person_to_fix_a_file_from_a_known_app():
    from vault.api import mcp

    root = Path(__file__).parent.parent
    texts = {t.name: t.description for t in mcp.TOOLS if "upload" in t.name or "import" in t.name}
    texts.update({str(p.relative_to(root)): p.read_text(encoding="utf-8")
                  for p in root.glob("**/collection-analyst/SKILL.md") if "node_modules" not in p.parts})
    assert len(texts) > 4
    for where, text in texts.items():
        # the skill may say "never ask ... to fix, re-export or re-upload": only an instruction to do it is a defect
        bad = [m.group(0) for m in FORBIDDEN.finditer(text)
               if not re.search(r"never|not |do not|don't", text[max(0, m.start() - 80):m.start()], re.I)]
        assert not bad, (where, bad)
    assert "fix them in the file" not in texts["get_staged_upload"] and "matching_line" in texts["get_staged_upload"]


def test_the_counts_for_a_file_with_the_17_row_shapes(signed_in, app):
    """13 rows with the retired code MB1 (numbers up to 1607; 3 of the cards are not in the catalog), 4 with VTHB, PHED, FWB and
    PAGL, and one ordinary row: what the preview answers, in numbers and in one line."""
    with app.state.db.sessions() as db:
        cards = [(n, f"Test Mystery {n}") for n in range(1, 11)]
        db.add_all([oracle(100 + n, name) for n, name in cards] + [oracle(200, "Test Variant"), oracle(201, "Test Promo"),
                                                                  oracle(202, "Test Foreign"), oracle(203, "Test Normal")])
        db.add_all([printing(100 + n, f"my-{n}", "plst", f"SET-{n}", 1.0 + n) for n, _ in cards]
                   + [printing(100 + n, f"my-{n}-b", "plst", f"OTH-{n}", 9.0) for n in (1, 2, 3)]  # three with two printings
                   + [printing(200, "variant", "thb", "258", 0.4), printing(201, "promo-a", "plst", "AAA-45", 2.0),
                      printing(202, "foreign-a", "3ed", "76", 4.0), printing(202, "foreign-b", "2ed", "76", 5.0),
                      printing(203, "normal", "m11", "149", 1.0)])
        db.commit()
    mb1 = [row(f"Test Mystery {n}", "MB1", str(105 + n * 150)) for n in range(1, 11)]  # up to 1605
    mb1 += [row("Test Missing A", "MB1", "1607"), row("Test Missing B", "MB1", "300"), row("Test Missing C", "MB1", "999")]
    file = (HEADER + "".join(mb1) + row("Test Variant", "VTHB", "258") + row("Test Promo", "PHED", "74")
            + row("Test Foreign", "FWB", "76") + row("Test Promo", "PAGL", "45") + row("Test Normal", "M11", "149")).encode()
    p = preview(signed_in, file)
    assert (p["rows"], p["matched_rows"], p["unmatched_rows"]) == (18, 15, 3)
    assert p["matched_by"] == {"set_number": 2, "name_set": 12, "name_number": 1}
    assert (p["printing_not_in_file_rows"], p["not_found_rows"], p["left_for_daily_update_rows"]) == (13, 3, 0)
    assert p["matching_line"] == (
        "All 18 rows are kept: 2 rows name the printing; 13 were found by card name or collector number because the file's "
        "set or number does not identify a printing the catalog has (their value follows the file's own price where it has "
        "one, until the printing is named; 4 of them could be more than one printing); 3 rows are not in the Vault's card "
        "list yet, kept as written and priced from the file where it has a price.")



def test_names_past_the_lookup_cap_are_left_for_the_daily_update_and_never_called_not_found(signed_in, catalog, monkeypatch):
    from vault import printing_lookup

    monkeypatch.setattr(printing_lookup, "MAX_LOOKUP_NAMES", 2)
    names = ["Test Bolt", "Test Daxos", "Test Old Border", "Test Nonexistent Card"]  # looked up in sorted order: two are left out
    file = (HEADER + "".join(row(n, "ZZZ", str(i)) for i, n in enumerate(names, 1))).encode()
    p = preview(signed_in, file)
    why = {u["name"]: u["why"] for u in p["unmatched"]}
    assert p["matched_rows"] == 2  # sorted: "test bolt" and "test daxos" are looked up and found by name
    assert why == {"Test Old Border": "later", "Test Nonexistent Card": "later"}
    assert p["not_found_rows"] == 0 and p["left_for_daily_update_rows"] == 2


def test_a_fallback_code_never_ties_a_row_to_another_card_with_that_number(signed_in, app):
    """VTHB/259 "Test Daxos" while thb/259 is another card: found by name in thb, never called the printing the file names."""
    with app.state.db.sessions() as db:
        db.add_all([oracle(1, "Test Daxos"), oracle(2, "Test Other Card"), printing(1, "dax-7", "thb", "7", 0.1),
                    printing(2, "other-259", "thb", "259", 3.0)])
        db.commit()
    p = preview(signed_in, (HEADER + row("Test Daxos", "VTHB", "259")).encode())
    assert p["matched_by"] == {"name_set": 1} and p["printing_not_in_file_rows"] == 1
    assert p["found_by_rule"][0]["row"] == 1 and p["found_by_rule"][0]["candidates"] == 1


def test_a_printing_found_in_the_catalog_is_left_to_the_sync_which_knows_its_finish(signed_in, app):
    """An etched-only printing the file calls Normal: the import must not store it with no finish (the sync would skip it)."""
    from datetime import date

    from mtg_toolkits.scryfall import Card as LibraryCard
    from vault.sync import sync

    with app.state.db.sessions() as db:
        db.add_all([oracle(1, "Test Etched"), OraclePrinting(scryfall_id=sid("etched-only"), oracle_id=sid("oracle-1"), set_code="cmm", set_name="CMM",
                                                      collector_number="600", lang="en", usd_etched=7.0)])
        db.commit()
    file = (HEADER + row("Test Etched", "CMM", "600")).encode()
    assert preview(signed_in, file)["matched_by"] == {"set_number": 1}  # the preview says it names a printing
    assert signed_in.post(f"{V1}/imports", files={"file": ("e.csv", file, "text/csv")}).status_code == 201
    with app.state.db.sessions() as db:
        [entry] = list(db.scalars(select(Entry)))
        assert entry.scryfall_id is None  # left to the sync, as before this change
        bulk = [LibraryCard.from_json({"id": sid("etched-only"), "name": "Test Etched", "set": "cmm", "collector_number": "600",
                                       "finishes": ["etched"], "prices": {"usd_etched": "7.00"}})]
        sync(db, bulk, day=date(2026, 10, 12))
        db.expire_all()
        [entry] = list(db.scalars(select(Entry)))
        assert (entry.scryfall_id, entry.match_method, entry.price_finish) == (sid("etched-only"), "set_number", "etched")
    [card] = signed_in.get(f"{V1}/collection/cards").json()["items"]
    assert (card["price"]["source"], card["price"]["market"]) == ("scryfall", 7.0)  # not the file's 1.00 as Normal


def test_a_card_found_by_name_keeps_the_files_own_price_and_a_named_printing_follows_the_catalog(signed_in, app):
    from datetime import date

    from mtg_toolkits.scryfall import Card as LibraryCard
    from vault.sync import sync

    def lib(key, name, set_code, number, usd):
        return LibraryCard.from_json({"id": sid(key), "name": name, "set": set_code, "collector_number": number,
                                      "prices": {"usd": str(usd)}, "finishes": ["nonfoil"]})

    file = (HEADER + row("Test Named", "ZZZ", "1") + row("Test By Name", "ZZZ", "2")).encode()  # file price 1.00 each
    assert signed_in.post(f"{V1}/imports", files={"file": ("n.csv", file, "text/csv")}).status_code == 201
    exact_file = (HEADER + row("Test Named", "QQQ", "5") + row("Test By Name", "ZZZ", "2")).encode()
    assert signed_in.post(f"{V1}/imports", files={"file": ("n2.csv", exact_file, "text/csv")}).status_code == 201
    bulk = [lib("named", "Test Named", "qqq", "5", 9.0), lib("by-name", "Test By Name", "yyy", "7", 30.0)]
    with app.state.db.sessions() as db:
        sync(db, bulk, day=date(2026, 10, 12))
    prices = {c["name"]: c["price"] for c in signed_in.get(f"{V1}/collection/cards").json()["items"]}
    assert prices["Test Named"]["source"] == "scryfall"  # the printing is named: the catalog's price
    assert prices["Test By Name"]["source"] == "file"  # found by name: the file's own price, not the $30 printing's
    # the SQL analytics follow the same rule: 1.00 (the file) + 9.00 (the named printing), not the $30 guess
    from vault import analytics
    from vault.models import User

    with app.state.db.sessions() as db:
        history = analytics.scoped_history(db, db.scalar(select(User.id)), [date(2026, 10, 12)])
    assert history[date(2026, 10, 12)]["market"] == 10.0
    # and an unchanged re-import keeps what the sync resolved by name (the value does not flip to nothing until the next sync)
    assert preview(signed_in, exact_file)["changes"]["unchanged"] == 2
    assert signed_in.post(f"{V1}/imports", files={"file": ("n3.csv", exact_file, "text/csv")}).status_code == 201
    with app.state.db.sessions() as db:
        by = {e.name: (e.scryfall_id, e.match_method) for e in db.scalars(select(Entry))}
    assert by["Test By Name"] == (sid("by-name"), "name")


def test_a_nul_character_in_a_file_is_refused_not_a_server_error(signed_in):
    bad = (HEADER + row("Test Bolt\x00", "M11", "149")).encode()
    res = signed_in.post(f"{V1}/imports/preview", files={"file": ("n.csv", bad, "text/csv")})
    assert res.status_code == 400 and "no card file contains" in res.json()["detail"]
    started = signed_in.post(f"{V1}/uploads").json()
    page = signed_in.post("/upload", data={"ticket": started["url"].split("ticket=")[1]},
                          files={"file": ("n.csv", bad, "text/csv")})
    assert page.status_code == 200 and "could not be read" in page.text


def test_the_preview_finds_an_exact_printing_by_set_and_number_even_when_the_name_is_not_the_catalogs(signed_in, app):
    """The daily sync matches M11/149 exactly whatever the row's name is; the preview must say the same (review thread)."""
    with app.state.db.sessions() as db:
        db.add_all([oracle(1, "Test Bolt"), printing(1, "bolt-m11", "m11", "149", 1.0), oracle(2, "Test Daxos"),
                    printing(2, "dax-258", "thb", "258", 0.4)])
        db.commit()
    p = preview(signed_in, (HEADER + row("Tést Bolt", "M11", "149") + row("Localised Daxos", "VTHB", "258")
                            + row("Test Daxos", "VTHB", "258")).encode())
    # the first row is exact by its set and number; a fallback code needs the card's name, so the second is not exact
    # and not found (the sync agrees: the library checks the name there too); the third is exact through the fallback
    assert p["matched_by"] == {"set_number": 2} and p["unmatched_rows"] == 1
    assert p["unmatched"][0]["name"] == "Localised Daxos"


def test_a_back_face_name_and_a_nul_in_an_extra_key(signed_in, app):
    from mtg_toolkits.models import CollectionEntry

    from vault.importer import ImportError_, _clean

    with app.state.db.sessions() as db:
        db.add_all([oracle(1, "Test Fire // Test Ice"), printing(1, "fi", "mh2", "290", 0.3)])
        db.commit()
    p = preview(signed_in, (HEADER + row("Test Ice", "ZZZ", "1")).encode())
    assert p["matched_by"] == {"name": 1} and p["unmatched_rows"] == 0  # the back face, as the sync reads it
    bad = CollectionEntry("Test Bolt", extra={"Col\x00umn": "x"})
    try:
        _clean(bad, 3)
    except ImportError_ as exc:
        assert "Row 3" in str(exc)
    else:
        raise AssertionError("a NUL in an extra key must be refused")
