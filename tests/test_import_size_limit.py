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
        facts = {"parts": len(pieces), "lines": file_parts.line_count(text), "sum": file_parts.checksum(text)}
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
