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


@pytest.mark.parametrize("rows", ["2", "4"], ids=["refused: more rows than the limit, with the sep line", "refused: more rows than the limit, no sep line"])
def test_every_path_refuses_a_file_over_the_row_limit_in_one_sentence_and_stages_nothing(app, agent, bot, monkeypatch, rows):  # noqa: F811
    from tests.test_agents import CSV  # five card rows, a header and the sep line
    monkeypatch.setenv("IMPORT_MAX_ROWS", rows)
    if rows == "4":
        CSV = CSV.split(b"\n", 1)[1]  # without the sep line: a header and five rows, one past the row limit of four
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


# ---- the row guard counts with the library's own reader, lazily, before any entry is built ----------------------------------------------------

@pytest.fixture
def entries_built(monkeypatch):
    """Counts the entries the library builds, so a test can show that a refused file built none."""
    from mtg_toolkits import dragonshield, formats, moxfield
    built = []
    for module in (dragonshield, formats, moxfield):
        real = module.CollectionEntry
        monkeypatch.setattr(module, "CollectionEntry", lambda *a, _real=real, **k: (built.append(1), _real(*a, **k))[1])
    return built


def test_the_guard_uses_the_delimiter_and_the_rows_the_library_chose_and_builds_no_entry(monkeypatch, entries_built):
    """Review of PR 483 (threads on the sep= preflight): the first line was probed by a copy of the library's rules, 200 characters of it
    and with a comma reading beside it. The guard now opens the reader the library would open (its own sep= rules, its own format) and
    counts that reader's rows."""
    import time

    from vault import importer
    monkeypatch.setenv("IMPORT_MAX_ROWS", "5")
    owners = '"sep=,"\r\nFolder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language\r\n'
    rows = "".join(f"my cards,1,0,Sol Ring {n},C21,Commander 2021,{n},Mint,Normal,English\r\n" for n in range(5))
    assert len(importer.read_file((owners + rows).encode())[1]) == 5  # the owner's own header shape, a real-looking body: taken
    with pytest.raises(importer.ImportError_, match="too many rows"):
        importer.read_file((owners + rows + "my cards,1,0,Sol Ring 9,C21,Commander 2021,9,Mint,Normal,English\r\n").encode())
    assert len(entries_built) == 5  # only the file that was taken built entries

    monkeypatch.delenv("IMPORT_MAX_ROWS")
    # Copilot's first file: 201 spaces before the declaration, then a quote after a semicolon that is only text in a semicolon file
    padded = " " * 201 + "sep=;\nCard Name;Quantity;Note\n" + 'Sol Ring;1;,"\n' + "Sol Ring;1;\n" * 291_740
    before = len(entries_built)
    started = time.perf_counter()
    with pytest.raises(importer.ImportError_, match="too many rows"):
        importer.read_file(padded.encode())
    assert time.perf_counter() - started < 1.5 and len(entries_built) == before  # refused without building a single entry

    # Copilot's second file: valid, semicolons, 50,000 records of three lines each in a quoted cell (950,030 bytes), is taken
    valid = "sep=;\nCard Name;Quantity;Note\n" + 'Sol Ring;1;"p\nq\nr"\n' * 50_000
    assert 940_000 < len(valid.encode()) < 960_000
    assert len(importer.read_file(valid.encode())[1]) == 50_000


@pytest.mark.parametrize("first", ['sep=,"', '"sep=,', ' "sep=,"', "sep=;ignored,quantity,name,set_code,finish,condition", "sep=", "sep=;x", "SEP=;",
                                   "Card Name,Quantity", "quantity,name,set_code,finish,condition", "Count,Name"], ids=repr)
def test_no_form_of_first_line_lets_a_file_of_more_rows_than_the_limit_through(first, entries_built):
    """The reviewer's malformed sep= forms and the plain headers: a stray quote after the first row and 291,740 rows after it are refused,
    whichever reader the library picks for the file (or the file is refused as unreadable), and none of them builds an entry."""
    from vault import importer
    text = first + "\n" + 'x;",1,y,,nonfoil,NM\n' + "Sol Ring;1;\nSol Ring,1,y,,nonfoil,NM\n" * 145_870
    with pytest.raises(importer.ImportError_):
        importer.read_file(text.encode())
    assert entries_built == []


def test_the_guard_reaches_the_reader_of_every_format_the_importer_reads(monkeypatch):
    """If a library update stops opening its readers through the module-level csv name, the guard must fail loudly, not skip itself."""
    from vault import importer
    monkeypatch.setenv("IMPORT_MAX_ROWS", "2")
    samples = {
        "dragonshield": "Folder Name,Quantity,Card Name\nf,1,A\nf,1,B\nf,1,C\n",
        "moxfield": "Count,Name,Edition\n1,A,c21\n1,B,c21\n1,C,c21\n",
        "csv": "quantity,name,set_code,finish,condition\n1,A,c21,nonfoil,NM\n1,B,c21,nonfoil,NM\n1,C,c21,nonfoil,NM\n",
    }
    for expected, text in samples.items():
        assert importer.formats.detect(text) == expected
        with pytest.raises(importer.ImportError_, match="too many rows"):
            importer.read_file(text.encode())


def test_the_guard_costs_little_on_the_biggest_file_the_limits_allow_and_on_a_file_that_quotes_every_cell():
    import time

    from tests.memory_probe import synthetic_csv
    from vault import importer
    worst = synthetic_csv(config.DEFAULT_IMPORT_MAX_BYTES, "worst").decode()
    started = time.perf_counter()
    importer.check_rows("dragonshield", worst, 100_000)  # 100,000 rows: taken
    quoted = 'Card Name,Quantity,Set Code\n' + '"Sol Ring","1","C21"\n' * 100_000
    importer.check_rows("dragonshield", quoted, 100_000)
    with pytest.raises(importer.ImportError_):
        importer.check_rows("dragonshield", "Card Name,Quantity\n" + "x,1\n" * 4_000_000, 100_000)  # stops at the limit, not at the end
    assert time.perf_counter() - started < 6.0
