"""#153 (and the shared parts of #154 to #156): the one-prompt setup. docs/onboarding.md ("Tests (write first)") is the spec.

- a generated page per host at /setup/<host>.md (scripts/build_plugin.py), with the fixed layout, production address only,
  every step tagged ASSISTANT or PERSON, no secret in chat, nothing installed that is not named;
- the MCP prompt `vault_start` (vault/api/mcp_catalog.py): listed, read-only, names only real read tools;
- the Connect page's Copy setup prompt, and llms.txt, link to every page.
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from fastapi.testclient import TestClient

from tests.test_agents import make_token, rpc
from vault.api import mcp
from vault.api.mcp_catalog import GROUNDING, PROMPTS

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_plugin as bp  # noqa: E402

HOSTS = ["claude", "chatgpt", "codex", "copilot"]
HEADINGS = ["What this does", "Before you start", "Do this", "Check it worked", "If it fails", "Then", "Never"]
# the surfaces named in #153 (Claude Code, claude.ai and Desktop), #154 (ChatGPT web and desktop), #155 (Codex CLI, IDE, cloud)
# and #156 (GitHub Copilot desktop app, VS Code, CLI): the page each lives on, and the words of its row in the design's host table
ISSUE_SURFACES = {
    "Claude Code": ("claude", "Claude Code"),
    "claude.ai and Claude Desktop": ("claude", "claude.ai, Claude Desktop"),
    "ChatGPT (web)": ("chatgpt", "ChatGPT (web)"),
    "ChatGPT desktop app": ("chatgpt", "ChatGPT desktop app"),
    "Codex CLI": ("codex", "Codex (CLI and IDE extension"),
    "Codex IDE extension": ("codex", "Codex (CLI and IDE extension"),
    "Codex cloud": ("codex", "Codex cloud"),
    "GitHub Copilot desktop app": ("copilot", "GitHub Copilot desktop app"),
    "GitHub Copilot CLI": ("copilot", "GitHub Copilot CLI"),
    "VS Code (GitHub Copilot)": ("copilot", "VS Code (GitHub Copilot)"),
}
DOC_HOSTS = {"claude.ai", "code.claude.com", "support.claude.com", "claude.com", "learn.chatgpt.com", "help.openai.com",
             "docs.github.com", "code.visualstudio.com", "github.com"}
# the only things a page may tell anyone to install: the Vault's plugin and skills, from this repository
ALLOWED_INSTALLS = {"claude plugin marketplace add colombod-personal/the-vault", "claude plugin install the-vault@the-vault",
                    "npx skills add colombod-personal/the-vault"}
INSTALLERS = re.compile(r"\b(npx|npm|pnpm|yarn|pip3?|pipx|uv|brew|apt(-get)?|winget|choco|curl|wget|iwr|sudo|git clone|"
                        r"claude plugin|codex plugin|code --install-extension)\b")


def page(host: str) -> str:
    return (ROOT / "public" / "setup" / f"{host}.md").read_text(encoding="utf-8")


def sections(text: str) -> dict[str, str]:
    """The ``## `` sections of a page, by heading, in order."""
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def steps(text: str) -> list[str]:
    return [l for l in text.splitlines() if re.match(r"\d+\. ", l)]


def outside_never(text: str) -> str:
    return text.split("\n## Never", 1)[0]


# ---- the host table and the pages -----------------------------------------------------------------------------------


def test_the_host_table_has_a_row_for_every_surface_the_four_issues_name():
    doc = (ROOT / "docs" / "onboarding.md").read_text(encoding="utf-8")
    table = doc.split("## What each host can do", 1)[1].split("**Deep links", 1)[0]
    rows = [l for l in table.splitlines() if l.startswith("| ") and not l.startswith("| Host") and not l.startswith("|---")]
    for surface, (_, words) in ISSUE_SURFACES.items():
        assert any(r.split("|")[1].strip().startswith(words) or words in r.split("|")[1] for r in rows), f"no row for {surface}"


def test_every_surface_has_a_section_on_its_hosts_page_and_the_build_knows_it():
    assert {s["name"] for s in bp.SURFACES} == set(ISSUE_SURFACES)
    for surface in bp.SURFACES:
        assert surface["page"] == ISSUE_SURFACES[surface["name"]][0]
        assert f"### {surface['name']}" in page(surface["page"]), surface["name"]
    assert [h["id"] for h in bp.SETUP_HOSTS] == HOSTS


@pytest.mark.parametrize("host", HOSTS)
def test_a_setup_page_exists_for_each_host_and_is_up_to_date(host):
    assert (ROOT / "public" / "setup" / f"{host}.md").is_file()
    assert bp.setup_page(host) == page(host).replace("\r\n", "\n")
    assert [p for p in bp.stale() if "setup" in p] == []


@pytest.mark.parametrize("host", HOSTS)
def test_the_page_has_the_fixed_headings_in_order_and_ends_with_the_safety_rules(host):
    text = page(host)
    assert text.startswith("# Set up The Vault in ")
    assert list(sections(text)) == HEADINGS
    assert text.rstrip().endswith(bp.SETUP_NEVER[-1])
    # one safety text everywhere: the page cannot weaken it for one host
    assert all(f"- {rule}" in sections(text)["Never"] for rule in bp.SETUP_NEVER)


def test_the_safety_section_is_the_same_on_every_page():
    nevers = {sections(page(h))["Never"].strip() for h in HOSTS}
    assert len(nevers) == 1
    never = nevers.pop().lower()
    for needle in ("token", "password", "disable", "install", "only this page", "write"):
        assert needle in never, needle


@pytest.mark.parametrize("host", HOSTS)
def test_every_step_is_tagged_assistant_or_person(host):
    secs = sections(page(host))
    for name in ("Do this", "Check it worked", "Then"):
        listed = steps(secs[name])
        assert listed, f"{host}: no steps under {name}"
        for line in listed:
            assert re.match(r"\d+\. (ASSISTANT|PERSON): \S", line), f"{host}: untagged step: {line}"
    assert any("PERSON:" in l for l in steps(secs["Do this"]))  # someone has to sign in
    assert any("ASSISTANT: call `whoami`" in l for l in steps(secs["Check it worked"]))


@pytest.mark.parametrize("host", HOSTS)
def test_a_page_names_only_the_production_address_and_known_documentation_hosts(host):
    text = page(host)
    assert "vercel.app" not in text and "localhost" not in text and "127.0.0.1" not in text and "example.com" not in text
    hosts = {urlparse(u.rstrip(".,;:")).hostname for u in re.findall(r"https?://[^\s)`>\"'|]+", text)}
    assert bp.HOST.removeprefix("https://") in hosts
    assert hosts <= DOC_HOSTS | {"mtgvault.cards"}, hosts - DOC_HOSTS
    for url in re.findall(r"https?://[^\s)`>\"'|]*/api/mcp\b", text):
        assert url == f"{bp.HOST}/api/mcp"


@pytest.mark.parametrize("host", HOSTS)
def test_the_failure_table_has_the_rows_the_design_asks_for(host):
    table = [l for l in sections(page(host))["If it fails"].splitlines() if l.startswith("|")]
    assert table[0].replace(" ", "") == "|Symptom|Likelycause|Whattodo|" and set(table[1]) <= set("|- ")
    body = "\n".join(table[2:]).lower()
    for needle in ("not found", "authentication", "no cards", "whoami fails", "no write tools", "already", "wrong address"):
        assert needle in body, (host, needle)
    assert all(len(r.strip("|").split("|")) == 3 for r in table)


@pytest.mark.parametrize("host", HOSTS)
def test_nothing_asks_for_a_secret_in_chat_disables_a_check_or_installs_something_unnamed(host):
    text = outside_never(page(host))
    assert "vault_pat_" not in page(host) and "Bearer" not in page(host) and "--header" not in page(host)
    for bad in ("--insecure", "--no-verify", "NODE_TLS_REJECT_UNAUTHORIZED", "--dangerously", "--yolo", "--allow-all", "sandbox_mode",
                "verify=False", "| sh", "| bash", "disable the", "turn off", "bypass"):
        assert bad not in text, bad
    for secret in ("token", "password", "secret", "api key", "passcode"):
        for line in text.splitlines():
            if secret in line.lower():
                # only a negation, or the Claude Code plugin's own hidden field, may mention one
                assert re.search(r"\bno\b|\bnot\b|never|without|plugin's own", line.lower()), f"{host}: {line}"
    # every install command is one of the named ones; no other installer appears anywhere
    commands = re.findall(r"`([^`]+)`", text)
    for cmd in commands:
        if INSTALLERS.search(cmd):
            assert cmd in ALLOWED_INSTALLS, f"{host}: unnamed install: {cmd}"
    for line in text.splitlines():
        if INSTALLERS.search(line) and not line.startswith("|"):
            assert any(c in line for c in ALLOWED_INSTALLS) or "`" not in line, f"{host}: {line}"


def test_a_token_is_never_offered_except_in_the_claude_code_plugins_own_field():
    for host in HOSTS:
        lines = [l for l in outside_never(page(host)).splitlines() if "token" in l.lower()]
        if host == "claude":
            assert lines and all("plugin's own" in l for l in lines), lines
        else:
            assert lines == [], (host, lines)


def test_what_the_design_table_calls_unverified_is_said_to_be_unverified_on_the_page():
    """docs/onboarding.md: 'where a cell is unverified say not verified yet' - the page never claims what only the docs say."""
    chatgpt, codex, copilot, claude = (page(h) for h in ("chatgpt", "codex", "copilot", "claude"))
    assert chatgpt.count("not verified yet") >= 3  # plans, the menu path, the desktop app
    assert "Codex cloud" in codex and "not verified yet" in codex.split("### Codex cloud", 1)[1].split("\n## ", 1)[0]
    assert "not verified yet" in copilot.split("### GitHub Copilot desktop app", 1)[1].split("\n### ", 1)[0]
    assert "not verified yet" in claude.split("### claude.ai and Claude Desktop", 1)[1]  # the prefilled link has not run on a real account
    for host in HOSTS:
        assert "not run in" in page(host)  # the page says it was written from the host's documentation and has not been run there yet


def test_the_commands_on_the_pages_are_the_ones_the_connect_page_is_generated_from():
    """One source (HARNESSES): the commands and files a page gives cannot drift from the Connect page's blocks."""
    def code(hid, kind, n=0):
        return [s for s in next(h for h in bp.HARNESSES if h["id"] == hid)["steps"] if s["kind"] == kind][n]["code"]
    claude, codex, copilot = page("claude"), page("codex"), page("copilot")
    add, login = code("claude-code", "oauth").split("\n")
    assert f"`{add}`" in claude and f"`{login}`" in claude
    add, login = code("codex", "oauth").split("\n")
    assert f"`{add}`" in codex and f"`{login}`" in codex and "`codex mcp list`" in codex
    assert f"`{code('copilot-cli', 'config')}`" in copilot
    def flat(text):  # a file's lines are indented under their step: compare them without indentation
        return "\n".join(line.strip() for line in text.split("\n"))
    assert flat(code("vscode", "oauth")) in flat(copilot) and flat(code("copilot-cli", "config", 1)) in flat(copilot)
    assert all(f"`{c}`" in claude for c in ("claude plugin marketplace add colombod-personal/the-vault", "claude plugin install the-vault@the-vault"))


def test_the_prefilled_links_are_built_the_way_the_hosts_document_them():
    link = bp.claude_connector_link()
    assert link == ("https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=The%20Vault"
                    "&connectorUrl=https%3A%2F%2Fmtgvault.cards%2Fapi%2Fmcp")
    assert link in page("claude")
    vs = bp.vscode_install_link()
    assert vs.startswith("vscode:mcp/install?")
    assert json.loads(unquote(vs.split("?", 1)[1])) == {"name": "vault", "type": "http", "url": f"{bp.HOST}/api/mcp"}
    assert vs in page("copilot")


def test_without_oauth_only_the_claude_code_plugin_is_offered_and_the_others_say_coming_soon(monkeypatch):
    """Decision 2 in docs/onboarding.md: until sign-in is switched on (#48), the token path is the Claude Code plugin's field
    only, and every other host says 'coming soon'."""
    monkeypatch.setattr(bp, "OAUTH_READY", False)
    for host in ("chatgpt", "codex", "copilot"):
        text = bp.setup_page(host)
        assert "Coming soon" in text and "codex mcp add" not in text and "copilot mcp add" not in text and "/mcp auth" not in text
        assert not steps(sections(text)["Do this"])
        assert list(sections(text)) == HEADINGS and "token" not in outside_never(text).lower().replace("no token", "")
    claude = bp.setup_page("claude")
    assert "claude mcp add" not in claude and "claude plugin install the-vault@the-vault" in claude and "plugin's own" in claude
    assert list(sections(claude)) == HEADINGS
    for name in ("Do this", "Check it worked", "Then"):
        assert all(re.match(r"\d+\. (ASSISTANT|PERSON): ", l) for l in steps(sections(claude)[name]))


def test_a_page_that_names_another_host_fails_the_build_check(monkeypatch):
    monkeypatch.setattr(bp, "HOST", "https://the-vault-abc.vercel.app")
    stale = bp.stale()
    assert any("setup" in p for p in stale)


# ---- served ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_the_page_is_served_as_markdown(settings, host):
    from vault.app import create_app

    app = create_app(settings, serve_static=True)
    try:
        with TestClient(app) as c:
            res = c.get(f"/setup/{host}.md")
            assert res.status_code == 200 and res.headers["content-type"].startswith("text/markdown")
            assert res.text.replace("\r\n", "\n") == page(host).replace("\r\n", "\n")
            assert "etag" in res.headers  # cacheable: a static file, the same for everyone
    finally:
        app.state.db.engine.dispose()


def test_vercel_serves_the_pages_as_markdown_and_lets_them_be_cached():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    rule = next(h for h in config["headers"] if h["source"].startswith("/setup/"))
    headers = {h["key"]: h["value"] for h in rule["headers"]}
    assert headers["Content-Type"].startswith("text/markdown") and "max-age=" in headers["Cache-Control"]
    assert "private" not in headers["Cache-Control"] and "no-store" not in headers["Cache-Control"]


# ---- links ----------------------------------------------------------------------------------------------------------


def test_llms_txt_and_the_connect_page_link_to_every_setup_page_and_name_the_prompt():
    llms = (ROOT / "public" / "llms.txt").read_text(encoding="utf-8")
    connect = (ROOT / "public" / "connect.html").read_text(encoding="utf-8")
    for host in HOSTS:
        assert f"{bp.HOST}/setup/{host}.md" in llms
        assert f'href="/setup/{host}.md"' in connect
    assert "`vault_start`" in llms and "vault_start" in connect


@pytest.mark.parametrize("host", HOSTS)
def test_the_connect_pages_copy_setup_prompt_names_the_right_page_and_the_production_address(host):
    connect = (ROOT / "public" / "connect.html").read_text(encoding="utf-8")
    card = connect.split(f'id="setup-{host}"', 1)[1].split("</section>", 1)[0]
    assert "Copy setup prompt" in card and "<button" in card
    prompt = bp.setup_prompt(host)
    assert prompt in card.replace("&amp;", "&")
    assert prompt == f"Set up The Vault for me. Follow only this page: {bp.HOST}/setup/{host}.md"
    assert f"/setup/{host}.md" in prompt and all(f"/setup/{o}.md" not in prompt for o in HOSTS if o != host)
    assert not re.search(r"https?://[\w.-]+\.vercel\.app", connect) and "vercel" not in card.lower()


def test_the_claude_card_has_the_prefilled_add_connector_link():
    connect = (ROOT / "public" / "connect.html").read_text(encoding="utf-8")
    card = connect.split('id="claude"', 1)[1].split("</div>", 1)[0]
    assert bp.claude_connector_link().replace("&", "&amp;") in card
    vscode = connect.split('id="vscode"', 1)[1].split("</div>", 1)[0]
    assert bp.vscode_install_link().replace("&", "&amp;") in vscode


# ---- the MCP prompt vault_start -------------------------------------------------------------------------------------

READ_TOOLS_IT_USES = ["whoami", "get_collection_summary", "search_cards", "list_decks", "get_deck", "check_decklist", "get_archidekt_deck",
                      "find_rules_term", "search_rules", "get_rule", "verify_citation"]


def start_prompt() -> dict:
    return next(p for p in PROMPTS if p["name"] == "vault_start")


def test_vault_start_names_every_tool_it_uses_and_each_is_a_real_read_tool():
    text = start_prompt()["text"]
    for name in READ_TOOLS_IT_USES:
        assert f"`{name}`" in text, name
        assert name in mcp.BY_NAME and not mcp.BY_NAME[name].write, name
    named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+|whoami)`", text))
    assert named <= set(mcp.BY_NAME), named - set(mcp.BY_NAME)  # a renamed tool breaks the build
    assert not [t.name for t in mcp.TOOLS if t.write and t.name in text]  # it never names a write tool
    assert not [t.name for t in mcp.TOOLS if t.write and re.search(rf"\b{t.name}\b", text)]


def test_vault_start_contract_is_read_only_and_has_both_empty_data_branches():
    text = start_prompt()["text"]
    lower = text.lower()
    assert "never write" in lower or "do not save, import, edit or delete" in lower
    assert "no collection" in lower and "how to import" in lower and "stop" in lower  # nothing imported: explain, then stop
    assert "no decks" in lower and "continue" in lower and "nothing is saved" in lower  # no decks: read-only options, keep going
    assert "write access" in lower  # saving needs the write grant, described not performed
    assert "three next steps" in lower and "most valuable" in lower and "citation" in lower
    assert GROUNDING in text  # grounded like the other prompts


def test_vault_start_is_listed_and_a_read_only_token_gets_it(signed_in, app):
    read = make_token(signed_in, scopes=("read",))
    with TestClient(app) as bot:
        init = rpc(bot, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}, read).json()["result"]
        assert init["capabilities"]["prompts"] == {"listChanged": False}
        listed = rpc(bot, "prompts/list", token=read).json()["result"]["prompts"]
        entry = next(p for p in listed if p["name"] == "vault_start")
        assert entry["arguments"] == [] and "text" not in entry and entry["title"] and entry["description"]
        got = rpc(bot, "prompts/get", {"name": "vault_start"}, read).json()["result"]
        assert got["messages"][0]["role"] == "user"
        text = got["messages"][0]["content"]["text"]
        assert text.startswith("Start here") and "whoami" in text and "(not given)" not in text
        tools = {t["name"] for t in rpc(bot, "tools/list", token=read).json()["result"]["tools"]}
        assert set(READ_TOOLS_IT_USES) <= tools  # a read-only caller can do everything the tour asks
        assert not [n for n in mcp.BY_NAME if mcp.BY_NAME[n].write and n in tools]


def test_the_setup_pages_quote_the_tour_for_hosts_that_do_not_list_prompts():
    from vault.api.mcp_catalog import START_TOUR

    for host in HOSTS:
        then = sections(page(host))["Then"]
        assert "`vault_start`" in then
        assert all(f"> {line}".rstrip() in then for line in START_TOUR.splitlines() if line.strip()), host


def test_the_catalog_docs_list_the_prompt():
    assert "`vault_start`" in (ROOT / "docs" / "agents.md").read_text(encoding="utf-8")
    assert "`vault_start`" in (ROOT / "public" / "llms.txt").read_text(encoding="utf-8")
