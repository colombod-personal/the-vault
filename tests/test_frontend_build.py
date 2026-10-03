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
