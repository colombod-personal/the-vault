"""The committed front-end bundle (public/app.bundle.js) is built from the current JSX.

Run ``npm --prefix web ci && npm --prefix web run build`` after changing any public/*.jsx.
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
BUNDLE = PUBLIC / "app.bundle.js"


def sources() -> list[str]:
    block = re.search(r"export const SOURCES = \[(.*?)\];", (ROOT / "web" / "build.mjs").read_text(), re.S)
    return re.findall(r"'([^']+)'", block.group(1))


def test_bundle_is_built_from_the_current_sources():
    header = BUNDLE.read_text(encoding="utf-8")[:2000]
    digest = hashlib.sha256()
    for name in sources():
        digest.update(name.encode() + b"\0" + (PUBLIC / name).read_text(encoding="utf-8").encode() + b"\0")
    recorded = re.search(r"sources-sha256: ([0-9a-f]{64})", header).group(1)
    assert recorded == digest.hexdigest(), "public/app.bundle.js is stale: run `npm --prefix web run build`"


def test_every_view_is_in_the_bundle_and_nothing_compiles_in_the_browser():
    jsx = sorted(str(p.relative_to(PUBLIC)) for p in PUBLIC.rglob("*.jsx"))
    assert sorted(sources()) == jsx
    html = (PUBLIC / "index.html").read_text()
    assert 'src="app.bundle.js"' in html
    assert "text/babel" not in html and "babel" not in html.lower()
    assert ".development.js" not in html  # React's production build


ICONS = ("favicon.ico", "favicon.svg", "apple-touch-icon.png")


def test_every_page_has_the_site_icon():
    """Browsers ask for /favicon.ico even when a page links none, so it sits at the root of public/
    (the smoke test checks it is served)."""
    for page in PUBLIC.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        for name in ICONS:
            assert (PUBLIC / name).is_file() and f'href="/{name}"' in html, f"{page.name}: {name}"
    assert (PUBLIC / "favicon.ico").read_bytes()[:4] == b"\0\0\1\0"  # an icon file, not a renamed PNG
    assert (PUBLIC / "apple-touch-icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (PUBLIC / "favicon.svg").read_text(encoding="utf-8").startswith("<svg")


def test_the_phone_layout_never_touches_wider_screens():
    """public/mobile.css is the phone layout. Its rules sit inside phone-width media queries, so
    tablets and desktops render exactly as without it; only .m-only (phone-only elements) is
    hidden outside them."""
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert html.index('href="layout.css"') < html.index('href="mobile.css"'), "mobile.css loads last"
    assert "viewport-fit=cover" in html  # the tab bar keeps clear of the home indicator
    css = re.sub(r"/\*.*?\*/", "", (PUBLIC / "mobile.css").read_text(encoding="utf-8"), flags=re.S)
    top_level, depth, start = [], 0, 0
    for i, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                top_level.append(css[start:i].strip())
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                start = i + 1
    assert depth == 0
    outside = [s for s in top_level if not re.fullmatch(r"@media \((min-width: \d+px\) and \()?max-width: 760px\)", s)]
    assert outside == [".m-only"], outside
