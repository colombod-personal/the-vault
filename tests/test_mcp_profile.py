"""The compact tool list for hosts that give a plugin's MCP tools a size budget (vault/api/mcp_profile.py).

Why it exists: on 2026-10-11 the ChatGPT desktop app showed the assistant only 30 of the Vault's 81 tools, an alphabetical
prefix, so list_decks and whoami were never there and it said "the deck-list tool isn't available" (vault-wxy.5). A client
whose initialize names it ``codex-mcp-client`` now gets the core tools plus vault_tools, vault_call (reads) and vault_write
(changes); everyone else keeps the full list. The dispatchers go through the same path as a direct call, so a token's
scopes decide; what they refuse first is presentation policy.
"""

import json
from pathlib import Path

import yaml

from tests.test_agents import auth, call_tool, make_token
from tests.test_mcp_catalog import agent, bot  # noqa: F401  (fixtures)
from vault.api import mcp, mcp_profile, mcp_session

SECRET = "unit-test-secret"
BUDGET_BYTES = 30_000  # measured: the host kept ~36 KB of the Vault's tools in the owner's app, ~70 KB in a clean home
SKILLS = Path(__file__).parent.parent / "skills"
L, C, W = mcp_profile.LOOKUP, mcp_profile.CALL, mcp_profile.WRITE


def without_prose(node):
    """A JSON schema with every description removed: what is left is what a call is checked against."""
    if isinstance(node, dict):
        return {k: without_prose(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [without_prose(v) for v in node]
    return node


def hello(bot, token, name):  # noqa: F811
    """initialize as ``name``; returns the session id the server issued (what the client sends back from then on)."""
    res = bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name, "version": "0"}}},
        headers=auth(token))
    assert res.status_code == 200, res.text
    return res.headers.get("mcp-session-id")


def send(bot, token, session, method, params=None, **headers):  # noqa: F811
    h = {**auth(token), **headers}
    if session:
        h["Mcp-Session-Id"] = session if session.isascii() else session.encode()  # httpx sends bytes as they are
    return bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}}, headers=h).json()


def run(bot, token, session, via, name, arguments=None):  # noqa: F811
    return send(bot, token, session, "tools/call", {"name": via, "arguments": {"name": name, **({} if arguments is None else {"arguments": arguments})}})


def listed(bot, token, session):  # noqa: F811
    return send(bot, token, session, "tools/list")["result"]["tools"]


# ---- the list --------------------------------------------------------------------------------------------------------------


def test_the_codex_client_gets_the_core_tools_and_the_dispatchers_within_the_budget(agent, bot):  # noqa: F811
    token = make_token(agent, scopes=("read", "write"))
    session = hello(bot, token, "codex-mcp-client")
    tools = listed(bot, token, session)
    assert [t["name"] for t in tools] == [*mcp_profile.CORE, L, C, W]
    assert {"whoami", "list_decks", "get_deck", "search_cards", "get_collection_summary", "get_rule"} <= {t["name"] for t in tools}
    assert len(json.dumps(tools)) < BUDGET_BYTES, f"{len(json.dumps(tools))} bytes: the host would drop the tail"
    # the same arguments as the full definition: a call built from the short text is checked exactly as before
    for t in tools[: len(mcp_profile.CORE)]:
        full = mcp.BY_NAME[t["name"]].schema(ui=False)
        assert without_prose(t["inputSchema"]) == without_prose(full["inputSchema"]), t["name"]  # every type, enum, bound, default
        assert t["annotations"] == full["annotations"]  # each keeps its own approval hints
        assert t["name"] == full["name"] and t["title"] == full["title"]
    by = {t["name"]: t for t in tools}
    assert by[L]["annotations"]["readOnlyHint"] is True and by[C]["annotations"]["readOnlyHint"] is True
    # it can run update_deck (replaces text), set_*_metadata (replaces a namespace) and rename_tag (merges): the host asks first
    assert by[W]["annotations"]["readOnlyHint"] is False and by[W]["annotations"]["destructiveHint"] is True
    assert len(tools) <= 30, "the tool count is part of what the host budgets"
    assert not set(mcp_profile.DISPATCHERS) & set(mcp.BY_NAME), "a runner must not share a name with a catalog tool"


def test_the_compact_list_stays_within_both_guards_with_and_without_the_views():
    """The two guards, measured the way the host counts them, for the largest list (a token that may write), with the MCP Apps
    view links in the tool definitions (ui=True) and without (ui=False). The core list is at the limit: a new core tool is a
    trade for one that is rarely needed, never a raise of these numbers."""
    for ui in (False, True):
        tools = mcp_profile.tool_list(mcp.BY_NAME, ("read", "write"), ui=ui)
        size = len(json.dumps(tools))
        assert len(tools) <= 30, f"ui={ui}: {len(tools)} tools"
        assert size < BUDGET_BYTES, f"ui={ui}: {size} bytes"


def test_sign_out_is_listed_directly_to_every_sign_in_and_the_runners_will_not_run_it(agent, bot):  # noqa: F811
    """It ends the caller's own OAuth connection, so a read-only sign-in has it too (Tool.any_scope); it is destructive, so it is
    listed with its own approval hint and never reached through vault_call or vault_write (vault-wxy.3.1)."""
    for scopes in (("read",), ("read", "write")):
        token = make_token(agent, scopes=scopes)
        session = hello(bot, token, "codex-mcp-client")
        by = {t["name"]: t for t in listed(bot, token, session)}
        assert "sign_out" in by and "sign_out" in mcp_profile.CORE
        assert by["sign_out"]["annotations"]["destructiveHint"] is True and by["sign_out"]["annotations"]["readOnlyHint"] is False
        assert by["sign_out"]["description"] == mcp_profile.COMPACT["sign_out"] and "confirm" in by["sign_out"]["description"]
        for via in (C, W):
            refused = run(bot, token, session, via, "sign_out", {"confirm": True})
            assert refused["error"]["code"] == -32602 and "listed directly" in refused["error"]["message"], (scopes, via)
        one = send(bot, token, session, "tools/call", {"name": L, "arguments": {"name": "sign_out"}})["result"]["structuredContent"]
        assert "listed directly" in one["run_with"]
        rows = send(bot, token, session, "tools/call", {"name": L, "arguments": {}})["result"]["structuredContent"]["tools"]
        assert "sign_out" not in {r["name"] for r in rows}  # it is in the list already, not behind a runner
    # a direct call behaves as in the full list: this personal access token is not an app connection, so it is refused with where
    # to remove it, and keeps working
    direct = send(bot, token, session, "tools/call", {"name": "sign_out", "arguments": {"confirm": True}})["result"]
    assert direct["isError"] is True and "Agents & API" in json.dumps(direct["structuredContent"])
    assert call_tool(bot, token, "whoami")["isError"] is False


def test_a_token_that_may_only_read_is_shown_no_write_tool_and_no_writer(agent, bot):  # noqa: F811
    token = make_token(agent)
    session = hello(bot, token, "codex-mcp-client")
    names = [t["name"] for t in listed(bot, token, session)]
    # the upload link and signing out (Tool.any_scope) are the only write tools a read-only sign-in is shown
    assert names == [n for n in mcp_profile.CORE if mcp.BY_NAME[n].listed_for({"read"})] + [L, C]
    assert {"start_collection_upload", "sign_out"} <= set(names)
    assert {n for n in names if n in mcp.BY_NAME and mcp.BY_NAME[n].write} == {"start_collection_upload", "sign_out"}


def test_every_other_client_and_an_older_client_keep_the_full_list(agent, bot):  # noqa: F811
    for scopes in (("read",), ("read", "write")):
        token = make_token(agent, scopes=scopes)
        wanted = {t.name for t in mcp.TOOLS if t.listed_for(set(scopes))}
        for session in (hello(bot, token, "claude-ai"), hello(bot, token, "openai-mcp"), hello(bot, token, "Codex-MCP-Client"), None):
            names = {t["name"] for t in listed(bot, token, session)}
            assert names == wanted and not names & set(mcp_profile.DISPATCHERS)


def test_a_session_id_with_non_ascii_text_falls_back_to_the_full_list(agent, bot):  # noqa: F811
    token = make_token(agent)
    names = {t["name"] for t in listed(bot, token, "v1.0.ab.é")}
    assert names == {t.name for t in mcp.TOOLS if t.listed_for({"read"})}


def test_core_descriptions_are_whole_sentences_and_nothing_is_cut_mid_clause():
    for name in mcp_profile.CORE:
        text = mcp_profile.compact_schema(mcp.BY_NAME[name])["description"]
        assert text == mcp_profile.COMPACT[name]
        assert 20 <= len(text) <= 190 and text.endswith(".") and "…" not in text, (name, text)
        assert text.count("(") == text.count(")"), (name, text)
    # the heuristic used for parameters and for vault_tools summaries: never an open parenthesis, never a trailing "e.g."
    for t in mcp.TOOLS:
        for text in (mcp_profile.first_sentence(t.description, mcp_profile.DESC_LISTED),
                     *(mcp_profile.first_sentence(v.get("description", ""), mcp_profile.DESC_PROP) for v in t.properties.values() if isinstance(v, dict))):
            assert text.count("(") == text.count(")"), (t.name, text)
            assert not text.rstrip("…").rstrip().endswith(("e.g.", "i.e.", "(", ",")), (t.name, text)
    assert mcp_profile.first_sentence("One rule by number (e.g. 702.19b) with its text. Another sentence.", 150) == "One rule by number (e.g. 702.19b) with its text."
    long = "A deck at a glance: its name, `overview` (format, commander(s), card count, colour identity), where it came from, and more."
    assert mcp_profile.first_sentence(long, 60) == "A deck at a glance: its name…"


# ---- vault_tools -----------------------------------------------------------------------------------------------------------


def test_vault_tools_lists_the_rest_and_shows_one_tool_in_full(agent, bot):  # noqa: F811
    token = make_token(agent, scopes=("read", "write"))
    session = hello(bot, token, "codex-mcp-client")
    out = send(bot, token, session, "tools/call", {"name": L, "arguments": {}})["result"]["structuredContent"]
    rows = {r["name"]: r for r in out["tools"]}
    assert "list_buckets" in rows and "create_bucket" in rows and not set(rows) & set(mcp_profile.CORE)
    assert rows["list_buckets"]["run_with"] == C and rows["create_bucket"]["run_with"] == W and rows["delete_deck"]["run_with"] is None
    assert all(len(r["summary"]) <= mcp_profile.DESC_LISTED + 1 for r in rows.values())
    one = send(bot, token, session, "tools/call", {"name": L, "arguments": {"name": "list_buckets"}})["result"]["structuredContent"]
    assert one["name"] == "list_buckets" and one["description"] == mcp.BY_NAME["list_buckets"].description and one["run_with"] == C
    core = send(bot, token, session, "tools/call", {"name": L, "arguments": {"name": "list_decks"}})["result"]["structuredContent"]
    assert "listed directly" in core["run_with"]
    words = send(bot, token, session, "tools/call", {"name": L, "arguments": {"query": "bucket"}})["result"]["structuredContent"]
    assert words["count"] and all("bucket" in f"{r['name']} {r['summary']}".lower() for r in words["tools"])
    assert send(bot, token, session, "tools/call", {"name": L, "arguments": {"name": "no_such_tool"}})["result"]["isError"] is True


def test_a_read_only_sign_in_is_not_told_about_the_write_tools(agent, bot):  # noqa: F811
    token = make_token(agent)  # read only
    session = hello(bot, token, "codex-mcp-client")
    out = send(bot, token, session, "tools/call", {"name": L, "arguments": {}})["result"]["structuredContent"]
    assert out["tools"] and not any(row["writes"] for row in out["tools"])
    assert send(bot, token, session, "tools/call", {"name": L, "arguments": {"name": "create_bucket"}})["result"]["isError"]


# ---- vault_call and vault_write --------------------------------------------------------------------------------------------


def test_vault_call_runs_the_named_read_tool_exactly_like_a_direct_call(agent, bot):  # noqa: F811
    token = make_token(agent)
    session = hello(bot, token, "codex-mcp-client")
    direct = call_tool(bot, token, "list_buckets")
    via = run(bot, token, session, C, "list_buckets", {})["result"]
    assert via["isError"] is direct["isError"] is False
    assert via["structuredContent"] == direct["structuredContent"]
    bad = run(bot, token, session, C, "get_deck_ideas", {})  # arguments are checked against the inner tool's own schema
    assert bad["error"]["code"] == -32602 and "Invalid arguments" in bad["error"]["message"]


def test_vault_call_will_not_run_a_tool_that_changes_something_and_vault_write_will_not_run_a_read(agent, bot):  # noqa: F811
    token = make_token(agent, scopes=("read", "write"))
    session = hello(bot, token, "codex-mcp-client")
    changes = run(bot, token, session, C, "create_bucket", {"name": "Trade binder"})
    assert changes["error"]["code"] == -32602 and W in changes["error"]["message"]
    reads = run(bot, token, session, W, "list_buckets", {})
    assert reads["error"]["code"] == -32602 and C in reads["error"]["message"]


def test_vault_write_runs_a_non_destructive_write_like_a_direct_call_and_does_not_widen_the_scope(agent, bot):  # noqa: F811
    writer = make_token(agent, scopes=("read", "write"))
    session = hello(bot, writer, "codex-mcp-client")
    direct = call_tool(bot, writer, "create_bucket", name="Direct binder")
    via = run(bot, writer, session, W, "create_bucket", {"name": "Dispatched binder"})["result"]
    assert direct["isError"] is False and via["isError"] is False
    buckets = json.dumps(call_tool(bot, writer, "list_buckets")["structuredContent"])
    assert "Direct binder" in buckets and "Dispatched binder" in buckets  # it exists, not only "no error"
    reader = make_token(agent)  # read only: whatever the API says to the direct call is what the dispatcher says
    session = hello(bot, reader, "codex-mcp-client")
    refused_direct = call_tool(bot, reader, "create_bucket", name="Nope")
    refused_via = run(bot, reader, session, W, "create_bucket", {"name": "Nope"})["result"]
    assert refused_direct["isError"] is True
    assert (refused_via["isError"], refused_via["structuredContent"]) == (refused_direct["isError"], refused_direct["structuredContent"])
    assert W not in [t["name"] for t in listed(bot, reader, session)]


def test_the_dispatchers_refuse_tools_that_delete_or_revoke_the_core_tools_and_themselves(agent, bot):  # noqa: F811
    token = make_token(agent, scopes=("read", "write"))
    session = hello(bot, token, "codex-mcp-client")
    for t in mcp.TOOLS:
        if t.destructive and t.name not in mcp_profile.CORE:
            for via in (C, W):
                refused = run(bot, token, session, via, t.name, {})
                assert refused["error"]["code"] == -32602 and "web app" in refused["error"]["message"], (via, t.name)
    for name in mcp_profile.CORE:  # listed directly: called by name, not through a dispatcher
        refused = run(bot, token, session, W if mcp.BY_NAME[name].write else C, name, {})
        assert refused["error"]["code"] == -32602 and "listed directly" in refused["error"]["message"], name
    for via in (C, W):
        for name in (*mcp_profile.DISPATCHERS, "no_such_tool"):
            refused = run(bot, token, session, via, name, {})
            assert refused["error"]["code"] == -32602 and "Unknown tool" in refused["error"]["message"], (via, name)


def test_a_name_that_is_not_exactly_a_tool_name_is_refused(agent, bot):  # noqa: F811
    token = make_token(agent, scopes=("read", "write"))
    session = hello(bot, token, "codex-mcp-client")
    for name in (" list_buckets", "List_Buckets", "list_buckets ", "list_buck​ets", "list_buckets" + "x" * 90, ""):
        refused = run(bot, token, session, C, name, {})
        assert refused["error"]["code"] == -32602, repr(name)
    for bad in ("not an object", ["x"], 3):
        refused = send(bot, token, session, "tools/call", {"name": C, "arguments": {"name": "list_buckets", "arguments": bad}})
        assert refused["error"]["code"] == -32602, repr(bad)
    assert send(bot, token, session, "tools/call", {"name": C, "arguments": {}})["error"]["code"] == -32602


def test_a_batch_of_dispatched_calls_is_answered_per_call(agent, bot):  # noqa: F811
    token = make_token(agent)
    session = hello(bot, token, "codex-mcp-client")
    batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": C, "arguments": {"name": "list_buckets", "arguments": {}}}}
             for i in (1, 2)]
    res = bot.post("/api/mcp", json=batch, headers={**auth(token), "Mcp-Session-Id": session}).json()
    assert [r["id"] for r in res] == [1, 2] and all(r["result"]["isError"] is False for r in res)


def test_a_batch_of_dispatched_writes_keeps_one_idempotency_key_per_call(agent, bot):  # noqa: F811
    token = make_token(agent, scopes=("read", "write"))
    session = hello(bot, token, "codex-mcp-client")
    batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call",
              "params": {"name": W, "arguments": {"name": "create_bucket", "arguments": {"name": f"Batch {i}"}}}} for i in (1, 2)]
    res = bot.post("/api/mcp", json=batch, headers={**auth(token), "Mcp-Session-Id": session, "Idempotency-Key": "one-key"}).json()
    assert all(r["result"]["isError"] is False for r in res)
    buckets = json.dumps(call_tool(bot, token, "list_buckets")["structuredContent"])
    assert "Batch 1" in buckets and "Batch 2" in buckets  # the second was not taken for a replay of the first


def test_a_retried_keyed_vault_write_is_not_applied_twice(agent, bot):  # noqa: F811
    token = make_token(agent, scopes=("read", "write"))
    session = hello(bot, token, "codex-mcp-client")
    for _ in range(2):
        res = send(bot, token, session, "tools/call", {"name": W, "arguments": {"name": "create_bucket", "arguments": {"name": "Retry binder"}}},
                   **{"Idempotency-Key": "retry-key-1"})
        assert res["result"]["isError"] is False
    assert json.dumps(call_tool(bot, token, "list_buckets")["structuredContent"]).count("Retry binder") == 1


def test_a_write_tool_the_read_only_runner_will_not_run_points_at_the_writer_only_when_it_is_shown(agent, bot):  # noqa: F811
    writer = make_token(agent, scopes=("read", "write"))
    reader = make_token(agent)
    for token, wants in ((writer, W), (reader, "may write")):
        session = hello(bot, token, "codex-mcp-client")
        refused = run(bot, token, session, C, "create_bucket", {"name": "x"})
        assert wants in refused["error"]["message"], (wants, refused)


def test_the_instructions_tell_a_compact_client_how_to_reach_the_tools_that_are_not_listed(agent, bot):  # noqa: F811
    token = make_token(agent)
    for name, compact in (("codex-mcp-client", True), ("claude-ai", False)):
        res = bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name, "version": "0"}}}, headers=auth(token))
        text = res.json()["result"]["instructions"]
        assert (mcp_profile.LOOKUP in text and mcp_profile.CALL in text) is compact
        assert text.startswith(mcp.INSTRUCTIONS)


def test_a_core_write_tool_refused_to_a_read_only_sign_in_says_it_needs_write_access(agent, bot):  # noqa: F811
    token = make_token(agent)
    session = hello(bot, token, "codex-mcp-client")
    refused = run(bot, token, session, W, "update_owned_cards", {})
    assert refused["error"]["code"] == -32602 and "may write" in refused["error"]["message"]


def test_a_direct_call_by_name_behaves_as_it_always_did_in_a_compact_session(agent, bot):  # noqa: F811
    """The refusal above is presentation policy, not enforcement: the REST API decides, for a direct call too."""
    token = make_token(agent, scopes=("read", "write"))
    session = hello(bot, token, "codex-mcp-client")
    res = send(bot, token, session, "tools/call", {"name": "delete_deck", "arguments": {"deck_id": 999999}})
    assert "error" not in res and res["result"]["isError"] is True  # the API's answer, not the dispatcher's refusal
    assert "404" in json.dumps(res["result"]["structuredContent"]) or "No saved deck" in json.dumps(res["result"]["structuredContent"])


# ---- coverage derived from the catalog -------------------------------------------------------------------------------------

# Write tools that are NOT marked destructive although they replace, merge, or confirm something, and why that was reviewed.
# Anything here is run through vault_write, whose own approval hint is destructive, unless it is also in CORE.
REVIEWED_NOT_DESTRUCTIVE = {
    "tag_cards": "adds tags to cards; previewed above 25 cards; the tags are removed with untag_cards (the web app for a compact client)",
    "refresh_prices": "re-reads prices from Scryfall into the person's own collection; force only skips the freshness check",
    "move_cards": "moves copies between buckets; previewed above 10 cards, nothing is lost",
    "import_deck_from_link": "saves a deck read from a link; with update and confirm it replaces the saved copy of that one deck, so it is listed directly",
    "update_deck": "overwrites one saved deck with the person's own edit; versioned, and the host asks (vault_write is destructive)",
    "rename_tag": "merges two of the person's own tags; the host asks (vault_write is destructive)",
    "rename_bucket": "renames one of the person's own buckets; the host asks (vault_write is destructive)",
    "set_card_metadata": "replaces one namespace of a card's notes ({} removes it); the host asks (vault_write is destructive)",
    "set_bucket_metadata": "replaces one namespace of a bucket's notes ({} removes it); the host asks (vault_write is destructive)",
}
# Tools that replace the person's saved data on a flag, which must be listed directly (never behind a runner).
REPLACING_MUST_BE_LISTED = ("import_deck_from_link", "refresh_deck")
CONFIRMING = ("confirm", "update", "replace", "replace_everything", "force")
DESTRUCTIVE_NAMES = ("delete_", "reset_", "untag_", "stop_", "remove_", "clear_", "revoke_")
SAMPLES = ({}, {"confirm": True}, {"confirm": False}, {"update": True, "confirm": True}, {"deck_id": 1, "confirm": True},
           {"upload_id": "u", "confirm": True}, {"share_id": 1}, {"tag": "x"}, {"bucket": 1})


def methods_of(tool):
    """Every HTTP method a tool can use: a fixed one, or each one its function returns for sample arguments."""
    if isinstance(tool.method, str):
        return {tool.method}
    found = set()
    for sample in SAMPLES:
        try:
            found.add(tool.method(sample))
        except Exception:  # noqa: BLE001  (a sample the function does not accept)
            pass
    return found


def test_every_tool_that_deletes_or_replaces_is_marked_destructive_or_reviewed():
    for t in mcp.TOOLS:
        verbs = methods_of(t)
        assert verbs, f"{t.name}: could not work out its HTTP method"
        if "DELETE" in verbs:
            assert t.destructive, f"{t.name} can DELETE"
        if t.name.startswith(DESTRUCTIVE_NAMES):
            assert t.destructive, f"{t.name}: deletes or revokes, so the dispatchers must refuse it"
        if t.write and not t.destructive and (verbs & {"PUT", "PATCH"} or any(k in t.properties for k in CONFIRMING)):
            assert t.name in REVIEWED_NOT_DESTRUCTIVE, f"{t.name} replaces or merges (or takes a confirmation flag): mark it destructive or record why not"
    assert set(REVIEWED_NOT_DESTRUCTIVE) <= {t.name for t in mcp.TOOLS}


def test_the_tools_that_replace_saved_data_on_a_flag_are_listed_directly():
    derived = {t.name for t in mcp.TOOLS if t.write and not t.destructive and ("update" in t.properties or "replace" in t.properties)}
    assert derived, "the derivation must see import_deck_from_link"
    for name in {*REPLACING_MUST_BE_LISTED, *derived}:
        assert name in mcp_profile.CORE, f"{name} replaces saved data: a runner would hide its own approval hint"


def test_every_skill_that_names_a_tool_outside_the_core_list_tells_the_assistant_how_to_reach_it():
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        meta = yaml.safe_load(text[4:].split("\n---\n", 1)[0])
        outside = [n for n in meta["metadata"]["vault-tools"].split() if n in mcp.BY_NAME and n not in mcp_profile.CORE]
        if outside:
            assert "`vault_tools`" in text and "look it up by that name" in text, f"{path.parent.name} names {outside[:3]}... with no way to reach them"


# The tools a skill names that a compact client can only get through the web app (they delete or revoke): the skills say so.
WEB_APP_ONLY = {"delete_deck", "import_collection_csv", "reset_collection", "undo_collection_reset", "delete_bucket",
                "delete_tag", "untag_cards", "stop_sharing"}


def test_every_tool_a_skill_names_is_listed_or_reachable_or_a_known_web_app_job():
    unreachable = set()
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        meta = yaml.safe_load(text[4:].split("\n---\n", 1)[0])
        stuck = {n for n in meta["metadata"]["vault-tools"].split()
                 if n in mcp.BY_NAME and n not in mcp_profile.CORE and mcp.BY_NAME[n].destructive}
        for name in sorted(stuck):  # a sentence of its own, naming the tool, not only the generic paragraph
            paragraphs = [p for p in text.split("\n\n") if f"`{name}`" in p and "web app" in p and "compact" in p]
            assert paragraphs, f"{path.parent.name} names {name}, which a compact client does in the web app: say so next to it"
        unreachable |= stuck
    assert unreachable <= WEB_APP_ONLY, f"new tools only the web app can run for a compact client: {sorted(unreachable - WEB_APP_ONLY)}"


# ---- the session id --------------------------------------------------------------------------------------------------------


def test_the_session_id_carries_the_profile_and_older_ids_still_read():
    ui_only, both, plain = mcp_session.issue(SECRET, True), mcp_session.issue(SECRET, True, True), mcp_session.issue(SECRET, False, True)
    assert mcp_session.read(SECRET, ui_only) is True and mcp_session.compact(SECRET, ui_only) is False
    assert mcp_session.read(SECRET, both) is True and mcp_session.compact(SECRET, both) is True
    assert mcp_session.read(SECRET, plain) is False and mcp_session.compact(SECRET, plain) is True
    assert mcp_session.compact("another-secret", both) is False  # not this server's id: unknown, so the full list
    flipped = both[:-1] + ("1" if both[-1] == "0" else "0")  # a different last digit of the signature, whatever it was
    assert mcp_session.compact(SECRET, flipped) is False and mcp_session.compact(SECRET, None) is False
    assert mcp_session.compact(SECRET, "v1.2.ab.é") is False and mcp_session.read(SECRET, "v1.2.ab.é") is None
