"""Check the links of the player guide (bead vault-30s.3.2). Standard library only, so CI runs it before any install.

    python scripts/check_site_links.py            # build docs/site into a temporary folder, then check that
    python scripts/check_site_links.py _site      # check a folder that scripts/build_site.py already built
    python scripts/check_site_links.py --public public   # the folder the Vault's own pages live in (default: public/)

It fails (exit 1, one line per problem) on:

- an internal link, image, stylesheet (HTML ``<link>``, ``url(...)`` in CSS, ``srcset``, video/audio) whose file is not in the
  site, or an ``#anchor`` that is not on its page;
- a link to https://mtgvault.cards that is not a page the Vault serves: the path must be written exactly as the file is named in
  ``public/`` (``/connect.html``, not ``/connect.html/`` or ``//connect.html``), or be one of the few routes the app answers
  (ROUTES), and an ``#anchor`` on a ``public/`` page must exist there too;
- an image (a screenshot) that is missing, has no ``alt`` text, or is loaded from another site;
- a root-relative link (``/page.html``: the guide is served under a project path on GitHub Pages, so it would leave the guide),
  a protocol-relative one, ``http://``, ``javascript:`` and ``data:``;
- ``mailto:`` and ``tel:`` (the guide carries no e-mail address or phone number: reports go through the Vault's support page);
- anything *loaded* (image, stylesheet, srcset, video, CSS ``url()``) from another site or from the Vault's own domain: the guide
  makes no request to another origin, only an ``<a>`` link may point there; an ``https://`` address with no host name;
- ``<form>`` and its fields (a guide collects nothing);
- things a plain guide has no use for and that can load or run something: ``<base>``, ``<iframe>``, ``<object>``, ``<embed>``,
  ``<meta http-equiv="refresh">``, an inline ``<script>``, an ``on...=`` event handler, ``@import`` in CSS.

Links to other sites are not fetched (CI must not depend on the network); they only have to be ``https://``, and only an ``<a>``
may point at another site: nothing is loaded from one.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

MTGVAULT_HOST = "mtgvault.cards"
# Answered by the app, not a file in public/. The Vault's single-page app answers "/" (its own #/ routes are not checked).
ROUTES = {"", "/", "/api/mcp"}
# What a guide may point at in public/: pages, text and media, never the app's scripts, styles or sources.
PAGE_SUFFIXES = {".html", ".txt", ".md", ".jpg", ".jpeg", ".png", ".svg", ".ico", ".mp4"}
LINK_ATTRS = {("a", "href"), ("area", "href"), ("link", "href"), ("img", "src"), ("script", "src"), ("source", "src"),
              ("video", "src"), ("video", "poster"), ("audio", "src"), ("track", "src")}
FORBIDDEN_TAGS = {"base": "<base> changes where every link goes", "iframe": "a guide does not embed other pages",
                  "object": "a guide does not embed objects", "embed": "a guide does not embed objects", "frame": "no frames",
                  "form": "a guide collects nothing (a form would send what is typed to another address)",
                  "input": "a guide collects nothing", "textarea": "a guide collects nothing", "select": "a guide collects nothing"}
CSS_URL = re.compile(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)\s]*))\s*\)", re.IGNORECASE)
CSS_IMPORT = re.compile(r"@import\b", re.IGNORECASE)


class Scan(HTMLParser):
    """The links of a page, the anchors it offers, and the constructs a guide may not use."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str, int]] = []  # tag, attribute, value, line
        self.ids: set[str] = set()
        self.problems: list[tuple[int, str]] = []
        self._style = False
        self._style_line = 0
        self._style_text: list[str] = []

    def css(self, text: str, line: int) -> None:
        if CSS_IMPORT.search(text):
            self.problems.append((line, "@import in CSS is not allowed: it loads another file the checker cannot follow"))
        for match in CSS_URL.finditer(text):
            self.links.append(("style", "url", next(g for g in match.groups() if g is not None).strip(), line))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: (value or "") for name, value in attrs}
        line = self.getpos()[0]
        for key in ("id", "name"):
            if values.get(key):
                self.ids.add(values[key])
        for attribute in values:
            if attribute.startswith("on"):
                self.problems.append((line, f"the event handler {attribute}= is not allowed: a guide runs no script"))
        for attribute in ("href", "src", "poster", "action", "data"):
            if (tag, attribute) in LINK_ATTRS and attribute in values:
                self.links.append((tag, attribute, values[attribute], line))
        if tag in ("img", "source") and values.get("srcset"):
            for candidate in values["srcset"].split(","):
                parts = candidate.split()
                if parts:
                    self.links.append((tag, "srcset", parts[0], line))
        if tag == "img" and "alt" not in values:
            self.problems.append((line, 'an image needs an alt attribute (alt="" only for a decoration)'))
        if tag in FORBIDDEN_TAGS:
            self.problems.append((line, f"<{tag}> is not allowed: {FORBIDDEN_TAGS[tag]}"))
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.problems.append((line, 'a <meta http-equiv="refresh"> redirect is not allowed'))
        if tag == "script" and not values.get("src"):
            self.problems.append((line, "an inline <script> is not allowed: a guide runs no script"))
        if values.get("style"):
            self.css(values["style"], line)
        if tag == "style":
            self._style, self._style_line, self._style_text = True, line, []

    def handle_data(self, data: str) -> None:
        if self._style:
            self._style_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self._style:
            self.css("".join(self._style_text), self._style_line)
            self._style = False

    def close(self) -> None:
        super().close()
        if self._style:  # a <style> that is never closed still runs to the end of the page in a browser: check what it holds
            self.css("".join(self._style_text), self._style_line)
            self._style = False


def scan(path: Path) -> Scan:
    parser = Scan()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


class Checker:
    def __init__(self, site: Path, public: Path) -> None:
        self.site, self.public = site.resolve(), public.resolve()
        self.problems: list[str] = []
        self.counts = {"pages": 0, "internal": 0, "mtgvault.cards": 0, "external": 0}
        self._ids: dict[Path, set[str]] = {}

    def ids_of(self, path: Path) -> set[str]:
        if path not in self._ids:
            self._ids[path] = scan(path).ids
        return self._ids[path]

    def problem(self, page: str, line: int, message: str) -> None:
        self.problems.append(f"{page}:{line}: {message}")

    def run(self) -> list[str]:
        for path in sorted(self.site.rglob("*.html")):
            self.counts["pages"] += 1
            page = path.relative_to(self.site).as_posix()
            result = scan(path)
            for line, message in result.problems:
                self.problem(page, line, message)
            for tag, attribute, value, line in result.links:
                self.check(page, line, tag, attribute, value.strip(), result.ids)
        for path in sorted(self.site.rglob("*.css")):
            page = path.relative_to(self.site).as_posix()
            sheet = Scan()
            sheet.css(path.read_text(encoding="utf-8"), 1)
            for line, message in sheet.problems:
                self.problem(page, line, message)
            for tag, attribute, value, line in sheet.links:
                self.check(page, line, tag, attribute, value.strip(), set())
        return self.problems

    def check(self, page: str, line: int, tag: str, attribute: str, url: str, own_ids: set[str]) -> None:
        loaded = tag == "link" or attribute in ("src", "poster", "srcset", "url", "data")
        what = "image" if tag == "img" else "link"
        if not url:
            return self.problem(page, line, f"empty {what}")
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme in ("mailto", "tel"):
            return self.problem(page, line, f"{scheme}: links are not allowed: the guide carries no e-mail address or phone number "
                                "(send people to https://mtgvault.cards/support.html)")
        if scheme in ("javascript", "data"):
            return self.problem(page, line, f"{scheme}: {what}s are not allowed: {url[:40]}")
        if scheme == "http":
            return self.problem(page, line, f"use https:// instead of http://: {url}")
        if scheme == "https":
            return self.check_absolute(page, line, loaded, parts, url)
        if scheme:
            return self.problem(page, line, f"unsupported link scheme {scheme}: {url}")
        if parts.netloc:
            return self.problem(page, line, f"a protocol-relative link (//...) is not allowed, write the full https:// address: {url}")
        self.counts["internal"] += 1
        path = unquote(parts.path)
        if path.startswith("/") or parts.path.startswith("/"):
            return self.problem(
                page, line, f"root-relative {what} {url}: the guide is served under a project path on GitHub Pages, so a link that "
                "starts with / leaves the guide. Use a relative link (page.html), or the full https://mtgvault.cards/... address")
        if "\\" in path or "\x00" in path:
            return self.problem(page, line, f"{what} {url} has a backslash or a NUL in its path")
        if not path:  # "#anchor": this page
            if parts.fragment and parts.fragment not in own_ids:
                self.problem(page, line, f"the anchor #{parts.fragment} is not on this page")
            return None
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(page), path))
        if resolved.startswith("../") or resolved == ".." or resolved.startswith("/"):
            return self.problem(page, line, f"{what} {url} points outside the site")
        target = self.site / resolved
        if path.endswith("/") or target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            kind = "screenshot or image" if tag == "img" else "page or file"
            return self.problem(page, line, f"broken internal {what}: {url} (no such {kind} in the site)")
        real = target.resolve()
        if self.site != real and self.site not in real.parents:
            return self.problem(page, line, f"{what} {url} resolves outside the site")
        if parts.fragment and target.suffix == ".html" and parts.fragment not in self.ids_of(target):
            self.problem(page, line, f"the anchor #{parts.fragment} is not on {url.split('#')[0]}")
        return None

    def check_absolute(self, page: str, line: int, loaded: bool, parts, url: str) -> None:
        host = (parts.hostname or "").lower()
        if not host:
            return self.problem(page, line, f"{url} has no host name: write the full address, https://host/path")
        if host == MTGVAULT_HOST or host.endswith("." + MTGVAULT_HOST):
            if loaded:  # before the Vault checks: a file that exists in public/ is still a request to the app's domain
                return self.problem(page, line, f"{url} is loaded from the Vault's own domain: keep images and styles in the guide itself "
                                    "(a page of the guide should make no request to another origin); only a link may point there")
        if host == MTGVAULT_HOST:
            self.counts["mtgvault.cards"] += 1
            return self.check_mtgvault(page, line, parts, url)
        if host.endswith("." + MTGVAULT_HOST):
            return self.problem(page, line, f"{url}: only https://{MTGVAULT_HOST} is a Vault address, this host is not a page the Vault serves")
        self.counts["external"] += 1
        if loaded:
            return self.problem(page, line, f"{url} is loaded from another site: keep images and styles in the guide itself (players' privacy)")
        return None

    def check_mtgvault(self, page: str, line: int, parts, url: str) -> None:
        try:
            port = parts.port
        except ValueError:
            port = -1
        if parts.username is not None or parts.password is not None or port not in (None, 443):
            return self.problem(page, line, f"{url}: write the address as https://{MTGVAULT_HOST}/... with no user name or port")
        path = parts.path
        if path in ROUTES:
            return None  # the app itself: "/" and its #/ routes are not files
        # The path must be exactly the file's name: no //, no trailing /, no ./, no %-escapes (the match is on the raw text).
        relative = path[1:]
        plain = path.startswith("/") and not path.startswith("//") and "%" not in path and "\\" not in path \
            and relative == posixpath.normpath(relative)
        target = (self.public / relative).resolve() if plain else None
        inside = bool(target) and (self.public == target or self.public in target.parents)
        if not plain or not inside or not target.is_file() or target.suffix.lower() not in PAGE_SUFFIXES:
            known = sorted(p.relative_to(self.public).as_posix() for p in self.public.glob("*.html"))
            return self.problem(page, line, f"{url} is not a page the Vault serves (nothing at {path} in public/, written exactly as "
                                f"the file is named; pages: {', '.join(known)})")
        if parts.fragment and target.suffix == ".html" and parts.fragment not in self.ids_of(target):
            self.problem(page, line, f"the anchor #{parts.fragment} is not on {url.split('#')[0]}")
        return None


def check_site(site: Path, public: Path) -> tuple[list[str], dict[str, int]]:
    checker = Checker(site, public)
    return checker.run(), checker.counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("site", nargs="?", help="a built site folder (default: build docs/site into a temporary folder)")
    parser.add_argument("--public", default=str(ROOT / "public"), help="the Vault's own pages (default: public/)")
    args = parser.parse_args(argv)
    if not Path(args.public).is_dir():
        print(f"check_site_links: {args.public} is not a folder", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        if args.site:
            site = Path(args.site)
        else:
            import build_site

            site = Path(tmp) / "_site"
            try:
                build_site.build(build_site.SOURCE, site)
            except build_site.SiteError as exc:
                for problem in exc.problems:
                    print(f"check_site_links: build: {problem}", file=sys.stderr)
                return 1
        if not site.is_dir():
            print(f"check_site_links: {site} is not a folder", file=sys.stderr)
            return 2
        problems, counts = check_site(site, Path(args.public))
    for problem in problems:
        print(f"check_site_links: {problem}", file=sys.stderr)
    summary = (f"{counts['pages']} pages; links: {counts['internal']} internal, {counts['mtgvault.cards']} to {MTGVAULT_HOST}, "
               f"{counts['external']} to other sites (not fetched)")
    if problems:
        print(f"check_site_links: FAILED, {len(problems)} problem(s). {summary}", file=sys.stderr)
        return 1
    print(f"check_site_links: ok. {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
