"""The player guide (docs/site) and its publishing (bead vault-30s.3.2): the pages build, every link resolves, nothing unverified ships,
and the workflow that publishes it publishes nothing until the owner switches it on.

- scripts/build_site.py wraps each page in the shared header and footer (which link back to https://mtgvault.cards) and refuses a page
  that is "being written" yet carries steps, or a "checked" page that does not say when and on which app it was run;
- scripts/check_site_links.py fails on a broken internal link, an address on mtgvault.cards that is not in public/, a missing
  screenshot, a root-relative link (the guide lives under /the-vault/ on GitHub Pages) and an image loaded from another site;
- .github/workflows/guide-pages.yml and the tests.yml step (the permissions rules themselves are in tests/test_workflows.py).
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_site  # noqa: E402
import check_site_links  # noqa: E402
import prepush  # noqa: E402

PUBLIC = ROOT / "public"
SOURCE = ROOT / "docs" / "site"
# The pages named in vault-30s.3.1 (the content bead): the skeleton must have a file for each.
GUIDE_PAGES = ["index.html", "install-chatgpt-desktop.html", "install-claude.html", "sign-in.html", "first-five-minutes.html",
               "troubleshooting.html"]
LAYOUT = (SOURCE / "_layout.html").read_text(encoding="utf-8")
DRAFT = "<!-- title: {t} -->\n<!-- summary: s -->\n<!-- status: being-written -->\n<h1>{t}</h1>\n"


def source_site(folder: Path, pages: dict[str, str]) -> Path:
    """A guide source folder with the real layout and the given pages ({file name: text})."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "_layout.html").write_text(LAYOUT, encoding="utf-8")
    (folder / "style.css").write_text("body{}", encoding="utf-8")
    for name, text in pages.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return folder


def home(extra: str = "") -> str:
    return "<!-- title: Home -->\n<!-- summary: s -->\n<!-- status: home -->\n<h1>Home</h1>\n" + extra


def built(tmp_path: Path, pages: dict[str, str]) -> Path:
    out = tmp_path / "_site"
    build_site.build(source_site(tmp_path / "src", pages), out)
    return out


def problems_of(site: Path) -> list[str]:
    return check_site_links.check_site(site, PUBLIC)[0]


# -- the real guide ------------------------------------------------------------------------------------------------------------------------

def test_the_real_guide_builds_and_every_link_resolves(tmp_path):
    out = tmp_path / "_site"
    written = build_site.build(SOURCE, out)
    assert set(GUIDE_PAGES) <= set(written) and "style.css" in written
    assert not any(name.startswith("_") for name in written), "the layout is not published"
    assert problems_of(out) == []


def test_the_command_line_check_passes_on_the_real_guide():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_site_links.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "ok." in result.stdout


def test_every_page_has_the_shared_header_and_footer_linking_back_to_the_vault(tmp_path):
    out = tmp_path / "_site"
    build_site.build(SOURCE, out)
    for name in GUIDE_PAGES:
        page = (out / name).read_text(encoding="utf-8")
        assert '<a class="back" href="https://mtgvault.cards">Back to the Vault</a>' in page, name
        assert "https://mtgvault.cards/support.html" in page and "Fan Content Policy" in page, name
        assert page.count("<h1") == 1 and "<title>" in page and 'name="description"' in page, name
        assert "{{" not in page, f"{name}: a layout placeholder was left unfilled"


def test_the_guide_has_a_page_for_each_topic_of_the_content_bead_and_no_unverified_steps():
    """Until the real-app rows pass (vault-30s.3.1), a page is a placeholder: marked, and with no numbered steps. When the content
    lands the page is `checked` with a date and the app version (the builder enforces both)."""
    pages = {p["rel"]: p for p in build_site.load_pages(SOURCE)}
    assert set(GUIDE_PAGES) <= set(pages)
    for name in GUIDE_PAGES[1:]:
        assert pages[name]["status"] in ("being-written", "checked"), name
        if pages[name]["status"] == "being-written":
            assert "<ol" not in pages[name]["body"].lower(), name
    assert pages["index.html"]["status"] == "home"


def test_the_home_page_is_in_plain_words_for_a_player():
    """Audience rule (consumers, not developers): no command-line, key or configuration words on the start page."""
    text = (SOURCE / "index.html").read_text(encoding="utf-8").lower()
    words = set(re.findall(r"[a-z]+", text))
    for word in ("mcp", "cli", "api", "token", "config", "terminal", "json", "oauth", "github"):
        assert word not in words, word
    assert "https://mtgvault.cards/connect.html" in text and "magic: the gathering" in text


def test_a_being_written_page_says_so_and_asks_search_engines_to_skip_it(tmp_path):
    out = tmp_path / "_site"
    build_site.build(SOURCE, out)
    page = (out / "install-claude.html").read_text(encoding="utf-8")
    assert "This page is being written." in page and "Only steps that were run on the real app are published" in page
    assert '<meta name="robots" content="noindex">' in page


# -- the builder's rules -------------------------------------------------------------------------------------------------------------------

def test_a_page_being_written_may_not_carry_steps(tmp_path):
    src = source_site(tmp_path / "src", {"index.html": home(), "a.html": DRAFT.format(t="A") + "<ol><li>Click here</li></ol>"})
    with pytest.raises(build_site.SiteError) as caught:
        build_site.build(src, tmp_path / "out")
    assert "may not contain steps" in str(caught.value)


def test_a_checked_page_must_say_when_and_where_it_was_run(tmp_path):
    page = DRAFT.format(t="A").replace("being-written", "checked") + "<ol><li>Click here</li></ol>"
    src = source_site(tmp_path / "src", {"index.html": home(), "a.html": page})
    with pytest.raises(build_site.SiteError) as caught:
        build_site.build(src, tmp_path / "out")
    assert "checked: <date>" in str(caught.value)
    ok = page.replace("<!-- status: checked -->", "<!-- status: checked -->\n<!-- checked: 2026-10-14, Claude 1.2.3 -->")
    src = source_site(tmp_path / "src2", {"index.html": home(), "a.html": ok})
    out = tmp_path / "out2"
    build_site.build(src, out)
    html = (out / "a.html").read_text(encoding="utf-8")
    assert "Checked on the real app: 2026-10-14, Claude 1.2.3." in html and "being written" not in html


@pytest.mark.parametrize("missing", ["title", "summary", "status"])
def test_a_page_without_its_header_lines_is_refused(tmp_path, missing):
    lines = {"title": "<!-- title: A -->", "summary": "<!-- summary: s -->", "status": "<!-- status: being-written -->"}
    page = "\n".join(v for k, v in lines.items() if k != missing) + "\n<h1>A</h1>"
    src = source_site(tmp_path / "src", {"index.html": home(), "a.html": page})
    with pytest.raises(build_site.SiteError) as caught:
        build_site.build(src, tmp_path / "out")
    assert f"`<!-- {missing}: ... -->`" in str(caught.value)


def test_a_guide_without_a_start_page_is_refused(tmp_path):
    src = source_site(tmp_path / "src", {"a.html": DRAFT.format(t="A")})
    with pytest.raises(build_site.SiteError) as caught:
        build_site.build(src, tmp_path / "out")
    assert "index.html is missing" in str(caught.value)


def test_files_starting_with_an_underscore_are_not_published_and_assets_are_copied(tmp_path):
    src = source_site(tmp_path / "src", {"index.html": home(), "assets/shot.png": "png", "_notes/todo.html": "<h1>x</h1>"})
    written = build_site.build(src, tmp_path / "out")
    assert "assets/shot.png" in written and "_layout.html" not in written and not any("_notes" in w for w in written)


def test_the_output_folder_may_not_be_the_source_or_inside_it(tmp_path):
    src = source_site(tmp_path / "src", {"index.html": home()})
    for out in (src, src / "_site", src.parent):
        with pytest.raises(build_site.SiteError):
            build_site.build(src, out)


# -- the link checker: each rule fails the build, and a good page passes ---------------------------------------------------------------------

BAD = {
    "a broken internal link": ('<a href="nope.html">x</a>', "broken internal link: nope.html"),
    "a link to a page of the Vault that does not exist": ('<a href="https://mtgvault.cards/nothing.html">x</a>', "is not a page the Vault serves"),
    "a link to the Vault without .html": ('<a href="https://mtgvault.cards/connect">x</a>', "is not a page the Vault serves"),
    "an anchor missing on a Vault page": ('<a href="https://mtgvault.cards/connect.html#nope">x</a>', "the anchor #nope is not on"),
    "an anchor missing on this page": ('<a href="#nope">x</a>', "the anchor #nope is not on this page"),
    "an anchor missing on another guide page": ('<a href="index.html#nope">x</a>', "the anchor #nope is not on index.html"),
    "a missing screenshot": ('<img src="assets/missing.png" alt="the screen">', "broken internal image: assets/missing.png"),
    "an image without alt text": ('<img src="style.css">', "needs an alt attribute"),
    "an image from another site": ('<img src="https://example.com/a.png" alt="">', "loaded from another site"),
    "a root-relative link": ('<a href="/connect.html">x</a>', "root-relative link"),
    "a protocol-relative link": ('<a href="//mtgvault.cards/connect.html">x</a>', "protocol-relative"),
    "a plain http link": ('<a href="http://mtgvault.cards/connect.html">x</a>', "use https://"),
    "a subdomain of the Vault": ('<a href="https://www.mtgvault.cards/">x</a>', "only https://mtgvault.cards is a Vault address"),
    "a link out of the site": ('<a href="../../etc/passwd">x</a>', "points outside the site"),
    "an app script as a page": ('<a href="https://mtgvault.cards/app.bundle.js">x</a>', "is not a page the Vault serves"),
    "a javascript link": ('<a href="javascript:alert(1)">x</a>', "are not allowed"),
}


@pytest.mark.parametrize("name", list(BAD))
def test_the_link_check_fails_on(tmp_path, name):
    html, expected = BAD[name]
    problems = problems_of(built(tmp_path, {"index.html": home(html)}))
    assert any(expected in p for p in problems), problems
    assert all(p.startswith("index.html:") for p in problems), "the problem names the page and the line"


def test_the_link_check_passes_on_good_links_and_counts_them(tmp_path):
    good = ('<a href="https://mtgvault.cards">home</a> <a href="https://mtgvault.cards/">home</a> <a href="https://mtgvault.cards/#/decks">app</a>'
            ' <a href="https://mtgvault.cards/connect.html#claude">claude</a> <a href="https://mtgvault.cards/llms.txt">llms</a>'
            ' <a href="https://mtgvault.cards/setup/claude.md">setup</a> <a href="https://mtgvault.cards/api/mcp">mcp</a>'
            ' <a href="https://github.com/colombod-personal/the-vault/issues">issues</a> <a href="mailto:a@example.com">mail</a>'
            ' <a href="sub.html#top">sub</a> <a href="#here" id="here">here</a> <img src="assets/s.png" alt="Sign-in screen">')
    src = source_site(tmp_path / "src", {"index.html": home(good), "sub.html": DRAFT.format(t="Sub").replace("<h1>", '<h1 id="top">'), "assets/s.png": "png"})
    out = tmp_path / "_site"
    build_site.build(src, out)
    problems, counts = check_site_links.check_site(out, PUBLIC)
    assert problems == [] and counts["external"] == 1 and counts["mtgvault.cards"] >= 7


def test_a_link_between_nested_pages_resolves_from_the_page_that_holds_it(tmp_path):
    nested = DRAFT.format(t="C") + '<a href="../index.html">up</a> <a href="../style.css">css</a> <a href="gone.html">gone</a>'
    site = built(tmp_path, {"index.html": home('<a href="apps/claude.html">c</a>'), "apps/claude.html": nested})
    problems = problems_of(site)
    assert len(problems) == 1 and problems[0].startswith("apps/claude.html:") and "gone.html" in problems[0], problems
    assert '<link rel="stylesheet" href="../style.css">' in (site / "apps" / "claude.html").read_text(encoding="utf-8"), \
        "the layout's stylesheet is relative to the page's depth"


def test_the_link_check_command_exits_1_on_a_broken_link_and_names_it(tmp_path):
    site = built(tmp_path, {"index.html": home('<a href="https://mtgvault.cards/nope.html">x</a><img src="assets/shot.png" alt="shot">')})
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_site_links.py"), str(site)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "nope.html is not a page the Vault serves" in result.stderr and "assets/shot.png" in result.stderr
    assert "FAILED, 2 problem(s)" in result.stderr


def test_every_vault_address_in_the_real_guide_is_a_known_public_page():
    """The same rule, stated directly on the sources: each https://mtgvault.cards/... in docs/site names a file in public/."""
    for page in SOURCE.rglob("*.html"):
        for url in re.findall(r"https://mtgvault\.cards(/[^\s\"'<>#?]*)?", page.read_text(encoding="utf-8")):
            if url in ("", "/", "/api/mcp"):
                continue
            assert (PUBLIC / url.lstrip("/")).is_file(), f"{page.name}: {url} is not in public/"


# -- the publishing workflow ---------------------------------------------------------------------------------------------------------------

def workflow(name: str) -> dict:
    data = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_the_guide_workflow_builds_and_checks_before_it_uploads():
    steps = workflow("guide-pages.yml")["jobs"]["build"]["steps"]
    runs = [s["run"] for s in steps if "run" in s]
    assert runs[0] == "python scripts/build_site.py docs/site _site"
    assert runs[1] == "python scripts/check_site_links.py _site"
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-pages-artifact@"))
    assert upload["with"]["path"] == "_site"
    assert steps.index(upload) > max(i for i, s in enumerate(steps) if s.get("run", "").startswith("python scripts/"))


def test_the_guide_workflow_holds_the_switch_in_a_repository_variable_and_explains_a_failure():
    text = (ROOT / ".github" / "workflows" / "guide-pages.yml").read_text(encoding="utf-8")
    assert "vars.GUIDE_PAGES_ENABLED == 'true'" in text
    assert "Settings -> Pages" in text, "the workflow explains the failure when Pages is off"
    wf = workflow("guide-pages.yml")
    assert "permissions" not in wf["jobs"]["build"], "the build job keeps the read-only token of the workflow"
    assert wf["permissions"] == {"contents": "read"}


def test_the_tests_workflow_checks_the_guide_before_it_installs_anything():
    steps = workflow("tests.yml")["jobs"]["test"]["steps"]
    check = next(i for i, s in enumerate(steps) if s.get("run") == "python scripts/check_site_links.py")
    install = next(i for i, s in enumerate(steps) if "pip install" in s.get("run", ""))
    assert check < install


def test_the_guide_check_is_in_the_pre_push_set():
    assert "tests/test_guide_site.py" in prepush.CROSS_CUTTING
