"""Check the links of the player guide (bead vault-30s.3.2). Standard library only, so CI runs it before any install.

    python scripts/check_site_links.py            # build docs/site into a temporary folder, then check that
    python scripts/check_site_links.py _site      # check a folder that scripts/build_site.py already built
    python scripts/check_site_links.py --public public   # the folder the Vault's own pages live in (default: public/)

It fails (exit 1, one line per problem) on:

- an internal link, image, script or stylesheet whose file is not in the site, or an ``#anchor`` that is not on its page;
- a link to https://mtgvault.cards that is not a page the Vault serves: the file must exist in ``public/`` (or be one of the
  few routes the app answers, ROUTES), and an ``#anchor`` on a ``public/`` page must exist there too;
- an image (a screenshot) that is missing, has no ``alt`` text, or is loaded from another site;
- a link that is root-relative (``/page.html``: the guide is served under a project path on GitHub Pages, so it would
  leave the guide), protocol-relative, ``http://``, or ``javascript:``.

Links to other sites are not fetched (CI must not depend on the network); they only have to be ``https://``.
"""

from __future__ import annotations

import argparse
import posixpath
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
LINK_ATTRS = {("a", "href"), ("link", "href"), ("img", "src"), ("script", "src"), ("source", "src"), ("video", "poster"),
              ("iframe", "src"), ("form", "action")}
LOADED = {("link", "href"), ("img", "src"), ("script", "src"), ("source", "src"), ("video", "poster"), ("iframe", "src")}


class Scan(HTMLParser):
    """The links of a page and the anchors it offers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str, int]] = []  # tag, attribute, value, line
        self.ids: set[str] = set()
        self.images_without_alt: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: (value or "") for name, value in attrs}
        for key in ("id", "name"):
            if values.get(key):
                self.ids.add(values[key])
        for attribute in ("href", "src", "poster", "action"):
            if (tag, attribute) in LINK_ATTRS and attribute in values:
                self.links.append((tag, attribute, values[attribute], self.getpos()[0]))
        if tag == "img" and "alt" not in values:
            self.images_without_alt.append(self.getpos()[0])


def scan(path: Path) -> Scan:
    parser = Scan()
    parser.feed(path.read_text(encoding="utf-8"))
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
            for line in result.images_without_alt:
                self.problem(page, line, "an image needs an alt attribute (alt=\"\" only for a decoration)")
            for tag, attribute, value, line in result.links:
                self.check(page, line, tag, attribute, value.strip(), result.ids)
        return self.problems

    def check(self, page: str, line: int, tag: str, attribute: str, url: str, own_ids: set[str]) -> None:
        loaded = (tag, attribute) in LOADED
        what = "image" if tag == "img" else "link"
        if not url:
            return self.problem(page, line, f"empty {what}")
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme in ("mailto", "tel"):
            return None
        if scheme == "javascript" or scheme == "data":
            return self.problem(page, line, f"{scheme}: {what}s are not allowed: {url[:40]}")
        if scheme == "http":
            return self.problem(page, line, f"use https:// instead of http://: {url}")
        if scheme == "https":
            return self.check_absolute(page, line, tag, loaded, parts, url)
        if scheme:
            return self.problem(page, line, f"unsupported link scheme {scheme}: {url}")
        if parts.netloc:
            return self.problem(page, line, f"a protocol-relative link (//...) is not allowed, write the full https:// address: {url}")
        self.counts["internal"] += 1
        if parts.path.startswith("/"):
            return self.problem(
                page, line, f"root-relative {what} {url}: the guide is served under a project path on GitHub Pages, so a link that "
                "starts with / leaves the guide. Use a relative link (page.html), or the full https://mtgvault.cards/... address")
        if not parts.path:  # "#anchor": this page
            if parts.fragment and parts.fragment not in own_ids:
                self.problem(page, line, f"the anchor #{parts.fragment} is not on this page")
            return None
        base = posixpath.dirname(page)
        resolved = posixpath.normpath(posixpath.join(base, unquote(parts.path)))
        if resolved.startswith("../") or resolved == "..":
            return self.problem(page, line, f"{what} {url} points outside the site")
        target = self.site / resolved
        if parts.path.endswith("/") or target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            kind = "screenshot or image" if tag == "img" else "page or file"
            return self.problem(page, line, f"broken internal {what}: {url} (no such {kind} in the site)")
        if parts.fragment and target.suffix == ".html" and parts.fragment not in self.ids_of(target):
            self.problem(page, line, f"the anchor #{parts.fragment} is not on {url.split('#')[0]}")
        return None

    def check_absolute(self, page: str, line: int, tag: str, loaded: bool, parts, url: str) -> None:
        host = (parts.hostname or "").lower()
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
        path = unquote(parts.path)
        if path in ROUTES:
            return None  # the app itself: "/" and its #/ routes are not files
        relative = posixpath.normpath(path.lstrip("/"))
        target = (self.public / relative).resolve()
        inside = self.public == target or self.public in target.parents
        if not inside or not target.is_file() or target.suffix.lower() not in PAGE_SUFFIXES:
            known = sorted(p.relative_to(self.public).as_posix() for p in self.public.glob("*.html"))
            return self.problem(page, line, f"{url} is not a page the Vault serves (nothing at {path} in public/; pages: {', '.join(known)})")
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
