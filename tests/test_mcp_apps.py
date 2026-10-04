"""MCP Apps views: tools point at ui:// pages, the pages are served as resources with the right type and
security metadata, they only ever insert text (never HTML), load nothing but Scryfall's card image,
and carry the provenance footer. Hosts without MCP Apps keep the normal text answer."""

import re
import shutil
import subprocess

import pytest

from tests.test_agents import call_tool, make_token, rpc
from tests.test_catalog_api import BOLT
from tests.test_mcp_catalog import agent, bot  # noqa: F401  (fixtures)
from vault.api import mcp, mcp_ui

VIEWS = {"card": "get_card_oracle", "deck": "deck_stats", "upgrades": "find_upgrades", "steps": "present_steps", "shopping": "shopping_list"}


def test_each_view_belongs_to_one_tool_and_every_view_is_used():
    by_tool = {t.name: t.ui for t in mcp.TOOLS if t.ui}
    assert by_tool == {tool: view for view, tool in VIEWS.items()}
    assert set(VIEWS) == set(mcp_ui.VIEWS)


def test_tools_list_names_the_view_only_for_tools_that_have_one(agent, bot):
    read = make_token(agent)
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}
    for view, tool in VIEWS.items():
        assert tools[tool]["_meta"] == {"ui": {"resourceUri": f"ui://vault/{view}", "visibility": ["model", "app"]}}
    assert "_meta" not in tools["whoami"] and "_meta" not in tools["search_cards"]


def test_views_are_listed_and_read_as_mcp_app_html_with_csp(agent, bot):
    read = make_token(agent)
    listed = rpc(bot, "resources/list", token=read).json()["result"]["resources"]
    assert {r["uri"] for r in listed} == {f"ui://vault/{v}" for v in VIEWS} and all(r["mimeType"] == "text/html;profile=mcp-app" for r in listed)
    card = rpc(bot, "resources/read", {"uri": "ui://vault/card"}, read).json()["result"]["contents"][0]
    assert card["mimeType"] == "text/html;profile=mcp-app" and card["text"].startswith("<!doctype html>")
    assert card["_meta"]["ui"]["csp"] == {"resourceDomains": ["https://cards.scryfall.io"]}  # the card image, nothing else
    deck = rpc(bot, "resources/read", {"uri": "ui://vault/deck"}, read).json()["result"]["contents"][0]
    assert deck["_meta"]["ui"]["csp"] == {}  # no network at all
    gone = rpc(bot, "resources/read", {"uri": "ui://vault/nope"}, read).json()
    assert gone["error"]["code"] == -32002
    assert rpc(bot, "resources/read", {"uri": 5}, read).json()["error"]["code"] == -32002


def test_the_server_declares_resources_and_a_host_without_apps_still_gets_the_text_answer(agent, bot):
    read = make_token(agent)
    init = rpc(bot, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}, read).json()["result"]
    assert "resources" in init["capabilities"]
    result = call_tool(bot, read, "get_card_oracle", name="Lightning Bolt")
    assert result["isError"] is False and "Lightning Bolt deals 3 damage" in result["content"][0]["text"]  # the normal answer, view or not


def test_the_card_answer_has_what_the_view_shows(agent, bot):
    read = make_token(agent)
    body = call_tool(bot, read, "get_card_oracle", name="Lightning Bolt")["structuredContent"]
    assert {"artist", "image_normal"} <= set(body["card"]) and body["rulings_total"] == 30
    assert {b["source"] for b in body["provenance"]} >= {"Scryfall"}


def test_the_walkthrough_attaches_each_rule_and_flags_unknown_ones(agent, bot):
    read = make_token(agent)
    body = call_tool(bot, read, "present_steps", title="Sample", cards=["Lightning Bolt"], steps=[
        {"text": "First thing.", "rules": ["100.1"]}, {"text": "Second thing.", "rules": ["100.1a", "999.9"]}])["structuredContent"]
    assert body["version"] == "2027-03-03" and body["steps"][0]["rules"] == [{"number": "100.1", "text": "First sample rule."}]
    assert [r["number"] for r in body["steps"][1]["rules"]] == ["100.1a"] and body["unknown_rules"] == [{"step": 2, "rule": "999.9"}]
    assert body["provenance"][0]["source"] == "Wizards of the Coast" and "Comprehensive Rules" in body["provenance"][0]["origin"]
    too_many = rpc(bot, "tools/call", {"name": "present_steps", "arguments": {"steps": [{"text": "x"}] * 13}}, read).json()
    assert too_many["error"]["code"] == -32602


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_a_view_only_inserts_text_and_loads_nothing_from_elsewhere(view):
    page = mcp_ui.html(view)
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\"", "XMLHttpRequest",
                   "fetch(", "WebSocket", "importScripts", "localStorage", "sessionStorage", "document.cookie", "window.open", "location."):
        assert banned not in page, f"{view} uses {banned}"
    assert "<script src" not in page and "<link " not in page and "@import" not in page and "url(" not in page
    urls = set(re.findall(r"https?://[^\s\"'<>)]+", page))
    assert urls <= {"https://cards.scryfall.io/"} | {u for u in urls if u.startswith(("https://cards.scryfall.io", "https:\\/\\/"))} or not urls
    assert "ui/initialize" in page and "ui/notifications/initialized" in page and "provenanceFooter(" in page
    assert "not produced or endorsed by Scryfall" in page  # every view says it is not an official product
    assert page.count("postMessage") == 1 and "e.source !== window.parent" in page  # one way out, and only the host is listened to


def test_only_the_card_view_may_load_an_image_and_only_from_scryfall():
    assert "cards\\.scryfall\\.io" in mcp_ui.html("card") and "<img" not in mcp_ui.html("deck")
    assert "artist" in mcp_ui.html("card") and "Illustrated by" in mcp_ui.html("card")  # the artist is always credited with the image
    assert 'ext(b.url, "link")' in mcp_ui.html("steps")  # links go through the host, never straight out


@pytest.mark.skipif(not shutil.which("node"), reason="node is needed to parse the view scripts")
@pytest.mark.parametrize("view", sorted(VIEWS))
def test_view_scripts_are_valid_javascript(view, tmp_path):
    script = re.search(r"<script>(.*)</script>", mcp_ui.html(view), re.S).group(1)
    path = tmp_path / f"{view}.js"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr


def test_the_protocol_version_is_the_stable_one():
    assert mcp_ui.PROTOCOL == "2026-01-26" and mcp_ui.MIME == "text/html;profile=mcp-app"
