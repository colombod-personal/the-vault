"""ChatGPT as the real thing behaves (twins/mcp_client.py ChatGptClient, #231): a private_key_jwt client that sends every
token request unsigned first and signed second, scans the tools once when the app is added, and flags steering
descriptions. These tests failed to exist on 2026-10-06, so every OAuth test passed while ChatGPT could not connect."""

import pytest
from fastapi.testclient import TestClient

from tests.test_mcp_oauth import app, settings, universe  # noqa: F401 - fixtures
from twins.mcp_client import STEERING, ChatGptClient
from vault.api import mcp


@pytest.fixture
def chatgpt(app, universe):
    return ChatGptClient(lambda: TestClient(app), universe.client_hosts)


def test_chatgpt_connects_and_refreshes_the_way_it_really_does(chatgpt):
    tokens = chatgpt.connect(write=True)
    assert chatgpt.attempts == [(False, 401), (True, 200)]  # unsigned and refused, then signed and accepted
    assert chatgpt.mcp("tools/list").status_code == 200
    again = chatgpt.refresh()
    assert again.status_code == 200 and again.json()["access_token"] != tokens["access_token"]
    assert chatgpt.attempts[2:] == [(False, 401), (True, 200)]  # refresh is the same two steps


def test_chatgpts_client_document_is_what_chatgpt_serves(chatgpt, universe):
    doc = universe.client_hosts.documents[("chatgpt.com", "/oauth/client.json")]
    assert doc["token_endpoint_auth_method"] == "private_key_jwt" and doc["token_endpoint_auth_signing_alg"] == "RS256"
    assert doc["jwks_uri"] == "https://chatgpt.com/oauth/jwks.json" and doc["client_id"] == "https://chatgpt.com/oauth/client.json"
    assert universe.client_hosts.documents[("chatgpt.com", "/oauth/jwks.json")]["keys"][0]["kid"] == "chatgpt-1"


def test_adding_the_app_scans_every_tool_once_and_none_draws_a_warning(chatgpt):
    tools = chatgpt.add_app(write=True)
    assert len(tools) == len([t for t in mcp.TOOLS]) and {"council_brief", "update_owned_cards"} <= {t["name"] for t in tools}
    assert chatgpt.flagged() == {}, chatgpt.flagged()


def test_the_scan_notices_a_steering_description_and_misses_later_changes(chatgpt, monkeypatch):
    assert STEERING.search("Call again with confirm true only after they say yes.")
    chatgpt.add_app(write=True)
    scanned = len(chatgpt.cached_tools)
    later = mcp.Tool("a_later_tool", "Added after the app was added.", path=lambda a: "/api/v1")
    monkeypatch.setattr(mcp, "TOOLS", [*mcp.TOOLS, later])
    live = chatgpt.mcp("tools/list").json()["result"]["tools"]
    assert len(live) == scanned + 1 and len(chatgpt.cached_tools) == scanned  # ChatGPT keeps what it scanned
    assert "a_later_tool" not in {t["name"] for t in chatgpt.cached_tools}
