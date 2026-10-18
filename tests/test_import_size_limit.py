"""One import-size limit for every path that takes a collection file (vault-0n9.3): ``IMPORT_MAX_BYTES``, the assembled bytes.

The Import CSV button and the API (``/imports``, ``/imports/preview``), the upload page, ``import_collection_csv``, a file the chat app
hands over by address, and a file sent in parts all refuse a file one byte over the limit with one plain sentence and stage nothing,
and take one exactly at it. The limit is read from the environment each time, so each test sets its own; there is no other number.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.memory_probe import synthetic_csv
from tests.test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from tests.test_file_intake import file_arg, intake_with
from vault import config, file_parts
from vault.models import Import, StagedUpload, StagedUploadPart

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 4000


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setenv("IMPORT_MAX_BYTES", str(LIMIT))
    return LIMIT


def at_limit() -> bytes:
    return synthetic_csv(LIMIT, "short")


def over_limit() -> bytes:
    return at_limit() + b" "  # one byte more; a trailing blank changes nothing else


def nothing_kept(app):
    with app.state.db.sessions() as db:
        return (db.query(StagedUpload).count(), db.query(StagedUploadPart).count(), db.query(Import).count()) == (0, 0, 0)


def test_the_default_is_eight_mebibytes_and_the_setting_is_read_each_time(monkeypatch):
    monkeypatch.delenv("IMPORT_MAX_BYTES", raising=False)
    assert config.import_max_bytes() == config.DEFAULT_IMPORT_MAX_BYTES == 8 * 1024 * 1024 == 8_388_608
    monkeypatch.setenv("IMPORT_MAX_BYTES", "12345")
    assert config.import_max_bytes() == 12345
    assert [config.size_text(n) for n in (8 * 1024 * 1024, 12_582_912, 1024 * 1024, 1_500_000, 4000)] == [
        "8 MB", "12 MB", "1 MB", "1.4 MB", "4,000 bytes"]
    monkeypatch.setenv("IMPORT_MAX_BYTES", "0")
    with pytest.raises(RuntimeError, match="IMPORT_MAX_BYTES"):
        config.Settings(database_url="postgresql://x", session_secret="s", dev_login=True, base_url="http://localhost").check()


@pytest.mark.parametrize("route", ["/imports", "/imports/preview"])
def test_the_import_routes_take_a_file_at_the_limit_and_refuse_one_byte_more(app, agent, limited, route):  # noqa: F811
    ok = agent.post(f"{V1}{route}", files={"file": ("my_cards.csv", at_limit(), "text/csv")})
    assert ok.status_code in (200, 201), ok.text
    refused = agent.post(f"{V1}{route}", files={"file": ("my_cards.csv", over_limit(), "text/csv")})
    assert refused.status_code == 400 and "4,000 bytes" in refused.json()["detail"] and "too large" in refused.json()["detail"]


def test_the_upload_page_takes_a_file_at_the_limit_and_refuses_one_byte_more_in_a_sentence(app, agent, limited):  # noqa: F811
    with TestClient(app) as page:
        started = agent.post(f"{V1}/uploads").json()
        ticket = started["url"].split("ticket=")[1]
        refused = page.post("/upload", data={"ticket": ticket}, files={"file": ("my_cards.csv", over_limit(), "text/csv")})
        assert refused.status_code == 413 and "4,000 bytes at most" in refused.text
        with app.state.db.sessions() as db:
            assert db.get(StagedUpload, started["id"]).content is None
        took = page.post("/upload", data={"ticket": ticket}, files={"file": ("my_cards.csv", at_limit(), "text/csv")})
        assert took.status_code == 200, took.text


def test_import_collection_csv_takes_a_file_at_the_limit_and_refuses_one_byte_more(app, agent, bot, limited):  # noqa: F811
    token = make_token(agent, scopes=["read", "write"])
    ok = call_tool(bot, token, "import_collection_csv", csv=at_limit().decode("utf-8"))
    assert not ok["isError"], ok
    refused = call_tool(bot, token, "import_collection_csv", csv=over_limit().decode("utf-8"))
    assert refused["isError"] is True and "4,000 bytes" in refused["content"][0]["text"]


def test_a_file_fetched_from_the_chat_app_takes_a_file_at_the_limit_and_refuses_one_byte_more(app, agent, bot, limited):  # noqa: F811
    token = make_token(agent, scopes=["read"])
    intake_with(app, lambda r: httpx.Response(200, content=at_limit(), headers={"content-type": "text/csv"}))
    assert call_tool(bot, token, "import_collection_file", file=file_arg())["structuredContent"]["status"] == "uploaded"
    intake_with(app, lambda r: httpx.Response(200, content=over_limit(), headers={"content-type": "text/csv"}))
    answer = call_tool(bot, token, "import_collection_file", file=file_arg(f"https://files.oaiusercontent.com/other?sig=2"))["structuredContent"]
    assert answer["status"] == "too_large" and "4,000 bytes" in answer["say"] and answer["say"].count(".") == 1


def test_a_file_in_parts_takes_a_file_at_the_limit_and_refuses_one_byte_more(app, agent, bot, limited):  # noqa: F811
    token = make_token(agent, scopes=["read"])

    def parts(content: bytes) -> list[dict]:
        text = content.decode("utf-8")
        pieces = [text[i:i + 1000] for i in range(0, len(text), 1000)]
        facts = {"parts": len(pieces), "lines": file_parts.line_count(text), "chars": len(text), "sum": file_parts.checksum(text)}
        return [call_tool(bot, token, "import_collection_file", text=p, part=i, **facts)["structuredContent"] for i, p in enumerate(pieces, 1)]

    assert parts(at_limit())[-1]["status"] == "uploaded"
    refused = parts(over_limit())[-1]
    assert refused["status"] == "too_large" and "4,000 bytes" in refused["say"] and refused["say"].count(".") == 1
    with app.state.db.sessions() as db:
        assert db.query(StagedUploadPart).count() == 0 and db.query(StagedUpload).count() == 1  # only the one at the limit


def test_the_tool_texts_and_the_pages_state_the_limit_from_the_setting_and_no_module_has_a_number_of_its_own():
    sources = {name: (ROOT / "vault" / name).read_text(encoding="utf-8") for name in (
        "importer.py", "uploads.py", "intake.py", "file_fetch.py", "file_parts.py", "api/v1.py", "api/mcp.py", "api/mcp_profile.py")}
    for name, source in sources.items():
        assert not re.search(r"MAX_UPLOAD_BYTES|INTAKE_MAX_BYTES", source), name
        assert not re.search(r"\b\d+\s*\*\s*1024\s*\*\s*1024\b", source), f"{name}: a size written as a number"
        assert not re.search(r"\b(?:5|8|12|20) ?(?:MB|MiB)\b", re.sub(r"\(IMPORT_MAX_BYTES[^)]*\)|8 MiB", "", source)), f"{name}: a limit in words"
    from vault.api import mcp
    assert f"up to {config.size_text(config.import_max_bytes())}" in mcp.BY_NAME["start_collection_upload"].description


# ---- the row limit, the second product limit (IMPORT_MAX_ROWS) ----------------------------------------------------------------------

def test_the_row_limit_is_a_setting_with_a_default_and_a_file_of_tiny_rows_is_refused_before_it_is_parsed(monkeypatch):
    """Review of PR 483 (M1): the size limit alone does not bound memory; a file of 29-byte rows at 8 MiB has 291,740 rows and peaked at
    577 MB. The row limit is checked on the lines before anything is parsed."""
    import time

    from vault import importer
    monkeypatch.delenv("IMPORT_MAX_ROWS", raising=False)
    assert config.import_max_rows() == config.DEFAULT_IMPORT_MAX_ROWS == 100_000
    tiny = b"Card Name,Quantity\n" + b"x,1\n" * 291_740
    started = time.perf_counter()
    with pytest.raises(importer.ImportError_, match="too many rows"):
        importer.read_file(tiny)
    assert time.perf_counter() - started < 1.0 and len(tiny) < config.import_max_bytes()  # within the size limit, over the row limit
    monkeypatch.setenv("IMPORT_MAX_ROWS", "0")
    with pytest.raises(RuntimeError, match="IMPORT_MAX_ROWS"):
        config.Settings(database_url="postgresql://x", session_secret="s", dev_login=True, base_url="http://localhost").check()


@pytest.mark.parametrize("rows", ["2", "4"], ids=["refused on the records before parsing", "refused on the parsed rows"])
def test_every_path_refuses_a_file_over_the_row_limit_in_one_sentence_and_stages_nothing(app, agent, bot, monkeypatch, rows):  # noqa: F811
    from tests.test_agents import CSV  # five card rows, a header and the sep line
    monkeypatch.setenv("IMPORT_MAX_ROWS", rows)
    if rows == "4":
        CSV = CSV.split(b"\n", 1)[1]  # without the sep line: six records, five rows: past the row limit but inside the record preflight
    write = make_token(agent, scopes=["read", "write"])
    for route in ("/imports", "/imports/preview"):
        refused = agent.post(f"{V1}{route}", files={"file": ("my_cards.csv", CSV, "text/csv")})
        assert refused.status_code == 400 and "too many rows" in refused.json()["detail"], route
    out = call_tool(bot, write, "import_collection_csv", csv=CSV.decode("utf-8"))
    assert out["isError"] is True and "too many rows" in out["content"][0]["text"]
    intake_with(app, lambda r: httpx.Response(200, content=CSV, headers={"content-type": "text/csv"}))
    fetched = call_tool(bot, write, "import_collection_file", file=file_arg())["structuredContent"]
    assert fetched["status"] == "unreadable" and "too many rows" in fetched["say"] and fetched["say"].count(".") == 1
    text = CSV.decode("utf-8")
    pieces = [text[i:i + 40] for i in range(0, len(text), 40)]
    facts = {"parts": len(pieces), "lines": file_parts.line_count(text), "chars": len(text), "sum": file_parts.checksum(text)}
    parts = [call_tool(bot, write, "import_collection_file", text=p, part=i, **facts)["structuredContent"] for i, p in enumerate(pieces, 1)]
    assert parts[-1]["status"] == "unreadable" and "too many rows" in parts[-1]["say"]
    with app.state.db.sessions() as db:
        assert db.query(StagedUpload).count() == 0 and db.query(StagedUploadPart).count() == 0
    with TestClient(app) as page:  # the upload page: the file is kept as received and the person is told it could not be read
        started = agent.post(f"{V1}/uploads").json()
        res = page.post("/upload", data={"ticket": started["url"].split("ticket=")[1]}, files={"file": ("my_cards.csv", CSV, "text/csv")})
        assert "too many rows" in res.text


def test_a_file_at_the_row_limit_is_taken(app, agent, monkeypatch):  # noqa: F811
    from tests.test_agents import CSV
    monkeypatch.setenv("IMPORT_MAX_ROWS", "5")
    assert agent.post(f"{V1}/imports/preview", files={"file": ("my_cards.csv", CSV, "text/csv")}).status_code == 200


# ---- the record preflight: a bounded, quote-aware count of records before any entry is built --------------------------------------------

@pytest.mark.parametrize("text, records", [
    ("", 0), ("a\n", 1), ("a", 1), ("a\nb\nc", 3), ("a\r\nb\r\nc\r\n", 3), ("a\rb\rc\r", 3), ("a\rb\r\nc\nd", 4),
    ("a\n\n\nb\n", 2), ("\r\n\r\na\r\n", 1), ("\n", 0),
    ('a,"x\ny\nz"\nb,"p\r\nq"\n', 2), ('"a""\n""b"\n"c"\n', 2), ('a,"x\ry"\rb\r', 2),
    ('"unterminated\nstill the same record', 1), ('x\n"unterminated\nmore', 2), ("'@x,1\n'@y,2\n", 2),
    # a quote inside an unquoted cell is an ordinary character (the csv module's rule), a quote at the start of a field opens one
    ('5" Wall,1\nb,2\n', 2), ('x",1\ny,2\nz,3\n', 3), ('a,5" Wall\nb,2\n', 2), ('a,"x\ny"\nb\n', 2), ('a, "x\ny"\nb\n', 3),
    ('sep=;\na;"x\ny"\nb\n', 3), ('sep=;\na,"x\ny"\nb\n', 4), ('"sep=\t"\na\t"x\ny"\nb\n', 3), ('a\n   ', 1), ('a\n \t \n', 1),
], ids=lambda v: None)
def test_records_are_counted_by_the_quote_state_for_lf_crlf_and_bare_cr_and_blank_lines_are_none(text, records):
    from vault import importer
    assert importer.record_count(text, 10**9) == records


def test_the_count_stops_as_soon_as_it_is_over_the_limit():
    from vault import importer
    assert importer.record_count("a\n" * 1_000_000, 10) == 11


def test_the_preflight_sees_what_a_line_count_missed(monkeypatch):
    """Review of PR 483 (thread 1): bare-CR files had no line feed at all, and quoted multiline cells made five records look like many
    lines. The generic-CSV marker case turns separators into line feeds in restore_csv, so the guard runs before it."""
    from vault import importer
    monkeypatch.setenv("IMPORT_MAX_ROWS", "5")
    header = "Card Name,Quantity\r"
    many_cr = header + "".join(f"Sol Ring {n},1\r" for n in range(50))  # no line feed anywhere: 51 records
    with pytest.raises(importer.ImportError_, match="too many rows"):
        importer.read_file(many_cr.encode())
    five_multiline = header + "".join(f'"Sol\nRing\n{n}",1\r' for n in range(5))  # six records, ten line feeds
    try:
        importer.read_file(five_multiline.encode())
    except importer.ImportError_ as caught:
        assert "too many rows" not in str(caught)  # not refused on the count: whatever else the parser says
    blank = "Card Name,Quantity\n" + "\n" * 500 + "Sol Ring,1\n" + "\r\n" * 500  # blank lines are none
    assert importer.record_count(blank, 10**9) == 2
    try:
        importer.read_file(blank.encode())
    except importer.ImportError_ as caught:
        assert "too many rows" not in str(caught)

    def never(text):
        raise AssertionError("restore_csv ran before the row guard")

    monkeypatch.setattr(importer, "restore_csv", never)  # the marker case: a marked file is rewritten row by row by restore_csv
    marked = "Card Name,Quantity\n" + "".join(f"'@Invented {n},1\n" for n in range(50))
    with pytest.raises(importer.ImportError_, match="too many rows"):
        importer.read_file(marked.encode())


def test_the_preflight_costs_little_on_the_biggest_file_the_limits_allow_and_on_a_file_that_quotes_every_cell():
    import time

    from tests.memory_probe import synthetic_csv
    from vault import importer
    worst = synthetic_csv(config.DEFAULT_IMPORT_MAX_BYTES, "worst").decode()
    started = time.perf_counter()
    assert importer.record_count(worst, 10**9) == 100_002  # 100,000 rows, the sep line and the header
    quoted = '"Card Name","Quantity","Set"\n' + '"Sol Ring","1","C21"\n' * 100_000
    assert importer.record_count(quoted, 10**9) == 100_001
    assert time.perf_counter() - started < 6.0


def test_the_preflight_agrees_with_the_csv_module_on_what_a_record_is():
    """Review of PR 483 (thread, L1): a quote in the middle of a cell must not open a quoted field. The count is compared with
    csv.reader's rows (a blank line is no record) on a few thousand short strings made of the characters that matter."""
    import csv
    import io
    import random

    from vault import importer
    rng = random.Random(483)
    for _ in range(4000):
        text = "".join(rng.choice('a,"\n\r"') for _ in range(rng.randint(0, 14)))
        rows = [r for r in csv.reader(io.StringIO(text, newline=""))]
        assert importer.record_count(text, 10**9) == sum(1 for r in rows if r), repr(text)


def test_a_stray_quote_cannot_hide_the_rows_after_it_from_the_row_guard():
    """The shape from the review: one stray quote in a cell swallowed the rest of the file as 'one record' (2 records counted where
    csv.reader sees 291,742), so the guard before the parse let 291,740 rows through."""
    import csv
    import io
    import time

    from vault import importer
    text = 'Card Name,Quantity\nx",1\n' + "y,1\n" * 291_740
    assert sum(1 for r in csv.reader(io.StringIO(text, newline=""))) == 291_742
    started = time.perf_counter()
    with pytest.raises(importer.ImportError_, match="too many rows"):
        importer.read_file(text.encode())
    assert time.perf_counter() - started < 1.0
    assert importer.record_count(text, 10**9) == 291_742
    five_inches = 'Card Name,Quantity\n' + "".join(f'5" Wall {n},1\n' for n in range(5))  # five rows with a quote inside the cell
    assert importer.record_count(five_inches, 10**9) == 6


def test_a_last_line_of_blanks_is_no_record_and_a_delimiter_declared_by_the_sep_line_is_the_one_a_quote_follows():
    from vault import importer
    assert importer.record_count("a\nb\n          ", 10**9) == 2 and importer.record_count("a\nb\n\t", 10**9) == 2
    assert importer.record_count('sep=;\nx;"a\nb"\n', 10**9) == 2  # after a semicolon a quote opens a field: the sep line and one record
    assert importer.record_count('sep=;\nx,"a\nb"\n', 10**9) == 3  # after a comma it does not: the file's delimiter is the semicolon
