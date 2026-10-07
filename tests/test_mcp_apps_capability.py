"""#50, #51: the server checks the MCP Apps capability (``io.modelcontextprotocol/ui``) a client advertises at ``initialize``.

The server is stateless, so what the client said comes back in a signed ``Mcp-Session-Id`` (vault/api/mcp_session.py). A client
that said it cannot show MCP Apps gets a tool list without ``_meta.ui``; one that can, or one we know nothing about (no session id,
an id that does not verify), gets the view links exactly as before, so a host that renders them today keeps doing so."""

import logging

import pytest
from fastapi.testclient import TestClient

from tests.test_mcp_oauth import app, settings, universe  # noqa: F401 - fixtures
from twins.mcp_client import APPS_CAPABILITIES, SESSION_HEADER, McpClient
from vault.api import mcp, mcp_session

VIEW_TOOLS = {t.name for t in mcp.TOOLS if t.ui and not t.write}  # this client connects with read access: write tools are not listed
SECRET = "a-test-secret"


@pytest.fixture
def host(app, universe):
    """An MCP client (the twin) connected through OAuth, as claude.ai and ChatGPT connect."""
    client = McpClient(lambda: TestClient(app), universe.client_hosts.publish())
    client.connect(write=True)
    return client


def listed(client: McpClient) -> dict:
    return {t["name"]: t for t in client.mcp("tools/list").json()["result"]["tools"]}


def test_a_host_that_advertises_mcp_apps_is_given_the_view_links(host):
    host.initialize(APPS_CAPABILITIES, name="apps-host")
    assert host.session_id and host.session_id.startswith("v1.1.")
    tools = listed(host)
    assert {n for n, t in tools.items() if "_meta" in t} == VIEW_TOOLS and len(VIEW_TOOLS) >= 7
    assert tools["find_combos"]["_meta"]["ui"]["resourceUri"] == "ui://vault/combos"


def test_a_host_that_does_not_advertise_it_gets_every_tool_without_view_links_and_the_same_text_answers(host):
    host.initialize({}, name="plain-host")
    assert host.session_id.startswith("v1.0.")
    tools = listed(host)
    assert not [n for n, t in tools.items() if "_meta" in t]
    assert set(tools) == {t.name for t in mcp.TOOLS if not t.write}  # the tools themselves are all there
    call = host.tool("whoami")
    assert call["result"]["isError"] is False and call["result"]["content"][0]["text"]  # tool calls are untouched


def test_a_client_with_no_session_id_gets_the_view_links_as_before(host):
    """claude.ai and ChatGPT render the views today. A client that sends no session id (stateless, or connected before this
    existed) is not guessed about: its tool list is what it always was."""
    assert host.session_id is None
    assert {n for n, t in listed(host).items() if "_meta" in t} == VIEW_TOOLS
    host.initialize({})  # said it cannot...
    host.session_id = None  # ...but a client that does not send the id back is unknown to us
    assert {n for n, t in listed(host).items() if "_meta" in t} == VIEW_TOOLS


@pytest.mark.parametrize("forged", ["v1.0.0123456789abcdef.00000000000000000000000000000000", "v1.1.x", "garbage", "v2.0.a.b", "", "v1.0." + "a" * 200])
def test_an_id_the_server_did_not_sign_is_ignored_not_trusted_and_not_an_error(host, forged):
    host.session_id = forged
    res = host.mcp("tools/list")
    assert res.status_code == 200
    assert {t["name"] for t in res.json()["result"]["tools"] if "_meta" in t} == VIEW_TOOLS  # unknown: as before


def test_an_id_signed_under_another_secret_is_unknown():
    other = mcp_session.issue("another-secret", ui=False)
    assert mcp_session.read(SECRET, other) is None
    assert mcp_session.read("another-secret", other) is False
    mine = mcp_session.issue(SECRET, ui=True)
    assert mcp_session.read(SECRET, mine) is True and mcp_session.read(SECRET, mine + "0") is None
    flipped = mine.replace("v1.1.", "v1.0.", 1)
    assert mcp_session.read(SECRET, flipped) is None  # the one bit cannot be flipped without the secret
    assert mine != mcp_session.issue(SECRET, ui=True)  # ids are not guessable from one another


@pytest.mark.parametrize("capabilities, expected", [
    (APPS_CAPABILITIES, True),
    ({"extensions": {"io.modelcontextprotocol/ui": {}}}, True),  # no mimeTypes given: the extension is named, so it is advertised
    ({"experimental": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}}, True),  # an early draft's place
    ({"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html"]}}}, False),  # cannot show our page type
    ({"extensions": {"io.modelcontextprotocol/ui": True}}, False),
    ({"extensions": {"other/thing": {}}}, False), ({"extensions": "x"}, False), ({}, False), (None, False), ("ui", False), ([], False)])
def test_what_counts_as_advertising_the_extension(capabilities, expected):
    assert mcp_session.advertises_ui(capabilities) is expected


def test_initialize_with_odd_capabilities_still_answers(host):
    for odd in ("a string", ["list"], 5, None):
        res = host.mcp("initialize", {"protocolVersion": "2025-06-18", "capabilities": odd, "clientInfo": "not a dict"})
        assert res.status_code == 200 and res.json()["result"]["serverInfo"]["name"] == "the-vault"
        assert res.headers[SESSION_HEADER.lower()].startswith("v1.0.")


def test_a_batch_that_contains_initialize_also_returns_the_session_id(host):
    body = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": APPS_CAPABILITIES}}]
    res = host.api.post("/api/mcp", json=body, headers={"Authorization": f"Bearer {host.tokens['access_token']}"})
    assert res.status_code == 200 and res.headers[SESSION_HEADER.lower()].startswith("v1.1.")


def test_requests_other_than_initialize_do_not_start_a_session(host):
    assert SESSION_HEADER.lower() not in host.mcp("tools/list").headers
    assert SESSION_HEADER.lower() not in host.mcp("ping").headers


def test_the_session_id_carries_no_person_and_no_token(host):
    host.initialize(APPS_CAPABILITIES)
    sid = host.session_id
    assert host.tokens["access_token"] not in sid and "dev@localhost" not in sid and len(sid) < 70
    assert sid.split(".")[0] == "v1" and sid.split(".")[1] in ("0", "1")  # a version, one bit, a random part and a signature, nothing else


def test_the_log_says_which_host_advertised_the_extension_without_naming_a_person(host, caplog):
    with caplog.at_level(logging.INFO, logger="vault.api.mcp"):
        host.initialize(APPS_CAPABILITIES, name="Apps Host\nforged line")
        host.initialize({}, name="plain-host")
    lines = [r.getMessage() for r in caplog.records if "MCP initialize" in r.getMessage()]
    assert lines == ["MCP initialize from Apps Hostforged line/1: MCP Apps extension advertised",
                     "MCP initialize from plain-host/1: MCP Apps extension not advertised"]


def test_a_page_the_host_cannot_show_is_never_the_only_way_to_the_information(host):
    """The text answer is what a host without MCP Apps shows, with or without view links in the list."""
    host.initialize({})
    res = host.tool("whoami")
    assert res["result"]["isError"] is False and res["result"]["content"][0]["type"] == "text"
