"""The player guide (docs/site) and its publishing (bead vault-30s.3.2): the pages build, every link resolves, nothing unverified ships,
and the workflow that publishes it publishes nothing until the owner switches it on.

- scripts/build_site.py wraps each page in the shared header and footer (which link back to https://mtgvault.cards), publishes an
  allow-list of file types only (no dotfiles, no `.local.` notes, no symlinks, no SVG), never deletes a folder it did not make,
  and refuses a page that is "being written" yet carries steps, or a "checked" page that does not say when and on which app;
- scripts/check_site_links.py fails on a broken internal link, an address on mtgvault.cards that is not in public/, a missing
  screenshot, a root-relative link (the guide lives under /the-vault/ on GitHub Pages), an image loaded from another site, an e-mail
  link, and the constructs that can load or run something (base, iframe, srcset, inline script, handlers, CSS url and @import);
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


def refused(tmp_path: Path, pages: dict[str, str]) -> str:
    with pytest.raises(build_site.SiteError) as caught:
        build_site.build(source_site(tmp_path / "src", pages), tmp_path / "out")
    return str(caught.value)


# -- the real guide ------------------------------------------------------------------------------------------------------------------------

def test_the_real_guide_builds_and_every_link_resolves(tmp_path):
    out = tmp_path / "_site"
    written = build_site.build(SOURCE, out)
    assert set(GUIDE_PAGES) <= set(written) and "style.css" in written
    assert not any(name.startswith("_") or name.startswith(".") for name in written), "the layout and dotfiles are not published"
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
    """Until the real-app rows pass (vault-30s.3.1), a page is a placeholder: marked, and with no steps. When the content
    lands the page is `checked` with a date and the app version (the builder enforces both)."""
    pages = {p["rel"]: p for p in build_site.load_pages(SOURCE)}
    assert set(GUIDE_PAGES) <= set(pages)
    for name in GUIDE_PAGES[1:]:
        assert pages[name]["status"] in ("being-written", "checked"), name
        if pages[name]["status"] == "being-written":
            assert not any(p.search(pages[name]["body"]) for p in build_site.STEP_PATTERNS), name
    assert pages["index.html"]["status"] == "home"


def test_every_page_and_the_layout_are_in_plain_words_for_a_player():
    """Audience rule (consumers, not developers): no command-line, key or configuration words on any page of the guide."""
    files = sorted(SOURCE.rglob("*.html"))
    assert len(files) >= 7
    for path in files:
        words = set(re.findall(r"[a-z]+", path.read_text(encoding="utf-8").lower()))
        for word in ("mcp", "cli", "api", "token", "config", "terminal", "json", "oauth", "github", "curl", "sudo"):
            assert word not in words, f"{path.name}: {word}"
    home_text = (SOURCE / "index.html").read_text(encoding="utf-8").lower()
    assert "https://mtgvault.cards/connect.html" in home_text and "magic: the gathering" in home_text


def test_a_placeholder_does_not_call_the_connect_page_current_until_a_real_run_exists():
    """vault-30s.3.1 has not run any step on the real app yet: the guide may point at the Connect page, but not vouch for it."""
    for path in SOURCE.glob("*.html"):
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "current instructions" not in text, path.name
        if "mtgvault.cards/connect.html" in text:
            assert "not" in text.split("connect.html")[1].split("</p>")[0], f"{path.name}: say the Connect page is not re-checked"


def test_a_being_written_page_says_so_and_asks_search_engines_to_skip_it(tmp_path):
    out = tmp_path / "_site"
    build_site.build(SOURCE, out)
    page = (out / "install-claude.html").read_text(encoding="utf-8")
    assert "This page is being written." in page and "Only steps that were run on the real app are published" in page
    assert '<meta name="robots" content="noindex">' in page


# -- the builder's rules -------------------------------------------------------------------------------------------------------------------

STEPS = {
    "a numbered list": "<ol><li>Open it</li></ol>",
    "a block to copy": "<pre>do this</pre>",
    "a bullet that starts 1.": "<ul><li>1. Open it</li></ul>",
    "a bullet that starts 1)": "<ul><li><strong>1)</strong> Open it</li></ul>",
    "Step 1": "<p>Step 1: open the app</p>",
    "step 2 in lower case": "<p>then step 2 is to sign in</p>",
    "a paragraph that starts 1.": "<p>1. Open the app</p>",
    "a line that starts 2.": "<p>Do these:\n2. Sign in</p>",
}


@pytest.mark.parametrize("name", list(STEPS))
def test_a_page_being_written_may_not_carry_steps(tmp_path, name):
    message = refused(tmp_path, {"index.html": home(), "a.html": DRAFT.format(t="A") + STEPS[name]})
    assert "may not contain steps" in message


def test_the_step_check_lets_ordinary_sentences_through(tmp_path):
    prose = "<p>Version 2 of the app, about 5 minutes, and a step by step explanation will come here. It covers 3 things.</p>"
    out = built(tmp_path, {"index.html": home(), "a.html": DRAFT.format(t="A") + prose})
    assert (out / "a.html").is_file()


def test_a_checked_page_must_say_when_and_where_it_was_run(tmp_path):
    page = DRAFT.format(t="A").replace("being-written", "checked") + "<ol><li>Click here</li></ol>"
    assert "checked: <date>" in refused(tmp_path, {"index.html": home(), "a.html": page})
    ok = page.replace("<!-- status: checked -->", "<!-- status: checked -->\n<!-- checked: 2026-10-14, Claude 1.2.3 -->")
    out = tmp_path / "out2"
    build_site.build(source_site(tmp_path / "src2", {"index.html": home(), "a.html": ok}), out)
    html = (out / "a.html").read_text(encoding="utf-8")
    assert "Checked on the real app: 2026-10-14, Claude 1.2.3." in html and "being written" not in html


@pytest.mark.parametrize("missing", ["title", "summary", "status"])
def test_a_page_without_its_header_lines_is_refused(tmp_path, missing):
    lines = {"title": "<!-- title: A -->", "summary": "<!-- summary: s -->", "status": "<!-- status: being-written -->"}
    page = "\n".join(v for k, v in lines.items() if k != missing) + "\n<h1>A</h1>"
    assert f"`<!-- {missing}: ... -->`" in refused(tmp_path, {"index.html": home(), "a.html": page})


def test_a_guide_without_a_start_page_is_refused(tmp_path):
    assert "index.html is missing" in refused(tmp_path, {"a.html": DRAFT.format(t="A")})


def test_files_starting_with_an_underscore_are_not_published_and_allowed_assets_are_copied(tmp_path):
    assets = {f"assets/shot{suffix}": "x" for suffix in (".png", ".jpg", ".jpeg", ".webp", ".ico", ".txt", ".css")}
    src = source_site(tmp_path / "src", {"index.html": home(), "_notes/todo.html": "<h1>x</h1>", "_notes/n.md": "x", **assets})
    written = build_site.build(src, tmp_path / "out")
    assert set(assets) <= set(written) and "_layout.html" not in written and not any("_notes" in w for w in written)
    assert not any(w.startswith(".") for w in written), "the marker file is not listed as published content"


@pytest.mark.parametrize("name", ["notes.md", "build.py", "logo.svg", "page.htm", ".secret", ".env", "assets/.hidden.png",
                                  "draft.local.png", "plan.local.html", "assets/run.js", "data.json", "archive.zip", "noextension"])
def test_only_the_allow_listed_file_types_are_published(tmp_path, name):
    message = refused(tmp_path, {"index.html": home(), name: "x"})
    assert Path(name).name in message
    assert "is never published" in message or "are not published" in message


def test_a_symbolic_link_is_refused_even_when_it_points_outside_the_tree(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    src = source_site(tmp_path / "src", {"index.html": home()})
    try:
        (src / "secret.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    with pytest.raises(build_site.SiteError) as caught:
        build_site.build(src, tmp_path / "out")
    assert "secret.txt: a symbolic link is refused" in str(caught.value)
    assert not (tmp_path / "out").exists() or not (tmp_path / "out" / "secret.txt").exists()


def test_a_symbolic_link_to_a_folder_is_refused_and_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("private", encoding="utf-8")
    src = source_site(tmp_path / "src", {"index.html": home()})
    try:
        (src / "assets").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    with pytest.raises(build_site.SiteError) as caught:
        build_site.build(src, tmp_path / "out")
    assert "assets: a symbolic link is refused" in str(caught.value)


def test_the_output_folder_may_not_be_the_source_or_inside_it(tmp_path):
    src = source_site(tmp_path / "src", {"index.html": home()})
    for out in (src, src / "_site", src.parent):
        with pytest.raises(build_site.SiteError):
            build_site.build(src, out)


def test_a_folder_it_did_not_make_is_never_deleted(tmp_path):
    """`build_site.py docs/site public` once deleted public/ in a fixture: the output must be new, empty or made by this script."""
    src = source_site(tmp_path / "src", {"index.html": home()})
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(build_site.SiteError) as caught:
        build_site.build(src, precious)
    assert "refusing to delete" in str(caught.value)
    assert (precious / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert build_site.main([str(SOURCE), str(precious)]) == 1
    assert (precious / "keep.txt").is_file()


def test_a_folder_it_made_is_replaced_and_an_empty_one_is_used(tmp_path):
    src = source_site(tmp_path / "src", {"index.html": home(), "old.html": DRAFT.format(t="Old")})
    out = tmp_path / "out"
    build_site.build(src, out)
    assert (out / "old.html").is_file() and (out / build_site.MARKER).is_file()
    (src / "old.html").unlink()
    build_site.build(src, out)
    assert not (out / "old.html").exists() and (out / "index.html").is_file()
    empty = tmp_path / "empty"
    empty.mkdir()
    build_site.build(src, empty)
    assert (empty / "index.html").is_file()


def test_navigation_links_are_escaped():
    nav = build_site.nav_html([{"rel": 'a"b<c.html', "meta": {"title": "T"}}], "x.html", "")
    assert '"b' not in nav and "&quot;" in nav and "&lt;" in nav


# -- the link checker: each rule fails the build, and a good page passes ---------------------------------------------------------------------

BAD = {
    "a broken internal link": ('<a href="nope.html">x</a>', "broken internal link: nope.html"),
    "a link to a page of the Vault that does not exist": ('<a href="https://mtgvault.cards/nothing.html">x</a>', "is not a page the Vault serves"),
    "a link to the Vault without .html": ('<a href="https://mtgvault.cards/connect">x</a>', "is not a page the Vault serves"),
    "a Vault page with a trailing slash": ('<a href="https://mtgvault.cards/connect.html/">x</a>', "is not a page the Vault serves"),
    "a Vault page with a double slash": ('<a href="https://mtgvault.cards//connect.html">x</a>', "is not a page the Vault serves"),
    "a Vault page with a dot segment": ('<a href="https://mtgvault.cards/./connect.html">x</a>', "is not a page the Vault serves"),
    "a Vault page with an escape": ('<a href="https://mtgvault.cards/connect%2Ehtml">x</a>', "is not a page the Vault serves"),
    "a Vault link with a port": ('<a href="https://mtgvault.cards:8443/connect.html">x</a>', "no user name or port"),
    "a Vault link with a user name": ('<a href="https://user@mtgvault.cards/connect.html">x</a>', "no user name or port"),
    "an anchor missing on a Vault page": ('<a href="https://mtgvault.cards/connect.html#nope">x</a>', "the anchor #nope is not on"),
    "an anchor missing on this page": ('<a href="#nope">x</a>', "the anchor #nope is not on this page"),
    "an anchor missing on another guide page": ('<a href="index.html#nope">x</a>', "the anchor #nope is not on index.html"),
    "a missing screenshot": ('<img src="assets/missing.png" alt="the screen">', "broken internal image: assets/missing.png"),
    "an image without alt text": ('<img src="style.css">', "needs an alt attribute"),
    "an image from another site": ('<img src="https://example.com/a.png" alt="">', "loaded from another site"),
    "a srcset from another site": ('<img src="style.css" alt="" srcset="https://example.com/a.png 2x">', "loaded from another site"),
    "a missing srcset image": ('<img src="style.css" alt="" srcset="assets/none.png 1x, assets/none2.png 2x">', "broken internal image: assets/none.png"),
    "a video from another site": ('<video src="https://example.com/v.mp4"></video>', "loaded from another site"),
    "an audio file from another site": ('<audio src="https://example.com/v.mp3"></audio>', "loaded from another site"),
    "a missing video poster": ('<video poster="assets/p.png"></video>', "broken internal link: assets/p.png"),
    "an object": ('<object data="https://example.com/x.swf"></object>', "<object> is not allowed"),
    "an embed": ('<embed src="https://example.com/x.swf">', "<embed> is not allowed"),
    "an iframe": ('<iframe src="https://example.com/"></iframe>', "<iframe> is not allowed"),
    "a base element": ('<base href="https://example.com/">', "<base> is not allowed"),
    "a meta refresh": ('<meta http-equiv="refresh" content="0; url=https://example.com/">', "redirect is not allowed"),
    "an inline script": ("<script>alert(1)</script>", "inline <script> is not allowed"),
    "an event handler": ('<p onclick="x()">x</p>', "event handler onclick="),
    "a stylesheet import": ("<style>@import url(other.css);</style>", "@import in CSS is not allowed"),
    "a css url from another site": ('<div style="background:url(https://example.com/x.png)">x</div>', "loaded from another site"),
    "a missing css url file": ("<style>.a{background:url('assets/none.png')}</style>", "broken internal link: assets/none.png"),
    "a root-relative link": ('<a href="/connect.html">x</a>', "root-relative link"),
    "an escaped root-relative link": ('<a href="%2Fetc/hostname">x</a>', "root-relative link"),
    "an escaped way out of the site": ('<a href="..%2F..%2Fetc/hostname">x</a>', "points outside the site"),
    "a protocol-relative link": ('<a href="//mtgvault.cards/connect.html">x</a>', "protocol-relative"),
    "a plain http link": ('<a href="http://mtgvault.cards/connect.html">x</a>', "use https://"),
    "a subdomain of the Vault": ('<a href="https://www.mtgvault.cards/">x</a>', "only https://mtgvault.cards is a Vault address"),
    "a link out of the site": ('<a href="../../etc/passwd">x</a>', "points outside the site"),
    "an app script as a page": ('<a href="https://mtgvault.cards/app.bundle.js">x</a>', "is not a page the Vault serves"),
    "a javascript link": ('<a href="javascript:alert(1)">x</a>', "are not allowed"),
    "an e-mail link": ('<a href="mailto:someone@example.com">mail</a>', "mailto: links are not allowed"),
    "a phone link": ('<a href="tel:+441234567">call</a>', "tel: links are not allowed"),
}


@pytest.mark.parametrize("name", list(BAD))
def test_the_link_check_fails_on(tmp_path, name):
    html, expected = BAD[name]
    problems = problems_of(built(tmp_path, {"index.html": home(html)}))
    assert any(expected in p for p in problems), problems
    assert all(p.startswith("index.html:") for p in problems), "the problem names the page and the line"


def test_the_link_check_fails_on_a_stylesheet_that_imports_or_loads_from_another_site(tmp_path):
    src = source_site(tmp_path / "src", {"index.html": home()})
    (src / "style.css").write_text("@import 'more.css';\n.a { background: url(https://example.com/x.png); }\n.b{background:url(shot.png)}",
                                   encoding="utf-8")
    out = tmp_path / "_site"
    build_site.build(src, out)
    problems = problems_of(out)
    assert any(p.startswith("style.css:") and "@import in CSS" in p for p in problems), problems
    assert any(p.startswith("style.css:") and "loaded from another site" in p for p in problems), problems
    assert any(p.startswith("style.css:") and "broken internal link: shot.png" in p for p in problems), problems


def test_the_link_check_passes_on_good_links_and_counts_them(tmp_path):
    good = ('<a href="https://mtgvault.cards">home</a> <a href="https://mtgvault.cards/">home</a> <a href="https://mtgvault.cards/#/decks">app</a>'
            ' <a href="https://mtgvault.cards/connect.html#claude">claude</a> <a href="https://mtgvault.cards/llms.txt">llms</a>'
            ' <a href="https://mtgvault.cards/setup/claude.md">setup</a> <a href="https://mtgvault.cards/api/mcp">mcp</a>'
            ' <a href="https://github.com/colombod-personal/the-vault/issues">issues</a>'
            ' <a href="sub.html#top">sub</a> <a href="#here" id="here">here</a> <img src="assets/s.png" alt="Sign-in screen">'
            ' <img src="assets/s.png" srcset="assets/s.png 1x" alt="">')
    src = source_site(tmp_path / "src", {"index.html": home(good), "sub.html": DRAFT.format(t="Sub").replace("<h1>", '<h1 id="top">'),
                                         "assets/s.png": "png"})
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


# -- the publishing workflow and its instructions ----------------------------------------------------------------------------------------

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


def test_a_manual_run_from_a_branch_does_nothing_and_cannot_displace_a_waiting_publish():
    """Concurrency keeps one waiting run per group and replaces it with a newer one: a build queued from a branch must not be able to
    take the place of a publish that is waiting. Only the deploy job (main, and only when switched on) is in the group, and the build
    job is for main only."""
    wf = workflow("guide-pages.yml")
    assert "concurrency" not in wf, "a workflow-level group would queue every run, even one from a branch"
    assert wf["jobs"]["build"]["if"] == "github.ref == 'refs/heads/main'"
    assert wf["jobs"]["deploy"]["concurrency"] == {"group": "guide-pages", "cancel-in-progress": False}
    assert "concurrency" not in wf["jobs"]["build"]


def test_the_guide_is_republished_when_what_its_link_check_reads_changes():
    paths = workflow("guide-pages.yml")["on"]["push"]["paths"]
    assert set(paths) == {"docs/site/**", "scripts/build_site.py", "scripts/check_site_links.py", "public/**",
                          ".github/workflows/guide-pages.yml"}


def test_the_tests_workflow_checks_the_guide_before_it_installs_anything():
    steps = workflow("tests.yml")["jobs"]["test"]["steps"]
    check = next(i for i, s in enumerate(steps) if s.get("run") == "python scripts/check_site_links.py")
    install = next(i for i, s in enumerate(steps) if "pip install" in s.get("run", ""))
    assert check < install


def test_the_guide_check_is_in_the_pre_push_set():
    assert "tests/test_guide_site.py" in prepush.CROSS_CUTTING


def test_the_switch_on_steps_make_the_environment_the_first_required_step():
    """The in-file `if` is not the boundary (a branch can carry a copy of the workflow without it): the github-pages environment's
    deployment branches are. The steps create it before the variable is set, say it does not exist today, and give the check."""
    text = (ROOT / "docs" / "developing.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    section = text.split("### Switching it on")[1].split("\n## ")[0]
    assert "does not exist today" in section
    assert section.index("github-pages") < section.index("GUIDE_PAGES_ENABLED")
    assert "gh api repos/colombod-personal/the-vault/environments/github-pages --jq .deployment_branch_policy" in section
    assert section.index("Required") < section.index("GUIDE_PAGES_ENABLED")
