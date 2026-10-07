"""Vercel Web Analytics and Speed Insights (the owner asked for both): on every public page and the app, anonymous, with no query
or hash ever sent, skipped for Do Not Track, described in the privacy notice, and built from sources that CI checks."""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
WEB = ROOT / "web"
TAG = '<script src="analytics.bundle.js"></script>'


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_every_public_page_and_the_app_load_the_bundle_exactly_once():
    pages = sorted(PUBLIC.glob("*.html"))
    assert {p.name for p in pages} >= {"index.html", "connect.html", "credits.html", "privacy.html", "support.html", "terms.html"}
    for page in pages:
        assert text(page).count(TAG) == 1, page.name


def test_the_server_rendered_sign_in_and_consent_pages_never_load_it():
    for name in ("oauth_routes.py", "reviewer_routes.py"):
        assert "analytics" not in (ROOT / "vault" / name).read_text(encoding="utf-8").lower(), name


def test_the_bundle_is_built_from_the_current_sources_and_package_versions():
    banner = (PUBLIC / "analytics.bundle.js").read_text(encoding="utf-8")[:200]
    recorded = re.search(r"analytics-sha256: ([0-9a-f]{64})", banner).group(1)
    digest = hashlib.sha256()
    for name in ("analytics/entry.mjs", "analytics/url.mjs"):
        digest.update(name.encode() + b"\0" + text(WEB / name).encode() + b"\0")
    packages = json.loads(text(WEB / "package-lock.json"))["packages"]
    for name in ("@vercel/analytics", "@vercel/speed-insights"):
        digest.update(f"{name}@{packages['node_modules/' + name]['version']}".encode() + b"\0")
    assert recorded == digest.hexdigest(), "public/analytics.bundle.js is stale: run `npm --prefix web run build`"
    bundle = (PUBLIC / "analytics.bundle.js").read_text(encoding="utf-8")
    assert "speed-insights" in bundle and "beforeSend" in bundle


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")
def test_only_the_path_is_ever_sent_and_do_not_track_or_global_privacy_control_turn_it_off():
    script = """
import { cleanUrl, measured } from %s;
const out = {
  clean: [cleanUrl('https://mtgvault.cards/?invite=SECRET#/decks/12'), cleanUrl('https://mtgvault.cards/privacy.html?x=1'), cleanUrl('not a url')],
  measured: [measured(null), measured({}), measured({ doNotTrack: '1' }), measured({ doNotTrack: 'yes' }), measured({ doNotTrack: '0' }), measured({ globalPrivacyControl: true })],
};
console.log(JSON.stringify(out));
""" % json.dumps((WEB / "analytics" / "url.mjs").as_uri())
    done = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True, timeout=30)
    result = json.loads(done.stdout)
    assert result["clean"] == ["https://mtgvault.cards/", "https://mtgvault.cards/privacy.html", ""]
    assert "SECRET" not in done.stdout
    assert result["measured"] == [True, True, False, False, True, False]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node (CI has it)")
def test_speed_insights_routes_are_view_names_from_a_fixed_list_and_never_an_id_or_a_token():
    """#285: every event was filed under 'Unknown'. The route is the app's view name only; nothing typed or secret can become one."""
    script = """
import { routeOf, VIEWS } from %s;
const at = (pathname, hash) => routeOf({ pathname, hash });
console.log(JSON.stringify({
  views: VIEWS.map((v) => at('/', '#/' + v)),
  none: [at('/', ''), at('/', '#'), at('/', '#/'), at('/', undefined)],
  withArg: [at('/', '#/decks/12'), at('/', '#/sets/MKM'), at('/', '#/help/connect'), at('/', '#/browse?q=sol')],
  hostile: [at('/', '#/invite=SECRET'), at('/', '#/<script>alert(1)</script>'), at('/', '#/share/TOKEN123'), at('/', '#/dashboardX'), at('/', '#/Browse')],
  pages: [at('/credits.html', '#/decks/1'), at('/privacy.html', ''), routeOf(null)],
}));
""" % json.dumps((WEB / "analytics" / "url.mjs").as_uri())
    done = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True, timeout=30)
    result = json.loads(done.stdout)
    assert result["views"] == ["/dashboard", "/browse", "/sets", "/decks", "/lab", "/graph", "/valuation", "/help"]
    assert result["none"] == ["/", "/", "/", "/"]
    assert result["withArg"] == ["/decks", "/sets", "/help", "/browse"]  # the argument after the view is dropped
    assert result["hostile"] == ["/", "/", "/", "/", "/"]  # not on the list: the plain landing route
    assert result["pages"] == ["/credits.html", "/privacy.html", "/"]
    for leaked in ("SECRET", "TOKEN", "script", "MKM", "12"):
        assert leaked not in done.stdout.replace('"/dashboard"', ""), leaked


def test_the_entry_point_gives_speed_insights_a_route_and_follows_the_hash():
    entry = text(WEB / "analytics" / "entry.mjs")
    assert "route: routeOf(location)" in entry and "hashchange" in entry and "setRoute" in entry


def test_the_entry_point_redacts_both_scripts_and_the_privacy_notice_says_what_is_collected():
    entry = text(WEB / "analytics" / "entry.mjs")
    assert entry.count("beforeSend") == 2 and "cleanUrl" in entry and "measured(" in entry
    privacy = " ".join(re.sub(r"<[^>]+>", " ", text(PUBLIC / "privacy.html")).split()).lower()
    for needed in ("web analytics", "speed insights", "cookieless", "do not track", "global privacy control", "no query, no hash",
                   "sign-in and consent pages never load"):
        assert needed in privacy, needed


def test_speed_insights_is_loaded_once_by_the_bundle_and_never_by_a_second_tag():
    """The Vercel bot's own install (PR #271) added a plain Speed Insights tag to index.html next to our bundle: every page view would
    have been measured twice, the second time without the path-only URL and the Do Not Track check."""
    for page in sorted(PUBLIC.glob("*.html")):
        html = text(page)
        assert "/_vercel/speed-insights/script.js" not in html and "window.si" not in html, page.name
        assert "/_vercel/insights/script.js" not in html, page.name
