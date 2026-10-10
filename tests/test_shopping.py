"""Shopping lists (#29, #55): the cheapest printing that fits a person's rules (finish, language, set, condition), and the list written
the way each store's own tool reads it. The store syntaxes below are what the stores' help pages show (docs/data-sources.md);
the Vault never contacts a store."""

import csv
import io
import re
from datetime import date
from pathlib import Path

import pytest

from tests.test_deck_api import PRICES, TODAY, oid
from tests.test_deck_api import loaded as _catalog  # noqa: F401  (the catalog with prices)
from vault import catalog_sync as cs
from vault import shopping as shop
from vault.models import OraclePrinting

V1 = "/api/v1/decks"
DECK = "1 Test Burn\n2 Cheap Ramp\n1 Test Rock"
PRINTING_DAY = date(2026, 10, 5)


def printing(n, scryfall_id, set_code, set_name, number, usd=None, foil=None, etched=None, lang="en"):
    return {"scryfall_id": scryfall_id, "oracle_id": oid(n), "set_code": set_code, "set_name": set_name, "collector_number": number,
            "lang": lang, "usd": usd, "usd_foil": foil, "usd_etched": etched}


PRINTINGS = [
    printing(4, "burn-mh2", "mh2", "Modern Horizons 2", "10", usd=0.5, foil=1.2),
    printing(4, "burn-2xm", "2xm", "Double Masters", "20", usd=0.3, foil=0.9),
    printing(4, "burn-sld", "sld", "Secret Lair Drop", "5", usd=4.0, foil=3.5, etched=9.0),
    printing(4, "burn-war", "war", "War of the Spark", "119", usd=0.2, lang="ja"),
    printing(7, "ramp-m20", "m20", "Core Set 2020", "1", foil=0.4),  # exists in foil only
    printing(3, "rock-ltr", "ltr", "The Lord of the Rings: Tales of Middle-earth", "1", usd=1.0),
]


@pytest.fixture
def shopping(_catalog, app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_printings(db, PRINTINGS)
        cs.record_source(db, "oracle_printings", version=PRINTING_DAY.isoformat(), rows=len(PRINTINGS))
        db.commit()
    return _catalog


def ask(client, **body):
    res = client.post(f"{V1}/shopping-list", json={"text": DECK, **body})
    assert res.status_code == 200, res.text
    return res.json()["result"]


def lines_of(result):
    return {l["name"]: l for l in result["lines"]}


# -- no rules: what it always did ------------------------------------------------------------------------------

def test_without_rules_it_prices_the_cheapest_priced_printing_as_before_and_chooses_no_printing(shopping):
    r = ask(shopping)
    assert r["rules"] is None and r["format"] == "plain" and r["no_qualifying_printing"] == 0
    burn = lines_of(r)["Test Burn"]
    assert (burn["unit_price_usd"], burn["price_date"], burn.get("printing")) == (0.5, TODAY.isoformat(), None)
    assert "no printing is chosen" in r["price_basis"]
    assert set(r["text"].splitlines()) == {"1 Test Burn", "2 Cheap Ramp", "1 Test Rock"}
    assert r["total_usd"] == round(0.5 + 2 * 0.25 + 1.0, 2)


# -- choosing a printing under rules ---------------------------------------------------------------------------

def test_the_cheapest_printing_that_fits_the_sets_is_chosen_and_reported(shopping):
    r = ask(shopping, sets=["MH2", "sld"])
    burn = lines_of(r)["Test Burn"]
    # of MH2 (0.50 nonfoil, 1.20 foil) and SLD (4.00, 3.50, 9.00 etched): the 0.50 MH2 nonfoil
    assert burn["printing"] == {"scryfall_id": "burn-mh2", "set": "MH2", "set_name": "Modern Horizons 2", "collector_number": "10",
                                "finish": "nonfoil", "language": "en"}
    assert (burn["unit_price_usd"], burn["price_date"]) == (0.5, PRINTING_DAY.isoformat())
    assert "fits your rules" in r["price_basis"]
    assert r["rules"] == {"finish": None, "language": None, "sets": ["mh2", "sld"], "condition": None}


def test_the_finish_rule_picks_the_cheapest_printing_in_that_finish(shopping):
    foil = lines_of(ask(shopping, finish="foil"))
    assert (foil["Test Burn"]["printing"]["set"], foil["Test Burn"]["printing"]["finish"], foil["Test Burn"]["unit_price_usd"]) == ("2XM", "foil", 0.9)
    assert (foil["Cheap Ramp"]["printing"]["set"], foil["Cheap Ramp"]["unit_price_usd"]) == ("M20", 0.4)
    etched = lines_of(ask(shopping, finish="etched"))
    assert etched["Test Burn"]["printing"]["set"] == "SLD" and etched["Test Burn"]["unit_price_usd"] == 9.0
    # no finish rule: whichever finish of whichever printing is cheapest, which can be a foil-only printing
    anyone = lines_of(ask(shopping))
    assert anyone["Cheap Ramp"].get("printing") is None  # (no rules at all: nothing chosen)
    cheapest = lines_of(ask(shopping, sets=["m20", "2xm"]))
    assert cheapest["Cheap Ramp"]["printing"]["finish"] == "foil" and cheapest["Test Burn"]["printing"]["set"] == "2XM"


def test_a_card_with_no_printing_that_fits_is_reported_and_left_out(shopping):
    r = ask(shopping, finish="nonfoil")
    ramp = lines_of(r)["Cheap Ramp"]  # only exists in foil in the catalog
    assert ramp["no_qualifying_printing"] is True and ramp.get("printing") is None and ramp["unit_price_usd"] is None
    assert "finish nonfoil" in ramp["reason"]
    assert r["no_qualifying_printing"] == 1 and r["unpriced_lines"] == 0
    assert "Cheap Ramp" not in r["text"] and "Test Burn" in r["text"]
    # no language rule: the cheapest nonfoil printing is the Japanese 0.20 one; Cheap Ramp is not in the total
    assert lines_of(r)["Test Burn"]["printing"]["set"] == "WAR" and r["total_usd"] == 0.2 + 1.0
    assert any("no printing that fits" in n for n in r["notes"])


def test_the_language_rule_uses_the_printings_own_language(shopping):
    r = ask(shopping, language="ja")
    assert lines_of(r)["Test Burn"]["printing"]["language"] == "ja" and lines_of(r)["Test Burn"]["unit_price_usd"] == 0.2
    assert lines_of(r)["Test Rock"]["no_qualifying_printing"] is True  # Scryfall prices no Japanese Test Rock
    assert ask(shopping, language="Japanese")["lines"] == r["lines"]  # a name works like the code
    english = lines_of(ask(shopping, language="en"))
    assert english["Test Burn"]["printing"]["set"] == "2XM"  # the cheaper Japanese printing does not qualify
    res = shopping.post(f"{V1}/shopping-list", json={"text": DECK, "language": "klingon"})
    assert res.status_code == 422 and "Unknown language" in res.text


def test_the_condition_rule_is_kept_and_shown_but_changes_no_price(shopping):
    plain = ask(shopping, sets=["mh2", "2xm"])
    graded = ask(shopping, sets=["mh2", "2xm"], condition="LP")
    assert [(l["name"], l["unit_price_usd"], l.get("printing")) for l in graded["lines"]] == [(l["name"], l["unit_price_usd"], l.get("printing")) for l in plain["lines"]]
    assert graded["rules"]["condition"] == "LP" and graded["total_usd"] == plain["total_usd"]
    note = next(n for n in graded["notes"] if "Condition LP" in n)
    assert "not per condition" in note
    assert shopping.post(f"{V1}/shopping-list", json={"text": DECK, "condition": "MINT"}).status_code == 422


def test_rules_need_the_printing_prices_to_be_loaded(_catalog):
    res = _catalog.post(f"{V1}/shopping-list", json={"text": DECK, "finish": "foil"})
    assert res.status_code == 503 and "price of each printing" in res.json()["detail"]
    assert _catalog.post(f"{V1}/shopping-list", json={"text": DECK}).status_code == 200  # without rules it still answers


# -- the store formats -----------------------------------------------------------------------------------------

def test_plain_and_card_kingdom_write_quantity_and_name_only(shopping):
    for fmt in ("plain", "cardkingdom"):
        r = ask(shopping, format=fmt, sets=["mh2", "2xm", "ltr", "m20"])
        assert sorted(r["text"].splitlines()) == ["1 Test Burn", "1 Test Rock", "2 Cheap Ramp"], fmt
        assert any("printings chosen are listed in `lines`" in n for n in r["notes"]), fmt  # no place for them in this paste
    ck = ask(shopping, format="cardkingdom")["store_format"]
    assert ck["store"] == "Card Kingdom Deck Builder" and ck["help_page"] == "https://blog.cardkingdom.com/deck-builder-craft-your-next-deck/"
    assert "'4 Name', '4x Name' or just 'Name'" in ck["limits"]


def test_tcgplayer_mass_entry_gets_the_set_code_and_collector_number(shopping):
    r = ask(shopping, format="tcgplayer", sets=["mh2", "2xm", "ltr", "m20"])
    assert sorted(r["text"].splitlines()) == ["1 Test Burn [2XM] 20", "1 Test Rock [LTR] 1", "2 Cheap Ramp [M20] 1"]
    assert all(re.fullmatch(r"\d+ .+ \[[A-Z0-9]+\] \S+", line) for line in r["text"].splitlines())
    assert r["store_format"]["help_page"].startswith("https://help.tcgplayer.com/") and "Mass Entry" in r["store_format"]["store"]
    assert any("Finish and condition are not part of a line" in n for n in r["notes"])
    assert any("foil" in n and "pick the finish in the store" in n for n in r["notes"])  # Cheap Ramp was chosen in foil
    assert ask(shopping, format="tcgplayer")["text"].splitlines()[0].count("[") == 0  # no rules, no printing: name only


def test_cardmarket_want_list_gets_the_expansion_name_in_parentheses(shopping):
    r = ask(shopping, format="cardmarket", sets=["mh2", "2xm", "ltr", "m20"])
    assert sorted(r["text"].splitlines()) == ["1 Test Burn (Double Masters)", "1 Test Rock (The Lord of the Rings: Tales of Middle-earth)",
                                              "2 Cheap Ramp (Core Set 2020)"]
    assert r["store_format"]["help_page"] == "https://help.cardmarket.com/en/how-to-add-a-mtg-decklist-to-wants"
    assert "'Name (Expansion)'" in r["store_format"]["limits"]
    assert sorted(ask(shopping, format="cardmarket")["text"].splitlines()) == ["1 Test Burn", "1 Test Rock", "2 Cheap Ramp"]


def test_csv_is_a_spreadsheet_with_the_printing_and_the_dated_price(shopping):
    r = ask(shopping, format="csv", sets=["mh2", "2xm"], finish="nonfoil")
    rows = list(csv.DictReader(io.StringIO(r["text"])))
    assert list(rows[0]) == ["quantity", "name", "set", "collector_number", "finish", "language", "unit_price_usd", "price_date"]
    burn = next(x for x in rows if x["name"] == "Test Burn")
    assert burn == {"quantity": "1", "name": "Test Burn", "set": "2XM", "collector_number": "20", "finish": "nonfoil", "language": "en",
                    "unit_price_usd": "0.3", "price_date": PRINTING_DAY.isoformat()}
    assert {x["name"] for x in rows} == {"Test Burn"}  # the others have no nonfoil printing in those sets and are left out


def test_a_cell_that_a_spreadsheet_could_run_as_a_formula_is_defused():
    lines = [{"name": "=HYPERLINK(\"http://x\")", "quantity": 1, "unit_price_usd": None, "price_date": None, "printing": None},
             {"name": "+2 Mace", "quantity": 1, "unit_price_usd": 0.1, "price_date": "2026-10-05", "printing": None},
             {"name": "Atraxa, Praetors' Voice", "quantity": 1, "unit_price_usd": None, "price_date": None, "printing": None}]
    rows = list(csv.reader(io.StringIO(shop.render(lines, "csv"))))[1:]
    assert [r[1] for r in rows] == ["'=HYPERLINK(\"http://x\")", "'+2 Mace", "Atraxa, Praetors' Voice"]  # the comma stays inside its cell


def test_all_returns_every_format_at_once(shopping):
    r = ask(shopping, format="all", sets=["mh2", "2xm", "ltr", "m20"])
    assert set(r["texts"]) == set(shop.FORMATS) and r["text"] == r["texts"]["plain"]
    assert r["texts"]["tcgplayer"] != r["texts"]["cardmarket"] and "[2XM]" in r["texts"]["tcgplayer"]
    assert set(r["store_format"]) == set(shop.FORMATS)


def test_the_store_pages_are_named_in_the_data_sources_doc_with_the_day_they_were_read():
    doc = (Path(__file__).parent.parent / "docs" / "data-sources.md").read_text(encoding="utf-8")
    for key in ("cardkingdom", "tcgplayer", "cardmarket"):
        info = shop.STORES[key]
        assert info["page"] in doc, key
        assert info["checked"] in doc, key


def test_the_answer_never_names_a_cheapest_store_or_claims_a_cart(shopping):
    r = ask(shopping, format="all", sets=["mh2"])
    notes = " ".join(r["notes"] + [v["limits"] for v in r["store_format"].values()]).lower()
    assert "never says which store is cheapest" in notes and "does not contact stores" in notes
    assert "added to your cart" not in notes and "in your cart" not in notes


def test_a_card_the_catalog_does_not_know_is_listed_without_a_printing_when_rules_are_given(shopping):
    r = shopping.post(f"{V1}/shopping-list", json={"text": "1 Nonexistent Wonder\n1 Test Rock", "sets": ["ltr"]}).json()["result"]
    unknown = lines_of(r)["Nonexistent Wonder"]
    assert unknown["known_card"] is False and unknown["no_qualifying_printing"] is True
    assert r["text"] == "1 Test Rock"


# -- choosing: unit-level --------------------------------------------------------------------------------------

class P:
    def __init__(self, sid, set_code, number, lang="en", usd=None, usd_foil=None, usd_etched=None):
        self.scryfall_id, self.set_code, self.collector_number, self.lang = sid, set_code, number, lang
        self.usd, self.usd_foil, self.usd_etched = usd, usd_foil, usd_etched


def test_ties_go_to_nonfoil_then_to_set_and_number():
    rules = shop.make_rules(None, None, None, None)
    a, b = P("a", "m21", "5", usd=1.0, usd_foil=1.0), P("b", "lea", "9", usd=1.0)
    assert shop.choose([a, b], rules)[0].scryfall_id == "b"  # same price, same finish rank: the earlier set code
    assert shop.choose([a], rules)[1] == "nonfoil"  # same price in foil: nonfoil wins


def test_a_set_code_matches_in_any_case_and_a_missing_card_has_no_choice():
    p = P("a", "MH2", "1", usd=2.0)
    assert shop.choose([p], shop.make_rules(None, None, ["mh2"], None))[2] == 2.0
    assert shop.choose([p], shop.make_rules(None, None, ["znr"], None)) is None
    assert shop.choose([], shop.make_rules(None, None, None, None)) is None


# -- loading the printing prices -------------------------------------------------------------------------------

def bulk(id_, oracle, **extra):
    base = {"object": "card", "id": id_, "oracle_id": oracle, "set": "tst", "set_name": "Test Set", "collector_number": "1", "lang": "en",
            "layout": "normal", "set_type": "expansion", "digital": False, "prices": {"usd": "1.00", "usd_foil": None, "usd_etched": None}}
    base.update(extra)
    return base


def test_only_priced_paper_printings_that_belong_in_a_deck_are_loaded():
    objects = [bulk("ok", oid(1)), bulk("digital", oid(1), digital=True), bulk("oversize", oid(1), oversized=True),
               bulk("token", oid(1), layout="token"), bulk("emblem", oid(1), layout="emblem"), bulk("art", oid(1), layout="art_series"),
               bulk("memo", oid(1), set_type="memorabilia"), bulk("unpriced", oid(1), prices={"usd": None, "usd_foil": None}),
               bulk("foilonly", oid(2), prices={"usd": None, "usd_foil": "0.25", "usd_etched": None}),
               bulk("japanese", oid(3), lang="ja"), {"object": "list"}, bulk("no-oracle", None)]
    rows = cs.printing_prices(objects)
    assert sorted(r["scryfall_id"] for r in rows) == ["foilonly", "japanese", "ok"]
    assert {r["scryfall_id"]: (r["usd"], r["usd_foil"], r["lang"]) for r in rows}["foilonly"] == (None, 0.25, "en")


def test_the_load_writes_only_what_changed_and_deletes_what_lost_its_price(app):
    first = cs.printing_prices([bulk("a", oid(1)), bulk("b", oid(2)), bulk("c", oid(3))])
    with app.state.db.sessions() as db:
        assert cs.sync_oracle_printings(db, first) == {"printings": 3, "written": 3, "removed": 0}
        db.commit()
        assert cs.sync_oracle_printings(db, first) == {"printings": 3, "written": 0, "removed": 0}  # nothing changed: nothing written
        second = cs.printing_prices([bulk("a", oid(1), prices={"usd": "2.00", "usd_foil": None, "usd_etched": None}), bulk("b", oid(2))])
        assert cs.sync_oracle_printings(db, second) == {"printings": 2, "written": 1, "removed": 1}
        db.commit()
        assert {p.scryfall_id: p.usd for p in db.query(OraclePrinting)} == {"a": 2.0, "b": 1.0}
        assert cs.sync_oracle_printings(db, []) == {"printings": 0, "written": 0, "removed": 0}  # an empty file never empties the table
        assert db.query(OraclePrinting).count() == 2


def test_the_price_job_loads_printings_only_when_that_source_is_enabled(app, monkeypatch, tmp_path):
    import gzip
    import json

    from jobs import sync_prices

    path = tmp_path / "default-cards.jsonl.gz"
    path.write_bytes(gzip.compress("\n".join(json.dumps(c) for c in [bulk("a", oid(1)), bulk("b", oid(2))]).encode()))
    monkeypatch.setenv("DATABASE_URL", app.state.settings.database_url)
    monkeypatch.setenv("CATALOG_SOURCES", "oracle_cards")
    sync_prices.main(["--file", str(path)])
    with app.state.db.sessions() as db:
        assert db.query(OraclePrinting).count() == 0
    monkeypatch.setenv("CATALOG_SOURCES", "oracle_cards,oracle_printings")
    sync_prices.main(["--file", str(path)])
    with app.state.db.sessions() as db:
        assert db.query(OraclePrinting).count() == 2
        from vault.models import CatalogSource
        assert db.get(CatalogSource, "oracle_printings").rows == 2


# -- through MCP -----------------------------------------------------------------------------------------------

def test_the_tool_takes_the_options_through_mcp_and_says_what_it_does_not_do(app, shopping):
    signed_in = shopping
    from fastapi.testclient import TestClient

    from tests.test_agents import call_tool, make_token, rpc
    from vault.api import mcp

    tool = mcp.BY_NAME["shopping_list"]
    props = tool.properties
    assert {"format", "finish", "language", "sets", "condition"} <= set(props)
    assert set(props["format"]["enum"]) == set(shop.FORMATS) | {"all"}
    assert "which store is cheapest is not known" in tool.description and "never contacts stores" in tool.description
    assert "Never say which shop is cheapest" in mcp.INSTRUCTIONS  # #241: the order lives in the instructions
    assert "not per condition" in tool.description
    with TestClient(app) as bot:
        token = make_token(signed_in)
        listed = {t["name"]: t for t in rpc(bot, "tools/list", token=token).json()["result"]["tools"]}
        assert {"format", "finish", "language", "sets", "condition"} <= set(listed["shopping_list"]["inputSchema"]["properties"])
        assert listed["shopping_list"]["annotations"]["readOnlyHint"] is True
        result = call_tool(bot, token, "shopping_list", text=DECK, format="tcgplayer", sets=["mh2", "2xm", "ltr", "m20"])
        assert result["isError"] is False, result["content"][0]["text"]
        body = result["structuredContent"]
        assert sorted(body["result"]["text"].splitlines()) == ["1 Test Burn [2XM] 20", "1 Test Rock [LTR] 1", "2 Cheap Ramp [M20] 1"]
        assert {b["kind"] for b in body["provenance"]} == {"computed"} and body["provenance"][0]["inputs"]
        names = {i["origin"] for i in body["provenance"][0]["inputs"]}
        assert any("each printing" in n for n in names)  # the source of the per-printing prices is named, with its date
        refused = rpc(bot, "tools/call", {"name": "shopping_list", "arguments": {"text": DECK, "finish": "sparkly"}}, token).json()
        assert "error" in refused or refused["result"]["isError"] is True  # not one of nonfoil, foil, etched: refused, not ignored
