"""Tap targets of at least 44 px on a phone (#90 measured 36 px; the phone-first pass of #95 raised it to 44).

The audit found the 'From a link' / 'Paste a list' chips, the deck tabs and the xs/sm buttons at 34 px by CSS and
nothing that measured them. This test reads the style sheets the page loads (styles.css, then layout.css), resolves
the cascade at phone width (390 px) for every kind of control a person taps, and fails when one is under 36 px.
The same controls were measured in a real browser at 390 px, view by view, with scripts/measure_tap_targets.js
(results in docs/graph-and-lab-review.md); this test keeps them from slipping back.

It is a CSS-level check, not a layout engine: a control counts as 36 px only when a rule gives it a `min-height` (or
`height`) of at least 36 px; a rule anywhere that sets less than 36 px on one of these controls at phone width fails."""

import re
from pathlib import Path

PUBLIC = Path(__file__).parent.parent / "public"
PHONE_WIDTH = 390
MIN_PX = 44


def _strip(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _blocks(css: str):
    """Yield (prelude, body) for each top-level block; at-rule bodies are returned as text."""
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            return
        prelude = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        yield prelude, css[j + 1:k - 1]
        i = k


def rules(path: Path, base_order: int = 0):
    """Flat list of (order, selector, declarations) that apply at phone width, in source order."""
    out, order = [], base_order

    def declarations(body: str) -> dict:
        decls = {}
        for part in body.split(";"):
            if ":" in part:
                key, value = part.split(":", 1)
                decls[key.strip().lower()] = value.strip()
        return decls

    def add(css: str):
        nonlocal order
        for prelude, body in _blocks(css):
            if prelude.startswith("@media"):
                m = re.search(r"max-width:\s*(\d+)px", prelude)
                lo = re.search(r"min-width:\s*(\d+)px", prelude)
                ok = ("(" in prelude and "print" not in prelude and "prefers" not in prelude
                      and (not m or int(m.group(1)) >= PHONE_WIDTH) and (not lo or int(lo.group(1)) <= PHONE_WIDTH))
                if ok and (m or lo):
                    add(body)
                continue
            if prelude.startswith("@"):
                continue
            for selector in prelude.split(","):
                order += 1
                out.append((order, " ".join(selector.split()), declarations(body)))

    add(_strip(path.read_text(encoding="utf-8")))
    return out


RULES = rules(PUBLIC / "styles.css") + rules(PUBLIC / "layout.css", 100_000)
COMPOUND = re.compile(r"^(?P<tag>[a-z][a-z0-9]*)?(?P<classes>(?:\.[A-Za-z0-9_-]+)*)(?P<rest>.*)$")


def _px(value: str):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)px(?:\s*!important)?", value.strip())
    return float(m.group(1)) if m else None


def _match(selector: str, tag: str, classes: set[str]):
    """None when the selector is not about this control; else (exact, specificity). A selector with an ancestor or a
    pseudo-class is not 'exact': it applies in some places only, so it can fail the check but never satisfy it."""
    parts = selector.split()
    if not parts:
        return None
    m = COMPOUND.match(parts[-1])
    if m.group("rest"):  # a pseudo-class or element (:hover, ::before) or an attribute: not the control's own box
        return None
    sel_classes = set(filter(None, m.group("classes").split(".")))
    if (m.group("tag") and m.group("tag") != tag) or not (sel_classes or m.group("tag")) or not sel_classes <= classes:
        return None
    exact = len(parts) == 1 and not m.group("rest")
    return exact, len(sel_classes) * 10 + (1 if m.group("tag") else 0)


def effective_min_height(tag: str, *classes: str):
    """(the min-height the cascade gives this control, the rules that could set it below the minimum)."""
    cls = set(classes)
    winner, too_small = None, []
    for order, selector, decls in RULES:
        hit = _match(selector, tag, cls)
        if not hit:
            continue
        exact, specificity = hit
        for prop in ("min-height",):  # a smaller `height` never shrinks a control below its min-height
            px = _px(decls[prop]) if prop in decls else None
            if px is None:
                continue
            if px < MIN_PX and not exact:
                too_small.append(f"{selector} {{ {prop}: {px:g}px }}")
            important = "!important" in decls[prop]
            if exact and prop == "min-height":
                key = (important, specificity, order)
                if winner is None or key > winner[0]:
                    winner = (key, px)
    return (winner[1] if winner else None), too_small


# Every kind of control a person taps, as the views render it (tag, classes).
CONTROLS = {
    "plain button (.btn)": ("button", "btn"),
    "primary button": ("button", "btn", "primary"),
    "small button (.btn.sm): Save, Refresh, Remove, Find upgrades": ("button", "btn", "sm"),
    "small primary button (.btn.sm.primary): Save to your decks": ("button", "btn", "sm", "primary"),
    "extra small button (.btn.xs): Your decks, Got it": ("button", "btn", "xs"),
    "extra small ghost button": ("button", "btn", "xs", "ghost"),
    "small ghost button (.btn.sm.ghost): Remove": ("button", "btn", "sm", "ghost"),
    "link styled as a button (a.btn)": ("a", "btn", "sm"),
    "chip: From a link, Paste a list, the deck tabs, role chips": ("button", "chip"),
    "active chip": ("button", "chip", "active"),
    "text field": ("input", "input"),
    "select": ("select", "select"),
    "the logo (the way home)": ("button", "brand"),
    "bottom tab bar buttons": ("button",),  # checked through .nav button below
    "colour filter pip (a button)": ("button", "pip", "W"),
    "a row's name that opens a card": ("button", "row-link"),
    "help hint (?)": ("a", "help-hint"),
    "close buttons": ("button", "btn", "xs", "close-x"),
}


def problems() -> list[str]:
    found = []
    for what, (tag, *classes) in CONTROLS.items():
        if what == "bottom tab bar buttons":
            continue
        px, too_small = effective_min_height(tag, *classes)
        if px is None or px < MIN_PX:
            found.append(f"{what}: min-height {px} at {PHONE_WIDTH}px (needs {MIN_PX})")
        found += [f"{what}: {rule}" for rule in too_small]
    return found


def test_every_kind_of_tap_target_is_at_least_36px_at_phone_width():
    assert not problems(), "\n".join(problems())


def test_the_bottom_tab_bar_buttons_are_tall_enough():
    heights = [_px(d.get("min-height", "")) for _, s, d in RULES if s == ".nav button" and "min-height" in d]
    assert heights and min(heights) >= MIN_PX


def test_the_checker_catches_a_small_control():
    """The test is only worth having if it fails on what the audit found: the same chip or button at 34 px."""
    global RULES
    saved = RULES
    try:
        assert problems() == []
        RULES = saved + [(10**6, ".chip", {"min-height": "34px"})]
        assert any(p.startswith("chip") and "34" in p for p in problems())
        RULES = saved + [(10**6, ".btn.xs", {"min-height": "34px"})]
        assert any("extra small" in p for p in problems())
        RULES = saved + [(10**6, ".deck-tabs .chip", {"min-height": "30px"})]  # a rule that only applies inside a container
        assert any(".deck-tabs .chip" in p for p in problems())
    finally:
        RULES = saved


def test_the_phone_rules_are_the_ones_that_raise_the_small_controls():
    """Wider screens keep the design's compact buttons (styles.css is unchanged): the 44 px floor is a phone rule."""
    wide = [d for s, d in ((s, d) for _, s, d in rules(PUBLIC / "styles.css")) if s in (".btn.sm", ".btn.xs", ".chip")]
    assert wide, "styles.css lost its button rules"
    layout = (PUBLIC / "layout.css").read_text(encoding="utf-8")
    assert "@media (max-width: 760px)" in layout and ".btn.sm, .btn.xs { min-height: 44px" in layout


def test_the_phone_pass_keeps_text_at_12_px_and_labels_at_11_px():
    """#95: scripts/measure_phone.js found no text under 12 px and no label under 11 px on any view at 390 px after the
    phone-first block at the end of layout.css; that block must not set a smaller size (SVG chart text scales with its chart)."""
    css = (PUBLIC / "layout.css").read_text(encoding="utf-8")
    block = _strip(css[css.index("Phone-first pass (#95)"):])
    sizes = [float(m) for m in re.findall(r"font-size:\s*([\d.]+)px(?:\s*!important)?\s*[;}]", block)]
    assert sizes and min(sizes) >= 11, sorted(set(sizes))
    assert "min-height: 44px" in block and "button.pip { min-width: 44px; min-height: 44px; }" in block
