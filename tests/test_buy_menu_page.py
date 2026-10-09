"""The "Where to buy" menu in the web app (#212, docs/where-to-buy-design.md): where it is, what it never does. Its wording is public/lib/buy.js
(tests/js/buy.test.mjs), its layout public/views/buy.jsx; the links and their order come from the server (tests/test_buy_api.py)."""

import re
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"
ROOT = PUBLIC.parent
BUY = (PUBLIC / "views" / "buy.jsx").read_text(encoding="utf-8")
LIB = (PUBLIC / "lib" / "buy.js").read_text(encoding="utf-8")
API = (PUBLIC / "lib" / "api.js").read_text(encoding="utf-8")
CSS = (PUBLIC / "layout.css").read_text(encoding="utf-8")


def view(name: str) -> str:
    return (PUBLIC / "views" / name).read_text(encoding="utf-8")


def test_the_menu_is_on_a_missing_card_in_the_ideas_view_the_lab_buy_rows_and_the_deck_page():
    assert "<BuyMenu card={name} />" in view("ideas.jsx") and view("ideas.jsx").count("<BuyMenu") == 2  # the alternatives list and the "none" panel
    assert "<BuyMenu card={p.card} />" in view("lab.jsx")
    deck = view("deck.jsx")
    assert "{r.need > 0 && <BuyMenu card={r.name} />}" in deck  # only a card the person still needs
    assert "<BuySettingsSection />" in view("account.jsx")


def test_the_files_are_loaded_and_built():
    assert 'src="lib/buy.js"' in (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert "'views/buy.jsx'" in (ROOT / "web" / "build.mjs").read_text(encoding="utf-8")


def test_the_menu_is_text_only_and_every_link_opens_in_a_new_tab_without_a_referrer():
    assert not re.search(r"<img|<svg|background-image|<canvas|<iframe|<video", BUY), "the menu has no image, map or frame"
    anchors = re.findall(r"<a\b[^>]*>", BUY)
    assert anchors and all('target="_blank"' in a and 'rel="noopener noreferrer"' in a for a in anchors), anchors
    assert "Where to buy" in BUY and "Where I buy" in BUY


def test_the_page_never_asks_for_a_position_or_keeps_one():
    """Design section 4.4 as built: the locator opens on its own page and the person types their town there. No geolocation, no place box,
    nothing stored in the browser, and the language is read once, in the browser, to order the shops while no country is saved."""
    for text in (BUY, LIB):
        for forbidden in ("geolocation", "getCurrentPosition", "watchPosition", "localStorage", "sessionStorage", "indexedDB", "cookie"):
            assert forbidden not in text, forbidden
    assert BUY.count("navigator.language") == 1 and "navigator.languages" not in BUY
    assert "Type your town" not in BUY and "postcode" in LIB  # the sentence about where to type it is the library's, once


def test_the_browser_sends_the_server_the_card_and_nothing_about_where_it_is():
    buy_calls = re.findall(r"^\s*(buyMenu|buyCountries|buySettings|saveBuySettings|removeBuySettings):.*$", API, flags=re.M)
    assert len(buy_calls) == 5
    for line in re.findall(r"^\s*(?:buyMenu|buyCountries|buySettings|saveBuySettings|removeBuySettings):.*$", API, flags=re.M):
        assert not re.search(r"language|locale|region|navigator|Accept-Language", line, flags=re.I), line
    assert "encodeURIComponent(card)" in API


def test_the_menu_and_the_settings_have_the_words_the_design_asks_for():
    for text in ("Where do you buy?", "Not now", "More shops", "Scryfall card page", "About these links", "Remove this store", "Add a store",
                 "Search address (optional)", "Web address (https only)", "never contacts a shop"):
        assert text in BUY, text


def test_the_menu_rows_are_tall_enough_for_a_thumb_and_the_text_is_not_smaller_than_12px():
    block = CSS[CSS.index("/* ---- Where to buy"):CSS.index("/* ==== Phone layout")]
    assert re.search(r"\.buy-link \{[^}]*min-height: 44px", block)
    assert not [s for s in re.findall(r"font-size:\s*([\d.]+)px", block) if float(s) < 12]
    assert "overflow-x" not in block and "position: absolute" in block and block.count("position: absolute") == 1  # only the screen-reader text
