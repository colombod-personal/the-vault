"""The landing page for signed-out visitors (#437): the plain-HTML page at / in public/index.html.

Owner, 2026-10-10: "In the landing page when no one is signed in or does not have an account, that page should tell the story: what
the Vault does and how it powers AI assistants to help manage your collection and enjoy using and building decks, using any AI
provider you have." The issue's five blocks are checked here; the list of assistants against docs/ai-integration-testing.md; the
add-by-hand steps against the Connect page they are generated with (scripts/build_plugin.py); and the head script that decides
between the landing page and the app is run in node on every kind of address."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import build_plugin as bp

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


INDEX = text(PUBLIC / "index.html")
CONNECT = text(PUBLIC / "connect.html")
APP = text(PUBLIC / "app.jsx")
CSS = text(PUBLIC / "layout.css")
HEAD = INDEX.split("</head>", 1)[0]
LANDING = INDEX.split('<div id="landing" class="landing">', 1)[1].split('<div id="root"></div>', 1)[0]


def section(element_id: str) -> str:
    return LANDING.split(f'id="{element_id}"', 1)[1].split("</section>", 1)[0]


def words(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


# ---- the five blocks ---------------------------------------------------------------------------------------------------------

def test_the_hero_says_what_it_is_and_offers_sign_in_and_the_demo():
    hero = LANDING.split('class="landing-hero"', 1)[1].split("</section>", 1)[0]
    assert "Your Magic collection, your decks and the rules, for the AI assistant you already use." in hero
    assert re.search(r'<a class="btn primary landing-cta" href="#/signin">Sign in or create an account</a>', hero)
    assert 'href="#demo"' in hero and "Watch the demo" in hero and 'id="demo"' in hero


def test_the_demo_video_loads_nothing_until_the_visitor_presses_play():
    """Vercel data transfer: preload="none" with a poster, no autoplay; the video itself comes with #441, only the poster is ours."""
    video = re.search(r"<video[^>]*>", LANDING).group(0)
    assert 'preload="none"' in video and "autoplay" not in video and "controls" in video and "muted" in video and "playsinline" in video
    poster = re.search(r'poster="/([^"]+)"', video).group(1)
    assert (PUBLIC / poster).is_file() and (PUBLIC / poster).stat().st_size < 120_000
    assert '<source src="/demo/vault-chatgpt-demo.mp4" type="video/mp4" />' in LANDING
    assert "54 seconds, no sound" in LANDING


def test_the_story_is_three_steps_with_real_pictures():
    how = section("how")
    steps = re.findall(r'<li class="landing-step">(.*?)</li>', how, flags=re.S)
    assert [re.search(r"<h3>(.*?)</h3>", s).group(1) for s in steps] == [
        "1. Bring your collection", "2. Connect your assistant", "3. Ask it things"]
    assert "Dragon Shield" in steps[0] and "Moxfield" in steps[0]
    assert bp.MCP_URL in steps[1]
    for src, alt in re.findall(r'<img src="/([^"]+)"[^>]*alt="([^"]+)"', how):
        assert (PUBLIC / src).is_file() and alt and "demo" in alt, src
    for asked in ("legal", "missing", "expert council"):
        assert asked in words(how)


def test_works_with_lists_every_assistant_with_its_honest_status():
    hosts = re.findall(r'<li class="host host-(\w+)"><span class="host-name">(.*?)</span><span class="host-status">(.*?)</span>',
                       section("works-with"))
    assert [(name.replace("&#x27;", "'"), status, label) for status, name, label in hosts] == [
        (a["name"], a["status"], bp.ASSISTANT_STATUS[a["status"]]) for a in bp.landing_assistants()]


def test_the_landing_page_shows_only_apps_tested_for_real_no_developer_tools_or_unsupported_apps():
    """Owner, 2026-10-10: the landing page is for people using apps; only tested ones, no CLI, nothing we do not support."""
    shown = [a["name"] for a in bp.landing_assistants()]
    assert shown == ["Claude (web)", "ChatGPT (web)", "Perplexity (web)"]
    page = text(PUBLIC / "index.html")
    for gone in ("Codex", "Claude Code", "Cursor", "GitHub Copilot", "Microsoft Copilot", "CLI", "not tested yet", "Cannot add"):
        assert gone not in page, gone


def test_the_list_of_assistants_matches_what_is_tested_in_the_testing_doc():
    doc = text(ROOT / "docs" / "ai-integration-testing.md")
    table = doc.split("## Which assistants are tested", 1)[1].split("\n## ", 1)[0]
    rows = [[c.strip() for c in line.strip("|").split("|")] for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    assert rows[0] == ["Assistant", "Status", "Evidence"]
    assert [(r[0], r[1]) for r in rows[1:]] == [(a["name"], a["status"]) for a in bp.ASSISTANTS]
    assert all(r[2] for r in rows[1:])  # every status has its evidence
    tested = {a["name"] for a in bp.ASSISTANTS if a["status"] == "tested"}
    assert tested == {"Claude (web)", "ChatGPT (web)", "Perplexity (web)", "Codex CLI"}
    # each tested app has its real run in the doc, not only a row
    for seen in ("claude.ai", "**ChatGPT** (2026-10-09", "**Perplexity** (web, Pro plan", "**Codex** (codex-cli"):
        assert seen in doc, seen


def test_add_by_hand_has_the_three_connectors_with_the_address_and_a_copy_button():
    hand = section("add-by-hand")
    assert "A listing in the Claude and ChatGPT directories is coming" in hand
    for app in ("claude", "chatgpt", "perplexity"):
        card = hand.split(f'id="add-{app}"', 1)[1].split('<div class="card"', 1)[0]
        assert f'<button class="copy" type="button" data-label="Copy">Copy</button><pre><code>{bp.MCP_URL}</code></pre>' in card, app
    claude = hand.split('id="add-claude"', 1)[1]
    assert bp.claude_connector_link().replace("&", "&amp;") in claude
    assert "red notice" in claude and "press Continue" in claude and bp.CLAUDE_LINK_CHECKED in claude
    assert "Settings → Connectors → Add custom connector" in claude
    assert "Plugins → Add → Add custom MCP server" in hand and "keep OAuth" in hand
    assert "Tick <strong>Write</strong> only if the assistant may change" in hand


def test_the_landing_page_and_the_connect_page_give_the_same_steps():
    """One source (manual_connector_cards): the two pages cannot drift."""
    cards = bp.manual_connector_cards()
    assert cards in CONNECT
    assert bp.manual_connector_cards("add-") in INDEX
    assert bp.manual_connector_cards("add-").replace('id="add-', 'id="') == cards
    for step in bp.PERPLEXITY_HOW:
        assert step.replace("'", "&#x27;").replace('"', "&quot;") in INDEX


def test_the_footer_is_short_and_honest():
    footer = words(LANDING.split('<footer class="landing-footer">', 1)[1].split("</footer>", 1)[0])
    assert "free fan project" in footer.lower() and "Nothing is sold" in footer
    assert "unofficial Fan Content permitted under the Fan Content Policy . Not approved/endorsed by Wizards" in footer
    assert "every answer names its sources" in footer
    for page in ("/privacy.html", "/terms.html", "/credits.html", "/support.html", "/connect.html"):
        assert f'href="{page}"' in LANDING, page
    # pricing is not advertised: "free" only in that quiet footer line
    body = LANDING.split('<footer class="landing-footer">', 1)[0]
    assert not re.search(r"\bfree\b", words(body), flags=re.I)


def test_no_logo_of_another_company_is_shown():
    for src in re.findall(r'<img src="([^"]+)"', LANDING):
        assert src.startswith("/landing/"), src
    assert "<svg" not in LANDING


# ---- the page loads fast and shares well ----------------------------------------------------------------------------------------

def test_meta_description_and_share_image():
    assert re.search(r'<meta name="description" content="[^"]{80,}" />', HEAD)
    for prop in ("og:title", "og:description", "og:url", "og:image", "og:image:alt"):
        assert f'property="{prop}"' in HEAD, prop
    assert '<meta name="twitter:card" content="summary_large_image" />' in HEAD
    image = PUBLIC / re.search(r'property="og:image" content="https://mtgvault.cards/([^"]+)"', HEAD).group(1)
    data = image.read_bytes()
    assert data[:2] == b"\xff\xd8" and len(data) < 150_000
    i = 2
    while True:  # the JPEG's frame header says its size: 1200 x 630, the size share cards use
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC2):
            assert (int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")) == (1200, 630)
            break
        i += 2 + length


def test_no_script_blocks_the_first_paint():
    """The landing page is HTML and CSS before any script: the head holds one small inline script and no script file, and every
    script file comes after the landing markup."""
    assert "<script src" not in HEAD and HEAD.count("<script>") == 1
    assert len(HEAD.split("<script>", 1)[1].split("</script>", 1)[0]) < 1500
    assert INDEX.index('<div id="landing"') < INDEX.index("<script src")


# ---- signed out: the landing page; signed in: straight to the app ------------------------------------------------------------

HEAD_SCRIPT = HEAD.split("<script>", 1)[1].split("</script>", 1)[0]
CASES = [
    # (search, hash, cookie, sessionStorage, landing shown?)
    ("", "", "", {}, True),
    ("", "#demo", "", {}, True),           # the page's own anchors
    ("", "#/", "", {}, True),
    ("", "", "vault_account=4a9c", {}, False),  # a signed-in browser: the app at once
    ("", "", "theme=x; vault_account=4a9c", {}, False),
    ("", "", "not_vault_account=1", {}, True),
    ("", "#/browse", "", {}, False),       # any app address: the app (its sign-in screen if signed out)
    ("", "#/signin", "", {}, False),
    ("?invite=abc", "", "", {}, False),    # an invite and a sign-in error keep the sign-in screen and its message
    ("?signin_error=denied&provider=google", "", "", {}, False),
    ("", "", "", {"vault_signed_out": "1"}, False),   # "You signed out" stays on the sign-in screen
    ("", "", "", {"vault_session_ended": "1"}, False),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")
def test_the_head_script_shows_the_landing_page_only_to_a_signed_out_visitor_of_the_bare_address():
    script = """
const vm = require('node:vm');
const [code, cases] = JSON.parse(process.argv[1]);
const out = cases.map(([search, hash, cookie, session]) => {
  const classes = new Set();
  const sandbox = { location: { search, hash }, document: { cookie, documentElement: { classList: { add: (c) => classes.add(c) } } },
    sessionStorage: { getItem: (k) => (k in session ? session[k] : null) } };
  sandbox.window = sandbox;
  vm.runInNewContext(code, sandbox);
  return { landing: !classes.has('in-app'), home: sandbox.window.VAULT_LANDING_HOME };
});
console.log(JSON.stringify(out));
"""
    done = subprocess.run(["node", "-e", script, json.dumps([HEAD_SCRIPT, [c[:4] for c in CASES]])],
                          capture_output=True, text=True, check=True, timeout=30)
    results = json.loads(done.stdout)
    assert [r["landing"] for r in results] == [c[4] for c in CASES]
    # the app knows the address was the bare / even after it takes ?invite off it: only then may it fall back to the landing page
    assert [r["home"] for r in results] == [not s and not re.match(r"#/.", h) and not session for s, h, _, session, _ in CASES]


def test_the_marker_the_head_script_reads_is_the_one_the_server_sets_and_it_is_not_a_credential():
    app_py = text(ROOT / "vault" / "app.py")
    assert 'ACCOUNT_COOKIE = "vault_account"' in app_py and "httponly=False" in app_py
    assert "vault_account=" in HEAD_SCRIPT
    assert 'session_cookie="vault_session"' in app_py
    assert not re.search(r"vault_session\b(?!_ended)", HEAD_SCRIPT)  # the session cookie (HttpOnly) is never read


def test_the_app_settles_it_from_the_servers_answer():
    assert "if (auth === 'signed-out') return landing ? null : <SignIn />;" in APP
    assert "if (auth === 'signed-in') { vaultShowLanding(false);" in APP
    assert "else if (auth === 'signed-out') vaultShowLanding(landing);" in APP
    assert "window.addEventListener('hashchange', onHash);" in APP and "setLanding(vaultAtLandingHome());" in APP
    assert "setLanding(false);  // the sign-in screen, which says the session ended" in APP
    # a reload at / keeps the bare address, so it shows the landing page again
    assert "window.VAULT_LANDING_HOME ? location.href : vaultUrlFor(route)" in APP
    # the landing page and the app never show together
    assert "html.in-app .landing { display: none; }" in CSS and "html:not(.in-app) #root { display: none; }" in CSS
    # the sign-in screen links back to the story
    assert '<a href="/">What the Vault does, and how to connect your assistant</a>' in text(PUBLIC / "views" / "account.jsx")


def test_the_generated_part_is_up_to_date():
    assert bp.index_html().replace("\r\n", "\n") == INDEX, "run: python scripts/build_plugin.py"
