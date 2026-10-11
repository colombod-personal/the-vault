"""MCP Apps views: tools point at ui:// pages, the pages are served as resources with the right type and
security metadata, they only ever insert text (never HTML), load nothing but Scryfall's card image,
and carry the provenance footer. Hosts without MCP Apps keep the normal text answer."""

import re
import shutil
import subprocess
from urllib.parse import urlparse

import pytest

from tests.test_agents import call_tool, make_token, rpc
from tests.test_catalog_api import BOLT
from tests.test_mcp_catalog import agent, bot  # noqa: F401  (fixtures)
from vault.api import mcp, mcp_ui

VIEWS = {"card": ("get_card_oracle",), "deck": ("deck_stats",), "combos": ("find_combos",), "upgrades": ("find_upgrades",), "steps": ("present_steps",),
         "shopping": ("shopping_list",), "printings": ("update_owned_cards", "show_owned_printings")}


def test_each_view_belongs_to_its_tools_and_every_view_is_used():
    by_tool = {t.name: t.ui for t in mcp.TOOLS if t.ui}
    assert by_tool == {tool: view for view, tools in VIEWS.items() for tool in tools}
    assert set(VIEWS) == set(mcp_ui.VIEWS)


def test_tools_list_names_the_view_only_for_tools_that_have_one(agent, bot):
    read = make_token(agent, scopes=["read", "write"])  # a write token lists the write tools too (update_owned_cards)
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}
    for view, names in VIEWS.items():
        for tool in names:
            assert tools[tool]["_meta"] == {"ui": {"resourceUri": mcp_ui.uri(view), "visibility": ["model", "app"]}}
    assert "_meta" not in tools["whoami"] and "_meta" not in tools["search_cards"]


def test_views_are_listed_and_read_as_mcp_app_html_with_csp(agent, bot):
    read = make_token(agent)
    listed = rpc(bot, "resources/list", token=read).json()["result"]["resources"]
    assert {r["uri"] for r in listed} == {mcp_ui.uri(v) for v in VIEWS} and all(r["mimeType"] == "text/html;profile=mcp-app" for r in listed)
    card = rpc(bot, "resources/read", {"uri": mcp_ui.uri("card")}, read).json()["result"]["contents"][0]
    assert card["mimeType"] == "text/html;profile=mcp-app" and card["text"].startswith("<!doctype html>")
    assert card["_meta"]["ui"]["csp"] == {"resourceDomains": ["https://cards.scryfall.io"]}  # the card image, nothing else
    assert card["_meta"]["openai/widgetCSP"] == {"connect_domains": [], "resource_domains": ["https://cards.scryfall.io"]}  # ChatGPT's key, same domains
    for entry in listed:  # the policy is on the listing too, the same as on the page itself
        page = rpc(bot, "resources/read", {"uri": entry["uri"]}, read).json()["result"]["contents"][0]
        assert entry["_meta"] == page["_meta"], entry["uri"]
    deck = rpc(bot, "resources/read", {"uri": mcp_ui.uri("deck")}, read).json()["result"]["contents"][0]
    assert deck["_meta"]["ui"]["csp"] == {}  # no network at all
    gone = rpc(bot, "resources/read", {"uri": "ui://vault/nope"}, read).json()
    assert gone["error"]["code"] == -32002
    assert rpc(bot, "resources/read", {"uri": "ui://vault/card-zzzzzzzz"}, read).json()["error"]["code"] == -32002  # a digest is eight hex digits
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


def test_printing_pictures_come_only_from_scryfall_with_the_artist_and_the_view_never_applies_a_change():
    page = mcp_ui.html("printings")
    assert {v for v, spec in mcp_ui.VIEWS.items() if spec.get("images")} == {"card", "printings"}
    assert "cards\\.scryfall\\.io" in page and "Illustrated by" in page and "Image: Scryfall" in page
    assert "confirm_owned_cards_update" not in page and "undo_owned_cards_update" not in page  # a yes in the chat applies, never the view
    assert '"ui/message"' in page and 'callTool("update_owned_cards"' in page  # the choice goes to the chat; else a preview only


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


@pytest.mark.parametrize("view", ["deck", "upgrades", "shopping"])
def test_deck_panels_lead_with_the_deck_not_with_card_counts(view):
    """#216: the panel opens with the deck's name, format and commander(s) (deckHeader), before its own statistics."""
    page = mcp_ui.html(view)
    assert "deckHeader(" in page and "var head = deckHeader(env.deck)" in page
    body = page[page.index("function render("):]  # (helpers defined above it also draw headings; the order that matters is render's own)
    assert body.index("deckHeader(env.deck)") < body.index('h("h2"')  # the deck comes before the panel's own title
    assert "Commander: " in page and "Format not given" in page and "Format read from the list" in page


def test_the_shopping_view_prints_the_stores_note_once_not_again_among_the_notes():
    """#317: the answer's notes repeat the store's own note, which the view already prints under the list to paste."""
    page = mcp_ui.html("shopping")
    assert "storeLimits.indexOf(n) < 0" in page
    # the notes that are not the store's note are still printed
    assert page.count('(r.notes || []).filter(function (n) { return storeLimits.indexOf(n) < 0; })') == 1


def test_the_shopping_view_names_the_store_and_what_its_paste_cannot_carry_and_switches_between_formats():
    """#55: store formats in the view - the heading names the store, its limits are shown, and `format: all` offers each text."""
    page = mcp_ui.html("shopping")
    assert "r.store_format" in page and "one.store" in page and ".limits" in page
    assert 'var several = r.texts' in page and "limits.textContent" in page  # `all`: one entry per store, limits follow the choice


def test_the_deck_view_offers_every_format_the_server_checks():
    """#52: it offered 15 of the 20; the list is now the server's own (vault.deck_tools.FORMATS), not a copy that can drift."""
    from vault.deck_tools import FORMATS

    page = mcp_ui.html("deck")
    assert "__FORMATS__" not in page and f"var FORMATS = {__import__('json').dumps(list(FORMATS))};" in page and len(FORMATS) == 20


def test_the_combos_view_is_for_find_combos_and_allows_no_network_at_all(agent, bot):
    assert mcp.BY_NAME["find_combos"].ui == "combos"
    read = make_token(agent)
    page = rpc(bot, "resources/read", {"uri": mcp_ui.uri("combos")}, read).json()["result"]["contents"][0]
    assert page["_meta"]["ui"]["csp"] == {} and "Commander Spellbook" in page["text"] and "<img" not in page["text"]


def test_the_export_is_a_local_blob_with_a_text_fallback_and_the_csp_is_unchanged(agent, bot):
    """#55: the shopping view saves the list through a Blob link made in the page; it loads nothing, and the policy stays what it was."""
    page = mcp_ui.html("shopping")
    assert "new Blob(" in page and "URL.createObjectURL(" in page and "blocks downloads: use Copy list" in page
    csp = {v: mcp_ui.resource_meta(v)["ui"]["csp"] for v in mcp_ui.VIEWS}
    assert csp == {v: ({"resourceDomains": ["https://cards.scryfall.io"]} if v in ("card", "printings") else {}) for v in mcp_ui.VIEWS}
    legacy = {v: mcp_ui.resource_meta(v)["openai/widgetCSP"] for v in mcp_ui.VIEWS}  # ChatGPT's key, same domains
    assert legacy == {v: {"connect_domains": [], "resource_domains": ["https://cards.scryfall.io"] if v in ("card", "printings") else []} for v in mcp_ui.VIEWS}


def hosts_named_in(page: str) -> set[str]:
    """Every host a page names in an address, including the ones written inside a regular expression (the views limit where a
    picture may come from with `/^https:\\/\\/cards\\.scryfall\\.io\\//`): the hosts the page can ask the browser for."""
    return {found.replace("\\", "") for found in re.findall(r"(?:https?|wss?):\\?/\\?/([A-Za-z0-9.\\-]+)", page)}


def declared_hosts(meta: dict) -> tuple[set[str], set[str]]:
    """The hosts the policy lets a view load from (`resourceDomains`) and talk to (`connectDomains`), from both keys; the two keys
    must say the same, or a host that reads only one of them (ChatGPT's desktop app reads either) would block what the other allows."""
    ui, chatgpt = meta["ui"]["csp"], meta["openai/widgetCSP"]
    domains = [*ui.get("resourceDomains", []), *chatgpt["resource_domains"], *ui.get("connectDomains", []), *chatgpt["connect_domains"]]
    assert all(re.fullmatch(r"https://[a-z0-9.-]+", d) for d in domains), domains  # an origin: no path, no wildcard, no http
    assert sorted(ui.get("resourceDomains", [])) == sorted(chatgpt["resource_domains"]), meta
    assert sorted(ui.get("connectDomains", [])) == sorted(chatgpt["connect_domains"]), meta
    return ({urlparse(d).hostname for d in ui.get("resourceDomains", [])}, {urlparse(d).hostname for d in ui.get("connectDomains", [])})


@pytest.mark.parametrize("view", sorted(VIEWS))
def test_the_domains_a_view_declares_are_exactly_the_hosts_its_page_can_ask_for(view):
    """The ChatGPT desktop app showed grey boxes where card pictures belong (2026-10-10, 2026-10-11) and the cause was hunted in the
    policy by eye. Now the page and the policy are compared by a test: a host the page names that the policy does not declare
    is blocked by the host (the view looks broken), and a declared host the page never names is a wider policy than needed."""
    page = mcp_ui.html(view)
    resources, connects = declared_hosts(mcp_ui.resource_meta(view))
    assert connects == set(), "no view talks to a network: tools are called through the host"
    assert hosts_named_in(page) == resources, f"{view}: the page names {sorted(hosts_named_in(page))}, the policy declares {sorted(resources)}"
    for loads_a_resource in ("@font-face", "@import", "url(", "<link", "<script src", "<iframe", "<object", "<embed", "<video", "<audio", "<source", "srcset", "<picture"):
        assert loads_a_resource not in page, f"{view} loads a resource with {loads_a_resource}: declare its host, or do not"
    assert ('h("img"' in page or "<img" in page) == bool(resources)  # a picture only where a host is declared for it, and then it is drawn
    assert "http://" not in page and "ws://" not in page  # https only (wss:// contains neither)


def test_what_the_hosts_would_see_changes_when_the_page_or_its_policy_does(monkeypatch):
    """OpenAI's rule: the resource URI is a cache key, and a breaking change to the page publishes a new URI. #446 changed the
    policy under the same address. The address now carries a digest of the page and its policy, so that cannot happen again."""
    for view in mcp_ui.VIEWS:
        assert mcp_ui.uri(view) == f"ui://vault/{view}-{mcp_ui.version(view)}" and re.fullmatch(r"[0-9a-f]{8}", mcp_ui.version(view))
        assert mcp_ui.view_of(mcp_ui.uri(view)) == view == mcp_ui.view_of(f"ui://vault/{view}")  # the old address is still served
        assert mcp_ui.view_of(f"ui://vault/{view}-00000000") == view  # and a tool list a host kept from an older release
    assert len({mcp_ui.version(v) for v in mcp_ui.VIEWS}) == len(mcp_ui.VIEWS)
    before = {v: mcp_ui.version.__wrapped__(v) for v in mcp_ui.VIEWS}
    assert before == {v: mcp_ui.version(v) for v in mcp_ui.VIEWS}  # stable from one call to the next
    monkeypatch.setattr(mcp_ui, "SCRYFALL_IMAGES", "https://cards.scryfall.example")  # the policy changes: only the views that use it get a new address
    assert {v for v in mcp_ui.VIEWS if mcp_ui.version.__wrapped__(v) != before[v]} == {"card", "printings"}
    monkeypatch.undo()
    monkeypatch.setattr(mcp_ui, "CSS", mcp_ui.CSS + "\n.x { color: red; }")  # the page changes: every view does
    assert {v for v in mcp_ui.VIEWS if mcp_ui.version.__wrapped__(v) != before[v]} == set(mcp_ui.VIEWS)
