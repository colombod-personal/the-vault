"""In-app help (#152): every main view has a "?" that lands on a real section, every link into the help
resolves, the welcome is dismissible and remembered, and the sections' wording stays tied to the app."""

import re
from pathlib import Path

PUBLIC = Path(__file__).parent.parent / "public"
HELP = (PUBLIC / "views" / "help.jsx").read_text(encoding="utf-8")
APP = (PUBLIC / "app.jsx").read_text(encoding="utf-8")
ACCOUNT = (PUBLIC / "views" / "account.jsx").read_text(encoding="utf-8")
CSS = (PUBLIC / "styles.css").read_text(encoding="utf-8") + (PUBLIC / "layout.css").read_text(encoding="utf-8")
BUILD = (Path(__file__).parent.parent / "web" / "build.mjs").read_text(encoding="utf-8")

SECTION_IDS = re.findall(r"^\s*id: '([a-z]+)', title:", HELP, re.M)
VIEW_MAP = dict(re.findall(r"(\w+): '([a-z]+)'", re.search(r"HELP_FOR_VIEW = \{(.*?)\};", HELP, re.S).group(1)))
VIEWS = re.findall(r"'(\w+)'", re.search(r"VAULT_VIEWS = \[(.*?)\]", APP).group(1))


def test_every_main_view_has_a_help_section_that_exists():
    assert SECTION_IDS, "no help sections found"
    assert len(set(SECTION_IDS)) == len(SECTION_IDS)
    for view in [v for v in VIEWS if v != "help"] + ["setdetail"]:
        assert view in VIEW_MAP, f"view {view} has no help section"
    assert set(VIEW_MAP.values()) <= set(SECTION_IDS)


def test_every_link_into_the_help_lands_on_a_section():
    targets = set(re.findall(r"#/help/([a-z]+)", HELP + APP + ACCOUNT))
    assert targets <= set(SECTION_IDS), targets - set(SECTION_IDS)
    assert "'help-' + s.id" in HELP  # the heading ids the links land on


def test_the_question_mark_is_on_every_view_and_the_help_is_reachable_and_routed():
    assert "<HelpHint view={route.view} />" in APP and "<Help section={route.section} />" in APP
    assert "view === 'help'" in APP and "helpHashFor(route.section)" in APP
    assert 'href="#/help"' in ACCOUNT
    assert "'views/help.jsx'," in BUILD and BUILD.index("views/help.jsx") < BUILD.index("'app.jsx'")


def test_the_help_is_accessible_and_the_touch_targets_are_44px():
    assert "aria-label=\"Help for this page\"" in HELP and 'aria-label="Help topics"' in HELP
    assert "aria-labelledby" in HELP and ".focus(" in HELP and "tabindex" in HELP.lower()
    for sel in (".help-hint {", ".help-toc a {"):
        block = CSS.split(sel, 1)[1].split("}", 1)[0]
        assert "min-height: 44px" in block, sel


def test_the_welcome_is_non_blocking_dismissible_and_remembered_safely():
    assert 'className="welcome"' in HELP and "role=\"dialog\"" not in HELP and "aria-modal" not in HELP
    assert "onClick={onDismiss}" in HELP and "welcomeDone()" in APP
    assert "try { return localStorage" in HELP and "try { localStorage.setItem" in HELP
    assert "useStateApp(() => !welcomeSeen())" in APP
