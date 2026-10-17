"""vault-0n9.3: the ``file_intake_call`` log event, one per import_collection_file call, so any real drop settles from the production
log what a chat app really sends for a file (a file object, a web address, a path, nothing). It adds a log line and changes no
answer. It holds shapes only: never a value, an address, a path, a file name, a token or anything about the person. Synthetic
data only."""

import hashlib
import json
import logging
from pathlib import Path

import httpx
import pytest

from tests.test_agents import V1, agent, bot, call_tool, make_token, rpc  # noqa: F401 - fixtures
from tests.test_file_intake import GOOD, file_arg, intake_with, token_of
from tests.test_mcp_profile import auth
from vault.api import mcp, mcp_profile

ROOT = Path(__file__).resolve().parent.parent
FIELDS = {"event", "ts", "request_id", "tool_definition_sha256", "arg_keys", "file_type", "file_keys", "protocol_version",
          "user_agent", "compact", "apps_ui"}
UI = {"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}}


def calls(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "vault.access" and '"file_intake_call"' in r.getMessage()]


def raw_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "vault.access" and '"file_intake_call"' in r.getMessage()]


def connect(bot, token, name="chatgpt-test", capabilities=None) -> str:
    res = bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": capabilities or {}, "clientInfo": {"name": name, "version": "0"}}},
        headers=auth(token))
    return res.headers["mcp-session-id"]


def post(bot, token, arguments, session=None, **headers):
    h = {**auth(token), **headers}
    if session:
        h["Mcp-Session-Id"] = session
    return bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                       "params": {"name": "import_collection_file", "arguments": arguments}}, headers=h)


def served_hash(bot, token, session=None) -> str:
    h = auth(token)
    if session:
        h["Mcp-Session-Id"] = session
    tools = bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}, headers=h).json()["result"]["tools"]
    entry = next(t for t in tools if t["name"] == "import_collection_file")
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


# ============================== the fields =====================================================================================

SHAPES = [
    ("the key is absent", {}, "absent", []),
    ("null", {"file": None}, "NoneType", []),
    ("a string", {"file": "chat_upload"}, "str", []),
    ("a local path", {"file": "C:\\Users\\someone\\Downloads\\my_cards.csv"}, "str", []),
    ("a list", {"file": ["a"]}, "list", []),
    ("a number", {"file": 7}, "int", []),
    ("an object with the four keys", {"file": file_arg()}, "dict", ["download_url", "file_id", "file_name", "mime_type"]),
    ("an object missing keys", {"file": {"file_id": "file-AbC123"}}, "dict", ["file_id"]),
    ("an empty object", {"file": {}}, "dict", []),
]


@pytest.mark.parametrize("label, arguments, file_type, file_keys", SHAPES, ids=[s[0] for s in SHAPES])
def test_the_event_holds_the_shape_of_the_call_and_nothing_else(app, agent, bot, caplog, label, arguments, file_type, file_keys):
    caplog.set_level(logging.DEBUG)
    intake_with(app, lambda r: httpx.Response(200, content=b"", headers={"content-type": "text/csv"}))
    token = token_of(agent)
    res = post(bot, token, {**arguments, "bucket_id": 5}, **{"MCP-Protocol-Version": "2025-06-18", "User-Agent": "openai-mcp/1.2.3"})
    assert res.status_code == 200 and "error" not in res.json()  # the answer is the answer it always was
    (event,) = calls(caplog)
    assert set(event) == FIELDS and event["event"] == "file_intake_call"
    assert event["file_type"] == file_type and event["file_keys"] == file_keys
    assert event["arg_keys"] == sorted([*arguments, "bucket_id"])
    assert event["protocol_version"] == "2025-06-18" and event["user_agent"] == "openai-mcp/1.2.3"
    assert len(event["tool_definition_sha256"]) == 12 and int(event["tool_definition_sha256"], 16) >= 0
    assert event["compact"] is False and event["apps_ui"] is None  # no session id was sent


def test_the_event_is_logged_for_this_tool_only(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    token = token_of(agent)
    call_tool(bot, token, "whoami")
    call_tool(bot, token, "start_collection_upload")
    assert calls(caplog) == []


def test_missing_headers_are_logged_as_absent_and_unknown(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    res = bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "import_collection_file", "arguments": {}}}, headers={**auth(token_of(agent)), "User-Agent": ""})
    assert res.status_code == 200
    (event,) = calls(caplog)
    assert event["protocol_version"] == "absent" and event["user_agent"] == "unknown"


# ============================== what this client was shown ====================================================================

def test_the_hash_is_that_of_the_tool_entry_the_client_was_shown_full_or_compact(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    token = token_of(agent)
    full = connect(bot, token, "chatgpt-test", UI)
    compact = connect(bot, token, "codex-mcp-client", UI)
    plain = connect(bot, token, "chatgpt-test", {})  # a host that did not advertise MCP Apps: no view links in the entry
    for session in (full, compact, plain):
        post(bot, token, {}, session)
    a, b, c = calls(caplog)
    assert a["tool_definition_sha256"] == served_hash(bot, token, full) and (a["compact"], a["apps_ui"]) == (False, True)
    assert b["tool_definition_sha256"] == served_hash(bot, token, compact) and (b["compact"], b["apps_ui"]) == (True, True)
    assert c["tool_definition_sha256"] == served_hash(bot, token, plain) and (c["compact"], c["apps_ui"]) == (False, False)
    assert len({a["tool_definition_sha256"], b["tool_definition_sha256"]}) == 2  # the compact entry is another text
    assert mcp_profile.compact_schema(mcp.BY_NAME["import_collection_file"], True)  # the entry exists in the compact list


def test_the_hash_changes_when_the_tool_definition_changes(app, agent, bot, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    token = token_of(agent)
    post(bot, token, {})
    monkeypatch.setattr(mcp.BY_NAME["import_collection_file"], "description", "Another text.")
    post(bot, token, {})
    first, second = calls(caplog)
    assert first["tool_definition_sha256"] != second["tool_definition_sha256"]


# ============================== never a value, bounded ========================================================================

SECRETS = ["sig-9f2c71d4e0a5-SECRET-TOKEN", "jane.doe@example.com", "my_cards", "someone", "alien51", "Downloads", "secret-host.example"]
LONG = "x" * 5000 + "TAILMARK"


def test_hostile_values_never_reach_the_log_and_the_record_is_bounded(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    token = token_of(agent)
    hostile = [
        {"file": f"https://secret-host.example/file-AbC?se=2026&sig={SECRETS[0]}"},  # a web address with a token in the query
        {"file": "C:\\Users\\someone\\Downloads\\my_cards.csv"},  # a local path
        {"file": "/home/alien51/Downloads/my_cards.csv"},
        {"file": LONG},  # a long string
        {"file": "line\x00one\x1b[31m\r\ntwo\x7f"},  # control characters
        {"file": {"download_url": f"https://secret-host.example/x?sig={SECRETS[0]}", "file_id": "file-" + LONG,
                  "file_name": "my_cards.csv", "mime_type": "text/csv"}},
        {"file": {SECRETS[0]: 1, SECRETS[1]: 2, "k" * 5000: 3, "path_C:\\Users\\someone": 4, "ok_key": 5}},  # hostile key names
        {"file": file_arg(), SECRETS[0]: "x", SECRETS[1]: ["y"], "z" * 5000: 1},  # hostile argument names
    ]
    for arguments in hostile:
        assert post(bot, token, arguments).status_code in (200, 400)
    lines = raw_lines(caplog)
    assert len(lines) == len(hostile)
    everything = "\n".join(lines)
    for secret in (*SECRETS, "TAILMARK", "\x00", "\x1b", "\x7f", "file-xxxx", "xxxxx"):
        assert secret not in everything, secret
    for line in lines:
        assert len(line) < 1400, len(line)
        assert set(json.loads(line)) == FIELDS
    assert [e["file_type"] for e in calls(caplog)] == ["str", "str", "str", "str", "str", "dict", "dict", "dict"]
    keys = calls(caplog)[6]["file_keys"]  # names that are not letters and underscores are logged as "other"
    assert keys == ["ok_key", "other"]
    assert calls(caplog)[7]["arg_keys"] == ["file", "other"]


def test_the_record_is_bounded_even_when_every_list_is_full(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    names = {f"key_{chr(97 + i)}_{'z' * 17}": 1 for i in range(26)}  # 24-character names, more than the cap
    post(bot, token_of(agent), {"file": names, **names}, **{"MCP-Protocol-Version": "2025-06-18", "User-Agent": " ".join(f"{'p' * 30}/{'9' * 15}" for _ in range(9))})
    (line,) = raw_lines(caplog)
    event = json.loads(line)
    assert len(event["arg_keys"]) == len(event["file_keys"]) == 12 and event["user_agent"].count("/") == 4
    assert len(line) < 1400, len(line)


def test_hostile_headers_are_cut_to_a_date_and_product_tokens(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    token = token_of(agent)
    post(bot, token, {}, **{"MCP-Protocol-Version": "2025-06-18; sig=SECRET-9f2c", "User-Agent": "Name/1 " + "A" * 3000})
    post(bot, token, {}, **{"MCP-Protocol-Version": "9" * 4000})
    first, second = calls(caplog)
    assert first["protocol_version"] == "other" and second["protocol_version"] == "other"
    assert "SECRET" not in "\n".join(raw_lines(caplog)) or first["user_agent"].startswith("sig-")  # a lone token is the form we keep
    assert len(first["user_agent"]) <= 4 * 48


UA = [
    # a host name, a user name, an e-mail and a Windows path in comments: nothing but the product tokens survives
    ("Mozilla/5.0 (Windows NT 10.0; ALIEN51-PC; dicol; jane.doe@example.com; C:\\Users\\dicol\\AppData) ChatGPT/1.2025.280 Electron/38.0",
     "Mozilla/5.0 ChatGPT/1.2025.280 Electron/38.0"),
    ("openai-mcp/1.0 (+https://openai.com/bot; contact: jane.doe@example.com)", "openai-mcp/1.0"),
    ("a/1 (nested (comment) with dicol) b/2", "a/1 b/2"),
    ("a/1 (never closed C:\\Users\\dicol b/2", "a/1"),  # an unclosed comment runs to the end
    ("a/1 stray) dicol b/2", "a/1 b/2"),
    ("a/1 b/2 c/3 d/4 e/5 f/6", "a/1 b/2 c/3 d/4"),  # at most four tokens
    ("ALIEN51 dicol jane.doe@example.com C:\\Users\\dicol host.example.com/ /etc/passwd", "unknown"),  # no name/version token at all
    ("dicol@alien51/1.0 C:\\Users\\dicol/1.0 name/ver /1.0 a/1\x00b ok/2", "ok/2"),  # a token must be exactly name/version
    ("", "unknown"),
    (None, "unknown"),
]


@pytest.mark.parametrize("header, kept", UA)
def test_the_user_agent_keeps_product_tokens_only(header, kept):
    assert mcp._product_tokens(header) == kept


def test_a_user_agent_with_a_machine_and_a_person_in_it_leaves_neither_in_the_log(app, agent, bot, caplog):
    caplog.set_level(logging.DEBUG)
    intake_with(app)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; host=ALIEN51-PC; user=dicol; mail=jane.doe@example.com; C:\\Users\\dicol\\x) ChatGPT/1.0"
    post(bot, token_of(agent), {}, **{"User-Agent": ua})
    (event,) = calls(caplog)
    assert event["user_agent"] == "Mozilla/5.0 ChatGPT/1.0"
    for word in ("ALIEN51", "dicol", "jane.doe", "example.com", "C:", "Windows"):
        assert word not in "\n".join(raw_lines(caplog)), word


# ============================== no behaviour change ===========================================================================

def test_a_log_that_cannot_be_written_does_not_change_the_answer(app, agent, bot, caplog, monkeypatch):
    intake_with(app)
    token = token_of(agent)
    before = post(bot, token, {"file": "chat_upload"}).json()["result"]

    def boom(*a, **k):
        raise RuntimeError("no log today")

    monkeypatch.setattr(mcp, "file_intake_call_fields", boom)
    after = post(bot, token, {"file": "chat_upload"}).json()["result"]
    assert after == before and after["structuredContent"]["status"] == "cannot_receive"


# ============================== the words around it ===========================================================================

def test_the_skill_says_what_to_do_when_the_app_gives_no_file_and_makes_no_promise_that_the_app_fills_it_in():
    text = " ".join((ROOT / "skills" / "collection-analyst" / "SKILL.md").read_text(encoding="utf-8").split())
    assert "call `import_collection_file` with the attached file as `file`" in text
    assert "If the app does not give you a file" in text and "no file object, only a path or nothing" in text
    assert "do not call `import_collection_file` with no arguments: call `start_collection_upload` and give the person the link" in text
    assert "nothing is imported until their yes" in text
    assert "at once, with `file`" not in text and "pass the attachment itself" not in text  # the old promise is gone
    assert "Never ask the person to fix, edit, re-export or re-upload the file" in text  # the experience rule stays
    for copy in ("the-vault", "the-vault-openai"):
        assert (ROOT / "plugins" / copy / "skills" / "collection-analyst" / "SKILL.md").read_text(encoding="utf-8").replace("\r\n", "\n") == \
            (ROOT / "skills" / "collection-analyst" / "SKILL.md").read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_server_instructions_are_not_changed_by_this_event():
    assert "file_intake_call" not in mcp.INSTRUCTIONS and "import_collection_file" not in mcp.INSTRUCTIONS


def test_the_logging_note_lists_exactly_the_fields_of_the_event():
    doc = (ROOT / "docs" / "mcp-oauth-threat-model.md").read_text(encoding="utf-8")
    start = doc.index("`file_intake_call` event")
    note = doc[start: doc.index("\n- ", start)]
    named = {f for f in FIELDS if f"`{f}`" in note}
    assert named == FIELDS, FIELDS - named
    assert "no value" in note.lower() and "nothing about the person" in note
