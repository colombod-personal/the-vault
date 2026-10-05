"""Dismissing overlays (usability review): the card panel's close button used to be a 32px circle, 64% of it under
the card header, with no name or focus ring, scrolling away with the panel, and pushed off-screen on phones when a
wide table made the page wider than the screen. These guards keep the fixes in place; the behaviour was checked in
a real browser (docs/usability-review.md)."""

import re
from pathlib import Path

PUBLIC = Path(__file__).parent.parent / "public"
APP = (PUBLIC / "app.jsx").read_text(encoding="utf-8")
ACCOUNT = (PUBLIC / "views" / "account.jsx").read_text(encoding="utf-8")
CSS = (PUBLIC / "layout.css").read_text(encoding="utf-8")


def rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"layout.css has no rule for {selector}"
    return m.group(1)


def test_the_card_panels_close_button_is_named_focused_first_and_in_its_own_sticky_row():
    assert 'className="drawer-bar"' in APP and 'aria-label="Close card details"' in APP and "data-autofocus" in APP
    assert 'role="dialog"' in APP and 'aria-modal="true"' in APP
    bar = rule(".drawer .drawer-bar")
    assert "position: sticky" in bar and "top: 0" in bar and "z-index" in bar
    button = rule(".drawer .drawer-bar .close")
    assert "width: 44px" in button and "height: 44px" in button and "position: static" in button
    assert ":focus-visible" in CSS and "outline: 2px solid var(--gold)" in CSS


def test_the_other_close_buttons_are_named_and_finger_sized():
    assert 'aria-label="Dismiss message"' in APP and 'aria-label={`Close ${title}`}' in ACCOUNT
    assert "close-x" in APP and "close-x" in ACCOUNT
    # 44px everywhere, and specific enough that the phone rule for small buttons (min-height 34px) can't shrink it
    assert "min-width: 44px" in rule(".btn.xs.close-x, .close-x") and "min-height: 44px" in rule(".btn.xs.close-x, .close-x")


def test_dialogs_move_focus_in_keep_it_inside_and_give_it_back():
    assert "function useDialogFocus" in ACCOUNT and "opener.focus()" in ACCOUNT and "e.key !== 'Tab'" in ACCOUNT
    assert "window.useDialogFocus(panel)" in APP and "useDialogFocus(box)" in ACCOUNT
    assert 'aria-modal="true"' in ACCOUNT


def test_a_wide_table_scrolls_inside_its_panel_instead_of_widening_the_page():
    assert "overflow-x: auto" in rule(".panel-flush, .panel:has(> table.tbl)")
    assert "overflow-x: clip" in rule("html, body")
    assert 'className="filter-grid"' in (PUBLIC / "views" / "browse.jsx").read_text(encoding="utf-8")
    assert ".filter-grid { grid-template-columns: 1fr 1fr !important; }" in CSS


def test_the_current_section_is_announced():
    assert APP.count("aria-current={") == 6


def test_the_x_of_a_close_button_never_takes_the_tap():
    """The × is drawn lines that ignore taps, so a tap on it lands on the round button (a text × used to
    catch taps on the card panel instead of its button)."""
    assert "function CloseIcon" in ACCOUNT and "<CloseIcon />" in ACCOUNT
    assert APP.count("<window.CloseIcon />") == 2 and '<span aria-hidden="true">×</span>' not in APP
    assert "✕</button>" not in APP and "✕</button>" not in ACCOUNT
    assert "pointer-events: none" in rule(".close-icon")
