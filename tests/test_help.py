"""In-app help (#152): every main view has a "?" that lands on a real section, every link into the help
resolves, the welcome is dismissible and remembered, and the sections' wording stays tied to the app."""

import re
from pathlib import Path

PUBLIC = Path(__file__).parent.parent / "public"
HELP = (PUBLIC / "views" / "help.jsx").read_text(encoding="utf-8")
APP = (PUBLIC / "app.jsx").read_text(encoding="utf-8")
ACCOUNT = (PUBLIC / "views" / "account.jsx").read_text(encoding="utf-8")
CSS = (PUBLIC / "layout.css").read_text(encoding="utf-8")
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
    assert "<HelpHint view={route.view} shared={!!viewing} />" in APP and "<Help section={route.section} />" in APP
    assert 'HelpHint view="dashboard"' in APP  # the empty vault has one too
    assert "shared={!!viewing}" in APP and "<HelpHint view=\"dashboard\" shared />" in APP  # a shared collection's hint opens Sharing
    assert "shared && view === 'dashboard' ? 'sharing'" in HELP
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


# -- the wording is checked against the app (#152): every name the help puts in curly quotes is a label in the views ----

VIEW_FILES = [p for p in sorted((PUBLIC / "views").glob("*.jsx")) if p.name != "help.jsx"] + [PUBLIC / "app.jsx"]
VIEW_TEXT = "\n".join(p.read_text(encoding="utf-8") for p in VIEW_FILES).replace("&amp;", "&")
DECK = (PUBLIC / "views" / "deck.jsx").read_text(encoding="utf-8")
GRAPH = (PUBLIC / "views" / "graph.jsx").read_text(encoding="utf-8")


def sections() -> dict[str, str]:
    """Each help section's text, by id (the strings of its `body` and `links`)."""
    out = {}
    chunks = re.split(r"\n  \{\n    id: '", HELP)[1:]
    for chunk in chunks:
        sid = chunk.split("'", 1)[0]
        out[sid] = " ".join(re.findall(r"^\s+'((?:[^'\\]|\\.)*)',?$", chunk, re.M)).replace("\\'", "'")
    return out


def quoted(text: str) -> list[str]:
    return re.findall(r"“([^”]+)”", text)


def shown_in_app(label: str) -> bool:
    """The label is a string the views render: a JSX text node, an attribute value or a string literal, whole."""
    return re.search(r"(^|[>\"'`{(\s])" + re.escape(label) + r"($|[<\"'`})\s,.…:])", VIEW_TEXT, re.M) is not None


def test_help_parsing_finds_every_section():
    assert set(sections()) == set(SECTION_IDS) and all(len(t) > 80 for t in sections().values())


def test_every_name_in_curly_quotes_is_a_label_the_app_shows():
    names = {name for text in sections().values() for name in quoted(text)}
    assert len(names) >= 30, "the help lost its named labels"
    missing = sorted(n for n in names if not shown_in_app(n))
    assert not missing, f"the help names labels the app does not have (renamed or removed?): {missing}"


def test_the_checker_rejects_a_label_the_app_does_not_have():
    assert shown_in_app("Save to your decks") and shown_in_app("Update saved copy") and shown_in_app("Buy list")
    assert not shown_in_app("Save deck")  # what the help used to say; the button says "Save to your decks"
    assert not shown_in_app("Deck ideas") and not shown_in_app("The Value view")


def test_the_decks_section_names_every_tab_and_the_pills_and_buttons_of_a_deck():
    tabs = re.findall(r"\['[a-z]+', '([^']+)'\]", DECK.split("const TABS =", 1)[1].split("];", 1)[0])
    assert tabs == ["Cards", "Stats", "Legality", "Upgrades", "Combos", "Buy list", "History"]  # History only for a saved deck
    named = set(quoted(sections()["decks"]))
    assert set(tabs) <= named, set(tabs) - named
    assert {"From a link", "Paste a list", "Save to your decks", "Update saved copy", "Refresh", "Remove", "Have", "Partial", "Need", "Copy list"} <= named
    assert 'label="Have"' in DECK and 'label="Partial"' in DECK and 'label="Need"' in DECK


def test_the_graph_section_names_every_mode_the_graph_has_and_does_not_claim_a_view_that_is_not_built():
    modes = re.findall(r"setMode\('[a-z]+'\)\}>([^<]+)</button>", GRAPH)
    assert len(modes) == 7, modes  # six modes and the Deck map: if a mode is cut or added, the help changes with it
    text = sections()["graph"]
    assert set(modes) <= set(quoted(text)), set(modes) - set(quoted(text))
    assert {"Price tier", "Depth"} <= set(quoted(text))
    # The deck ideas view (#161/#163) does not exist: no view, no route, no help link. The help says so.
    assert "not built yet" in text and "is being replaced" not in text
    assert not re.search(r"ideas", APP + HELP.split("const HELP_SECTIONS", 1)[0], re.I)
    assert VIEWS == ["dashboard", "browse", "sets", "decks", "lab", "graph", "valuation", "help"]


def test_the_lab_section_names_the_lab_sections_it_has():
    lab = (PUBLIC / "views" / "lab.jsx").read_text(encoding="utf-8").replace("&amp;", "&")
    for name in quoted(sections()["lab"]):
        assert name.lower() in lab.lower(), name
    assert "Cards by P&L" in lab and "Biggest stockpiles" in lab and "Acquisition spend by month" in lab


def test_the_decks_section_matches_how_archidekt_is_used():
    """Read only, on request, credited (README Attribution, tests/test_archidekt_readonly.py)."""
    from vault import archidekt_cache

    text = sections()["decks"]
    assert archidekt_cache.TTL.total_seconds() == 600 and "keeps a copy for ten minutes" in text
    assert "only when you ask" in text and "never searches or crawls Archidekt" in text and "never changes anything there" in text
    assert "deck list from Archidekt, thanks to its author" in DECK  # the credit the page shows on every Archidekt deck
    privacy = sections()["privacy"]
    assert "Scryfall" in privacy and "Archidekt decks are credited to their authors" in privacy and "not endorsed by Wizards" in privacy


def test_the_sections_no_longer_say_what_the_app_does_not_do():
    all_text = " ".join(sections().values())
    for stale in ("press Save deck", "The Value view", "live Scryfall", "is being replaced", "Share your collection creates",
                  "Account, Connected apps", "Account, Your data", "Account, Shared with me"):
        assert stale not in all_text, stale
    text = sections()["import"]  # the three-way re-import (#194) is proved by tests/test_reimport_merge.py
    assert "applies only what changed in your app" in text and "keeps edits you made through an assistant" in text
    assert "replaces the collection with the new file" not in text


def test_a_help_link_scrolls_its_section_heading_to_the_top_not_just_into_view():
    """Seen in the browser at 390 px (#159): focus() alone scrolled the least it could, so the section a "?" opened
    sat under the bottom tab bar. The heading is scrolled to the top, below the top bar."""
    assert "el.focus({ preventScroll: true }); el.scrollIntoView({ block: 'start' });" in HELP
    block = CSS.split(".help-section h2 {", 1)[1].split("}", 1)[0]
    assert "scroll-margin-top" in block
