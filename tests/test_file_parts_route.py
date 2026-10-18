"""A collection file sent as text in ordered parts to ``import_collection_file`` (vault-0n9.3): the route, the table, the limits.

The assistant that sends the parts is a coding app that was given the file's local path and no file object. Nothing is fetched;
every part is stored as it arrives, the file is checked when all parts are there, and only then previewed. Imports nothing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sql

from tests.test_agents import CSV, V1, agent, auth, bot, call_tool, make_token  # noqa: F401 - fixtures
from tests.test_mcp_profile import hello, send
from vault import file_parts, retention
from vault.api import mcp
from vault.models import Import, StagedUpload, StagedUploadPart

BOM = chr(0xFEFF)
EXTRA = "my cards,2,0,Jötun Grunt \u2014 Æther,XYZ,Invented Set,1,Mint,Normal,English,0.10,2024-01-01,0.05,0.10,0.08\n"
BASE = CSV.decode("utf-8") + EXTRA
# Without the `"sep=,"` first line: with CRLF line ends the library does not recognise that line (every route says unreadable, the upload
# page too), a finding of this build, not something the parts route decides.
NO_SEP = BASE.split("\n", 1)[1]


def crlf_bom(text: str) -> str:
    return BOM + text.replace("\n", "\r\n")


def cut(text: str, parts: int) -> list[str]:
    """``parts`` pieces of about the same length (the text has no character outside the Basic Multilingual Plane)."""
    size = -(-len(text) // parts)
    pieces = [text[i:i + size] for i in range(0, len(text), size)]
    assert len(pieces) == parts, (len(pieces), parts)
    return pieces


def facts(text: str) -> dict:
    return {"lines": file_parts.line_count(text), "sum": file_parts.checksum(text)}


def token_of(agent, *scopes):  # noqa: F811
    return make_token(agent, scopes=list(scopes or ("read",)))


def send_part(bot, token, pieces, i, text=None, **overrides):  # noqa: F811
    """Part ``i`` (from 1) of ``pieces`` as the tool takes it; returns the whole tool result."""
    whole = "".join(pieces)
    args = {"text": pieces[i - 1] if text is None else text, "part": i, "parts": len(pieces), **facts(whole), **overrides}
    return call_tool(bot, token, "import_collection_file", **args)


def sent(bot, token, pieces, order=None):  # noqa: F811
    return [send_part(bot, token, pieces, i)["structuredContent"] for i in (order or range(1, len(pieces) + 1))]


def tables(app):
    with app.state.db.sessions() as db:
        return db.query(StagedUpload).count(), db.query(StagedUploadPart).count()


def imports_of(app):
    with app.state.db.sessions() as db:
        return db.query(Import).count()


# ---- the happy path ---------------------------------------------------------------------------------------------------------------

def test_parts_in_any_order_make_one_staged_file_that_is_previewed_and_imports_nothing(app, agent, bot):  # noqa: F811
    text = crlf_bom(NO_SEP)  # CRLF, a byte-order mark, accents and a dash
    pieces = cut(text, 5)
    before = imports_of(app)
    token = token_of(agent, "read", "write")
    answers = sent(bot, token, pieces, order=[3, 1, 5, 2, 4])
    assert [a["status"] for a in answers] == ["part_received"] * 4 + ["uploaded"]
    final = answers[-1]
    assert final["changed"] is False and final["rows"] > 0 and final["copies"] > 0 and "changes" in final and "merge" in final
    assert final["filename"] == file_parts.FILE_NAME and len(final["content_hash"]) == 64 and "confirm_staged_upload" in final["next"]
    assert imports_of(app) == before  # nothing was imported
    assert tables(app) == (1, 0)  # one staged file; the parts were deleted when it was assembled
    with app.state.db.sessions() as db:
        assert db.get(StagedUpload, final["id"]).content == text.encode("utf-8")  # every byte, BOM and CRLF included
    # read the other way, and the person's yes
    again = call_tool(bot, token, "get_staged_upload", upload_id=final["id"])["structuredContent"]
    assert again["status"] == "uploaded" and again["content_hash"] == final["content_hash"] and again["changes"] == final["changes"]
    done = call_tool(bot, token, "confirm_staged_upload", upload_id=final["id"], confirm=True, content_hash=final["content_hash"])
    assert not done["isError"], done
    assert imports_of(app) == before + 1 and tables(app) == (0, 0)


def test_the_preview_of_a_file_in_parts_is_the_preview_of_the_same_file_fetched_whole(app, agent, bot):  # noqa: F811
    token = token_of(agent, "read", "write")
    final = sent(bot, token, cut(BASE, 3))[-1]
    from tests.test_file_intake import file_arg, intake_with
    import httpx
    intake_with(app, lambda r: httpx.Response(200, content=BASE.encode("utf-8"), headers={"content-type": "text/csv"}))
    whole = call_tool(bot, token, "import_collection_file", file=file_arg())["structuredContent"]
    for key in ("rows", "copies", "changes", "merge", "source", "matching_line", "unmatched_rows"):
        assert final[key] == whole[key], key
    assert final["content_hash"] == whole["content_hash"]  # the same bytes, the same identity


def test_a_read_only_connection_can_send_parts_and_see_the_preview_but_cannot_import_them(app, agent, bot):  # noqa: F811
    read = token_of(agent)
    before = imports_of(app)
    final = sent(bot, read, cut(BASE, 2))[-1]
    assert final["status"] == "uploaded"
    refused = call_tool(bot, read, "confirm_staged_upload", upload_id=final["id"], confirm=True, content_hash=final["content_hash"])
    assert refused["isError"] is True and "read-only" in refused["content"][0]["text"]
    assert imports_of(app) == before and tables(app) == (1, 0)


def test_a_file_in_parts_goes_into_the_persons_own_bucket_only(app, agent, bot):  # noqa: F811
    token = token_of(agent, "read", "write")
    bucket = call_tool(bot, token, "create_bucket", name="Trade binder")["structuredContent"]
    pieces = cut(BASE, 2)
    args = {"parts": 2, **facts(BASE), "bucket_id": bucket["id"]}
    first = call_tool(bot, token, "import_collection_file", text=pieces[0], part=1, **args)["structuredContent"]
    last = call_tool(bot, token, "import_collection_file", text=pieces[1], part=2, **args)["structuredContent"]
    assert first["status"] == "part_received" and last["status"] == "uploaded" and last["bucket"]["id"] == bucket["id"]
    other = call_tool(bot, token, "import_collection_file", text=pieces[0], part=1, parts=2, **facts(BASE), bucket_id=bucket["id"] + 1000)
    assert other["isError"] is True  # not the person's bucket: the same 404 as the file route


# ---- parts: idempotency, replacement, what is missing ------------------------------------------------------------------------------

def test_a_part_sent_again_changes_nothing_and_a_different_text_for_its_number_replaces_it_until_the_file_is_complete(app, agent, bot):  # noqa: F811
    token = token_of(agent, "read", "write")
    pieces = cut(BASE, 4)
    send_part(bot, token, pieces, 1)
    with app.state.db.sessions() as db:
        first = db.get(StagedUploadPart, (db.query(StagedUpload).one().id, 1))
        digest, content = first.part_hash, bytes(first.content)
    assert digest == file_parts.part_hash(content) == file_parts.part_hash(file_parts.to_bytes(pieces[0]))
    again = send_part(bot, token, pieces, 1)["structuredContent"]  # the same part, the same hash: a no-op
    assert again["status"] == "part_received" and again["have"] == 1 and tables(app) == (1, 1)
    # a different text for number 1 (same file identity): replaced, one row
    replaced = send_part(bot, token, pieces, 1, text=pieces[0] + "")["structuredContent"]
    assert replaced["have"] == 1
    swapped = send_part(bot, token, pieces, 1, text=pieces[0].replace("Folder Name", "Folder Nome"))["structuredContent"]
    assert swapped["have"] == 1 and tables(app) == (1, 1)
    with app.state.db.sessions() as db:
        assert db.query(StagedUploadPart).one().part_hash == file_parts.part_hash(file_parts.to_bytes(pieces[0].replace("Folder Name", "Folder Nome")))
    # the replaced text no longer matches the whole: the checksum refuses it when the last part arrives, and nothing stays
    for i in (2, 3):
        send_part(bot, token, pieces, i)
    refused = send_part(bot, token, pieces, 4)["structuredContent"]
    assert refused["status"] == "mismatch" and refused["detail"] == "checksum" and tables(app) == (0, 0)
    # starting again with the right text works
    final = sent(bot, token, pieces)[-1]
    assert final["status"] == "uploaded"


def test_after_the_file_is_complete_a_part_sent_again_is_a_no_op_that_returns_the_same_preview(app, agent, bot):  # noqa: F811
    token = token_of(agent, "read", "write")
    pieces = cut(BASE, 3)
    final = sent(bot, token, pieces)[-1]
    again = send_part(bot, token, pieces, 2)["structuredContent"]
    assert again["status"] == "uploaded" and again["id"] == final["id"] and again["content_hash"] == final["content_hash"]
    differently = send_part(bot, token, pieces, 2, text="not the part at all")["structuredContent"]  # too late to replace anything
    assert differently["id"] == final["id"] and differently["content_hash"] == final["content_hash"]
    assert tables(app) == (1, 0)


def test_the_answer_to_a_part_names_the_parts_still_missing(app, agent, bot):  # noqa: F811
    token = token_of(agent, "read", "write")
    pieces = cut(BASE, 8)
    answers = sent(bot, token, pieces, order=[1, 2, 4, 5, 6, 8])
    last = answers[-1]
    assert last["status"] == "part_received" and last["missing"] == [3, 7] and last["have"] == 6 and last["parts"] == 8
    assert "parts 3 and 7 are still missing" in last["next"] and "nothing has been imported" in last["next"]
    assert "preview" not in last and "rows" not in last  # nothing is previewed before every part is here
    one = send_part(bot, token, pieces, 3)["structuredContent"]
    assert one["missing"] == [7] and "part 7 is still missing" in one["next"]
    assert send_part(bot, token, pieces, 7)["structuredContent"]["status"] == "uploaded"


def test_a_hundred_parts_count_once_against_the_import_allowance_and_a_new_upload_counts_again(app, agent, bot):  # noqa: F811
    token = token_of(agent)
    allowance = app.state.settings.import_limit
    text = BASE + "".join(f"my cards,1,0,Card {i},XYZ,Invented Set,{i},Mint,Normal,English,0.10,2024-01-01,0.05,0.10,0.08\n" for i in range(60))
    pieces = cut(text, 100)
    answers = sent(bot, token, pieces)
    assert len(answers) == 100 and answers[-1]["status"] == "uploaded" and all(a["status"] == "part_received" for a in answers[:-1])
    # one upload used one of the allowance: allowance - 1 more uploads (each a new file) pass, the next one is refused
    for n in range(allowance - 1):
        more = send_part(bot, token, [f"{text}x{n}\n"], 1)
        assert not more["isError"], n
    refused = send_part(bot, token, ["one more\n"], 1)
    assert refused["isError"] is True and "Too many" in refused["content"][0]["text"]


def test_the_parts_have_a_counter_of_their_own(app, agent, bot):  # noqa: F811
    object.__setattr__(app.state.settings, "import_part_limit", 5)  # the route reads the same settings object
    token = token_of(agent)
    pieces = cut(BASE, 8)
    answers = [send_part(bot, token, pieces, i) for i in range(1, 7)]
    assert [a["isError"] for a in answers] == [False] * 5 + [True]
    assert "Too many" in answers[-1]["content"][0]["text"]
    assert tables(app) == (1, 5)  # the five that were accepted are kept; the refused one stored nothing


# ---- a read that does not match the file ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("what, change, reason", [
    ("a line count that is one too many", lambda f: {"lines": f["lines"] + 1}, "lines"),
    ("a line count that is one too few", lambda f: {"lines": f["lines"] - 1}, "lines"),
    ("a checksum that is not the file's", lambda f: {"sum": f["sum"] ^ 1}, "checksum"),
])
def test_parts_that_do_not_match_the_file_are_refused_in_one_sentence_and_nothing_is_staged(app, agent, bot, what, change, reason):  # noqa: F811
    token = token_of(agent, "read", "write")
    pieces = cut(BASE, 3)
    before = imports_of(app)
    answers = [send_part(bot, token, pieces, i, **change(facts(BASE)))["structuredContent"] for i in (1, 2, 3)]
    refused = answers[-1]
    assert refused["status"] == "mismatch" and refused["detail"] == reason and refused["changed"] is False
    assert refused["say"].count(".") == 1 and "nothing was staged" in refused["say"] and "not changed" in refused["say"]
    assert "half the size" in refused["next"] and "rows" not in refused and "content_hash" not in refused
    assert tables(app) == (0, 0) and imports_of(app) == before


def test_a_read_cut_short_can_never_become_a_partial_import(app, agent, bot):  # noqa: F811
    """The script counts the file's lines in the shell, on the whole file; a part whose read was cut short has fewer."""
    token = token_of(agent, "read", "write")
    pieces = cut(BASE, 3)
    whole = facts(BASE)
    truncated = pieces[:2] + [pieces[2][:len(pieces[2]) // 2]]  # the tail of the last part is missing
    out = [send_part(bot, token, pieces, i, text=truncated[i - 1], **whole)["structuredContent"] for i in (1, 2, 3)]
    assert out[-1]["status"] == "mismatch" and tables(app) == (0, 0)
    # a half-sent file that is abandoned stays a pending set only, never a staged file with content
    send_part(bot, token, pieces, 1)
    with app.state.db.sessions() as db:
        assert [r.content for r in db.query(StagedUpload)] == [None]


def test_numbers_that_do_not_make_sense_are_answered_in_one_sentence_and_store_nothing(app, agent, bot):  # noqa: F811
    token = token_of(agent)
    bad = [dict(part=0, parts=3), dict(part=4, parts=3), dict(part=1, parts=file_parts.MAX_PARTS + 1), dict(part=1, parts=3, lines=-1),
           dict(part=1, parts=3, sum=2 ** 32)]
    for extra in bad:
        args = {"text": "a,b\n", "part": 1, "parts": 3, "lines": 1, "sum": 5, **extra}
        out = call_tool(bot, token, "import_collection_file", **args)["structuredContent"]
        assert out["status"] == "bad_part" and out["changed"] is False and out["say"].count(".") == 1, extra
    out = call_tool(bot, token, "import_collection_file", text="a,b\n", part=1)["structuredContent"]  # the others are missing
    assert out["status"] == "bad_part"
    assert tables(app) == (0, 0)


def test_a_part_that_is_not_valid_text_is_refused_without_storing_anything(app, agent, bot):  # noqa: F811
    token = token_of(agent)
    with TestClient(app) as http:
        res = http.post(f"{V1}/uploads/from-file", headers=auth(token), content=json.dumps(
            {"text": "a\ud800b", "part": 1, "parts": 1, "lines": 0, "sum": 0}).encode("ascii"))  # a lone surrogate, as JSON escapes it
    assert res.status_code in (200, 422) and (res.status_code == 422 or res.json()["status"] in ("bad_part", "mismatch")) and tables(app) == (0, 0)


# ---- the one import-size limit, for parts ------------------------------------------------------------------------------------------

def test_a_part_over_the_import_limit_is_refused_and_nothing_is_kept(app, agent, bot, monkeypatch):  # noqa: F811
    monkeypatch.setenv("IMPORT_MAX_BYTES", "500")
    token = token_of(agent)
    out = call_tool(bot, token, "import_collection_file", text="x" * 501, part=1, parts=1, lines=0, sum=1)["structuredContent"]
    assert out["status"] == "too_large" and "500 bytes" in out["say"] and out["say"].count(".") == 1
    assert out["changed"] is False and tables(app) == (0, 0)


def test_parts_that_add_up_to_more_than_the_import_limit_are_dropped_and_nothing_is_staged(app, agent, bot, monkeypatch):  # noqa: F811
    monkeypatch.setenv("IMPORT_MAX_BYTES", "1000")
    token = token_of(agent)
    pieces = ["a" * 400 + "\n", "b" * 400 + "\n", "c" * 400 + "\n", "d\n"]
    out = [send_part(bot, token, pieces, i)["structuredContent"] for i in (1, 2, 3)]
    assert [o["status"] for o in out] == ["part_received", "part_received", "too_large"]
    assert tables(app) == (0, 0)  # the set was removed with its parts
    assert out[-1]["say"].count(".") == 1 and "1,000 bytes" in out[-1]["say"]  # one plain sentence, naming the limit


def test_a_file_exactly_at_the_limit_is_taken(app, agent, bot, monkeypatch):  # noqa: F811
    token = token_of(agent)
    text = BASE
    monkeypatch.setenv("IMPORT_MAX_BYTES", str(len(text.encode("utf-8"))))
    assert sent(bot, token, cut(text, 2))[-1]["status"] == "uploaded"


# ---- tenancy, retention, the link cap --------------------------------------------------------------------------------------------

def test_another_person_cannot_reach_a_file_that_is_sent_in_parts(app, agent, bot):  # noqa: F811
    alice = token_of(agent, "read", "write")
    pieces = cut(BASE, 3)
    send_part(bot, alice, pieces, 1)
    final = sent(bot, alice, pieces)[-1]
    with TestClient(app) as other:
        other.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        bob = make_token(other, scopes=["read", "write"])
        for tool, args in (("get_staged_upload", {}), ("confirm_staged_upload", {"confirm": True, "content_hash": final["content_hash"]})):
            assert call_tool(bot, bob, tool, upload_id=final["id"], **args)["isError"] is True, tool
        # the same parts from another person are another set, and complete nothing of Alice's
        mine = sent(bot, bob, pieces)[-1]
        assert mine["status"] == "uploaded" and mine["id"] != final["id"]
        assert call_tool(bot, alice, "get_staged_upload", upload_id=mine["id"])["isError"] is True
    assert call_tool(bot, alice, "get_staged_upload", upload_id=final["id"])["structuredContent"]["status"] == "uploaded"


def test_parts_expire_with_the_staged_upload_and_go_with_the_account(app, agent, bot):  # noqa: F811
    token = token_of(agent, "read", "write")
    pieces = cut(BASE, 4)
    for i in (1, 2):
        send_part(bot, token, pieces, i)
    assert tables(app) == (1, 2)
    with app.state.db.sessions() as db:  # the upload's hour is over
        db.execute(sql("UPDATE staged_uploads SET expires_at = :t"), {"t": datetime.now(timezone.utc) - timedelta(minutes=1)})
        db.commit()
        assert retention.prune_staged_uploads(db) == 1
        db.commit()
    assert tables(app) == (0, 0)  # the parts went with it (ON DELETE CASCADE)
    for i in (1, 2):
        send_part(bot, token, pieces, i)
    with app.state.db.sessions() as db:
        from vault.privacy import purge_user
        uid = db.query(StagedUpload).one().user_id
        assert purge_user(db, uid)["staged_uploads"] == 1
    assert tables(app) == (0, 0)


def test_a_new_file_replaces_the_one_in_progress_and_a_file_in_progress_is_not_one_of_the_five_links(app, agent, bot):  # noqa: F811
    token = token_of(agent, "read", "write")
    pieces, other = cut(BASE, 4), cut(BASE + "my cards,1,0,Another,XYZ,Invented Set,2,Mint,Normal,English,0,2024-01-01,0,0,0\n", 4)
    send_part(bot, token, pieces, 1)
    send_part(bot, token, other, 1)  # another file: the earlier unfinished one is dropped with its parts
    assert tables(app) == (1, 1)
    for _ in range(uploads_open()):
        assert not call_tool(bot, token, "start_collection_upload")["isError"]
    with app.state.db.sessions() as db:
        pending = db.query(StagedUpload).filter(StagedUpload.ticket_hash.like("intake-%")).count()
    assert pending == 1 and tables(app)[1] == 1  # six links were asked for; the sixth replaced a link, never the file in progress


def uploads_open() -> int:
    from vault import uploads
    return uploads.MAX_OPEN + 1


def test_a_retry_with_the_same_key_replays_the_answer(app, agent):  # noqa: F811
    token = make_token(agent, scopes=["read", "write"])
    pieces = cut(BASE, 2)
    body = lambda i: {"text": pieces[i - 1], "part": i, "parts": 2, **facts(BASE)}  # noqa: E731
    with TestClient(app) as http:
        headers = {**auth(token), "Idempotency-Key": "parts-key-1"}
        first = http.post(f"{V1}/uploads/from-file", json=body(1), headers=headers)
        again = http.post(f"{V1}/uploads/from-file", json=body(1), headers=headers)
        assert first.status_code == again.status_code == 200 and first.json() == again.json()
        assert again.headers.get("idempotent-replayed") == "true" and first.json()["status"] == "part_received"
        last = http.post(f"{V1}/uploads/from-file", json=body(2), headers={**auth(token), "Idempotency-Key": "parts-key-2"})
        assert last.json()["status"] == "uploaded" and tables(app) == (1, 0)


# ---- what the model is told ----------------------------------------------------------------------------------------------------------

def test_a_call_with_neither_file_nor_text_is_answered_with_the_steps_apart_from_the_sentence_for_the_person(app, agent, bot):  # noqa: F811
    token = token_of(agent)
    out = call_tool(bot, token, "import_collection_file")
    answer = out["structuredContent"]
    assert not out["isError"] and answer["status"] == "cannot_receive" and answer["changed"] is False
    assert answer["say"] == "This app cannot hand the file to the Vault yet; nothing was changed, and 'Import CSV' on the Vault website works with the same file."
    assert answer["next"] == file_parts.steps() and "<PATH>" in answer["next"] and "exec_command" in answer["next"]
    assert "tools.mcp__the_vault__import_collection_file" in answer["next"] and "login:false" in answer["next"]
    assert "Never send the person to a link when a file was attached" in answer["next"]
    # the same for a string that is not a file, and over the compact list the coding apps are served
    assert call_tool(bot, token, "import_collection_file", file="C:\\Users\\x\\my_cards.csv")["structuredContent"]["next"] == answer["next"]
    session = hello(bot, token, "codex-mcp-client")
    via_codex = send(bot, token, session, "tools/call", {"name": "import_collection_file", "arguments": {}})["result"]["structuredContent"]
    assert via_codex["next"] == answer["next"]
    # a staged answer does not carry the recipe
    assert "<PATH>" not in sent(bot, token, cut(BASE, 2))[-1]["next"]


def test_the_tool_text_and_the_steps_tell_the_person_nothing_to_do():
    tool = mcp.BY_NAME["import_collection_file"]
    for text in (tool.description, file_parts.steps()):
        for phrase in ("tell the person", "ask the person to", "the person should", "the person must", "Tell the person"):
            assert phrase not in text, phrase
    assert "local path" in tool.description and "`text`" in tool.description and "`sum`" in tool.description


def test_the_log_holds_no_value_from_a_file_sent_in_parts(app, agent, bot, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    token = token_of(agent)
    secret = "SECRET-CARD-NAME-0451"
    text = BASE + f"my cards,1,0,{secret},XYZ,Invented Set,9,Mint,Normal,English,0.1,2024-01-01,0,0,0\n"
    pieces = cut(text, 3)
    sent(bot, token, pieces)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert secret not in logged and str(facts(text)["sum"]) not in logged
    calls = [json.loads(r.getMessage()) for r in caplog.records if r.name == "vault.access" and '"file_intake_call"' in r.getMessage()]
    assert len(calls) == 3 and all(c["arg_keys"] == ["lines", "part", "parts", "sum", "text"] for c in calls)
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "vault.access" and '"file_intake"' in r.getMessage()]
    assert [e["outcome"] for e in events] == ["part", "part", "staged"] and events[-1]["parts"] == 3 and events[-1]["bytes"] == len(text.encode())
    assert not {"text", "content", "sum"} & set().union(*events)


# ---- the table and its migration ------------------------------------------------------------------------------------------------------

def test_the_parts_migration_goes_down_and_up_again(blank_database_url):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    import vault.db
    from vault.db import Database

    database = Database(blank_database_url)
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))

    def step(direction, revision):
        with database.engine.begin() as conn:
            cfg.attributes["connection"] = conn
            getattr(command, direction)(cfg, revision)

    try:
        step("upgrade", "0124")
        assert "staged_upload_parts" in inspect(database.engine).get_table_names()
        columns = {c["name"] for c in inspect(database.engine).get_columns("staged_upload_parts")}
        assert columns == {"staged_upload_id", "part_index", "part_hash", "content"}
        fks = inspect(database.engine).get_foreign_keys("staged_upload_parts")
        assert fks[0]["referred_table"] == "staged_uploads" and fks[0]["options"].get("ondelete") == "CASCADE"
        step("downgrade", "0123")
        assert "staged_upload_parts" not in inspect(database.engine).get_table_names()
        step("upgrade", "0124")
        assert "staged_upload_parts" in inspect(database.engine).get_table_names()
    finally:
        database.engine.dispose()
