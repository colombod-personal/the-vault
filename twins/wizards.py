"""Twin of where Wizards of the Coast publishes the Comprehensive Rules: the rules page on ``magic.wizards.com``
links the current edition's TXT on ``media.wizards.com`` (as the real page does, checked 2026-10-05: the link is in
the server HTML and the file name carries the edition date). :meth:`publish` sets the edition being served.

What the real page looks like (read 2026-10-07, ``tests/conformance``): three links in this order, DOCX, PDF, TXT, each an
``<a class="cta">`` whose address has a literal space (``MagicCompRules 20260925.txt``) and no date anywhere else on the page;
the TXT answers ``text/plain`` with an ``ETag``, a ``Last-Modified`` and a ``Content-Length``, and may be published before the
date it is effective from (its ``Last-Modified`` was weeks earlier than its "effective as of" line).

Earlier editions stay on the CDN under their dated names, in the folder of their year, after the page links a newer one
(checked 2026-10-09: ``MagicCompRules 20260819.txt`` answers 200 beside the current ``20260925``; a name that was never
published answers 404 with no content type). The page links only the current edition and no archive. The date in a file name
is the day it was published, not the day it takes effect (``20260819`` says "effective as of August 7, 2026").

**Prerelease pages (vault-064.10.8, read 2026-10-12, ``tests/conformance``).** The same twin also answers for the two sitemaps and the
prerelease pages ``jobs/sync_prerelease_guides.py`` reads, each as the real host does:

* ``magic.wizards.com``: ``/robots.txt`` (``User-agent: *`` with no ``Disallow``, and a ``Sitemap:`` line per language), ``/en/sitemap.xml`` (a
  ``urlset`` of ``<url><loc>..</loc><lastmod>..</lastmod></url>``), and articles under ``/en/news/feature/<slug>`` whose slugs are not uniform
  (``spider-man-prerelease-guide``, ``the-guide-to-your-first-prerelease-with-aetherdrift``);
* ``wpn.wizards.com``: a path without the language answers 302 (or 307) with a ``Location`` header to the ``/en`` path and a small HTML
  body that says the same by ``<meta http-equiv="refresh">``; ``/sitemap.xml`` goes to ``/sitemap_index.xml``, whose entries name WPN's *origin* host
  (``wpn.web-prod.tiamat-origin.cloud``, which the Vault never contacts), and the same paths on ``wpn.wizards.com`` answer 200:
  ``/__sitemap__/en/sitemap.xml`` and ``/en/event/prerelease-<slug>``. There is no robots file (``/en/robots.txt`` is a 404 page).

The pages are invented, in the shape the real ones have where the Vault reads: a ``<title>``, a list that follows "Prerelease Pack contains
the following", a "Scheduling : ..." line and the sentence about the days a Prerelease can be scheduled."""

from __future__ import annotations

import hashlib
import re
from datetime import date

import httpx

from .base import Request, Twin, html_response

PAGE_HOST, MEDIA_HOST = "magic.wizards.com", "media.wizards.com"
WPN_HOST, WPN_ORIGIN = "wpn.wizards.com", "wpn.web-prod.tiamat-origin.cloud"


class WizardsTwin(Twin):
    name = "wizards"
    hosts = (PAGE_HOST, MEDIA_HOST, WPN_HOST)

    def __init__(self):
        super().__init__()
        self.files: dict[str, str] = {}  # every edition on the CDN, by the date in its file name
        self.edition = "20270303"  # the one the page links
        self.route("GET", PAGE_HOST, "/en/rules", self._page)
        self.route("GET", MEDIA_HOST, "/{year}/downloads/{name}", self._file)
        self.guides: dict[str, dict] = {}  # article slug -> {"title", "html", "listed", "section"}
        self.events: dict[str, dict] = {}  # WPN event slug -> {"title", "html", "listed"}
        self.route("GET", PAGE_HOST, "/robots.txt", self._robots)
        self.route("GET", PAGE_HOST, "/en/sitemap.xml", self._magic_sitemap)
        self.route("GET", PAGE_HOST, "/en/news/{section}/{slug}", self._article)
        self.route("GET", WPN_HOST, "/robots.txt", lambda r: _refresh("/en/robots.txt", 302))
        self.route("GET", WPN_HOST, "/en/robots.txt", lambda r: html_response("<html><body>Page not found</body></html>", 404))
        self.route("GET", WPN_HOST, "/sitemap.xml", lambda r: _refresh("/sitemap_index.xml", 307))
        self.route("GET", WPN_HOST, "/sitemap_index.xml", self._wpn_index)
        self.route("GET", WPN_HOST, "/__sitemap__/en/sitemap.xml", self._wpn_sitemap)
        self.route("GET", WPN_HOST, "/en/event/{slug}", self._event)
        self.route("GET", WPN_HOST, "/event/{slug}", lambda r: _refresh("/en/event/" + r.params["slug"], 302))

    def publish(self, text: str, edition: str = "20270303", listed: bool = True) -> None:
        """Put ``text`` on the CDN under the file name dated ``edition``. ``listed`` (the default) also makes the page link it as the
        current edition; an earlier edition stays served, as Wizards' CDN does, whether or not the page still links it."""
        self.files[edition] = text
        if listed:
            self.edition = edition

    @property
    def text(self) -> str:
        return self.files.get(self.edition, "")

    def reset(self) -> None:
        super().reset()
        self.files = {}
        self.guides, self.events = {}, {}

    # -- prerelease pages (vault-064.10.8) -------------------------------------------------------
    def publish_guide(self, slug: str, title: str, *, play_boosters: int = 6, other_boosters: int = 0, listed: bool = True,
                      section: str = "feature", contents: bool = True) -> str:
        """A player guide article. ``listed`` puts it in the sitemap; ``contents=False`` leaves out the list of what a pack holds
        (a page the Vault cannot read the contents of). Returns its address."""
        pack = ""
        if contents:
            others = f"<li>{other_boosters} themed booster</li>" if other_boosters else ""
            pack = (f"<p>When you register you will receive a Prerelease Pack. Each Prerelease Pack contains the following:</p>"
                    f"<ul><li>{play_boosters} Play Boosters</li>{others}<li>1 Traditional foil promo card</li><li>1 Deck box</li>"
                    f"<li>1 Spindown die</li></ul>")
        body = f"<h1>{title}</h1><p>An invented guide for the twin.</p>{pack}<p>Build a 40-card deck.</p>"
        self.guides[slug] = {"title": title, "html": body, "listed": listed, "section": section}
        return f"https://{PAGE_HOST}/en/news/{section}/{slug}"

    def publish_event(self, slug: str, title: str, start: date | None, end: date | None, *, two_headed_giant: tuple[int, int] | None = None,
                      listed: bool = True, style: str = "both") -> str:
        """A WPN event page. ``style`` is how the dates are written: "scheduling" (the "Scheduling : ..." line), "sentence" (the
        "can be scheduled starting ... until ..." sentence), "both", or "none". ``two_headed_giant`` is (Prerelease Packs, Play
        Boosters) per team. Returns its address."""
        parts = [f"<h1>{title}</h1>"]
        if start and end and style in ("scheduling", "both"):
            span = (f"{start:%B} {start.day}\u2013{end:%B} {end.day}" if start.month != end.month else f"{start:%B} {start.day}\u2013{end.day}")
            parts.append(f"<p>Scheduling : {span}; use the Prerelease template Format : Sealed Deck</p>")
        if start and end and style in ("sentence", "both"):
            parts.append(f"<p>Prerelease events can be scheduled starting at 12 p.m. local time on {start:%A}, {start:%B} {start.day}, and "
                         f"throughout the week until {'' if start.month == end.month else end.strftime('%B') + ' '}{end.day}.</p>")
        if two_headed_giant:
            packs, loose = two_headed_giant
            said = ("one Prerelease Pack plus two Play Boosters" if (packs, loose) == (1, 2) else "two Prerelease Packs")
            parts.append(f"<p>Each team receives {said} to build their combined pool.</p>")
        self.events[slug] = {"title": title, "html": "".join(parts), "listed": listed}
        return f"https://{WPN_HOST}/en/event/{slug}"

    def _robots(self, req: Request) -> httpx.Response:
        sitemaps = "\n".join(f"Sitemap: https://{PAGE_HOST}/{lang}/sitemap.xml" for lang in ("en", "fr", "de", "ja"))
        return httpx.Response(200, content=f"User-agent: *\n{sitemaps}\n".encode(), headers={"content-type": "text/plain; charset=utf-8"})

    @staticmethod
    def _urlset(urls: list[tuple[str, str]]) -> httpx.Response:
        body = "".join(f"<url><loc>{loc}</loc><lastmod>{mod}</lastmod></url>" for loc, mod in urls)
        xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'
        return httpx.Response(200, content=xml.encode(), headers={"content-type": "application/xml"})

    def _magic_sitemap(self, req: Request) -> httpx.Response:
        urls = [(f"https://{PAGE_HOST}/en/play-events/prerelease", "2026-10-01T23:01:09.501Z"),
                (f"https://{PAGE_HOST}/en/rules", "2026-09-25T12:00:00.000Z")]
        urls += [(f"https://{PAGE_HOST}/en/news/{g['section']}/{slug}", "2026-09-18T21:00:13.691Z")
                 for slug, g in self.guides.items() if g["listed"]]
        return self._urlset(urls)

    def _article(self, req: Request) -> httpx.Response:
        page = self.guides.get(req.params["slug"])
        if page is None or page["section"] != req.params["section"]:
            return html_response("<html><head><title>Page not found | Magic: The Gathering</title></head></html>", 404)
        return html_response(f"<html><head><title>{page['title']}</title></head><body>{page['html']}</body></html>")

    def _wpn_index(self, req: Request) -> httpx.Response:
        xml = ('<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               f"<sitemap><loc>https://{WPN_ORIGIN}/__sitemap__/en/sitemap.xml</loc></sitemap>"
               f"<sitemap><loc>https://{WPN_ORIGIN}/__sitemap__/fr/sitemap.xml</loc></sitemap></sitemapindex>")
        return httpx.Response(200, content=xml.encode(), headers={"content-type": "text/xml; charset=UTF-8"})

    def _wpn_sitemap(self, req: Request) -> httpx.Response:
        urls = [(f"https://{WPN_ORIGIN}/en/news/example-news-item", "2026-09-01T00:00:00.000Z")]
        urls += [(f"https://{WPN_ORIGIN}/en/event/{slug}", "2026-09-01T00:00:00.000Z") for slug, e in self.events.items() if e["listed"]]
        return self._urlset(urls)

    def _event(self, req: Request) -> httpx.Response:
        page = self.events.get(req.params["slug"])
        if page is None:
            return html_response("<html><head><title>Page not found | WPN</title></head></html>", 404)
        return html_response(f"<html><head><title>{page['title']}</title></head><body>{page['html']}</body></html>")

    @property
    def url(self) -> str:
        return f"https://{MEDIA_HOST}/{self.edition[:4]}/downloads/MagicCompRules%20{self.edition}.txt"

    def _page(self, req: Request) -> httpx.Response:
        base = self.url[:-4].replace("%20", " ")
        return html_response(f"""<h2>Comprehensive Rules</h2>

<p><a class="cta" href="{base}.docx"><span class="txt">DOCX</span></a></p>

<p><a class="cta" href="{base}.pdf" target="_blank"><span class="txt">PDF</span> </a></p>

<p><span class="txt"><a class="cta" href="{base}.txt" target="_blank"><span class="txt">TXT</span></a></span></p>""")

    def _file(self, req: Request) -> httpx.Response:
        named = re.fullmatch(r"MagicCompRules[ %20]+(\d{8})\.txt", req.params["name"])
        text = self.files.get(named.group(1)) if named and req.params["year"] == named.group(1)[:4] else None
        if not text:
            return httpx.Response(404, text="not found")
        body = text.encode("utf-8")
        return httpx.Response(200, content=body, headers={
            "content-type": "text/plain", "accept-ranges": "bytes",
            "etag": '"' + hashlib.md5(body).hexdigest() + '"', "last-modified": "Mon, 17 Aug 2026 16:18:07 GMT"})


def _refresh(path: str, status: int) -> httpx.Response:
    """How wpn.wizards.com sends a path without the language: a 302 or 307 with a ``Location`` header and an HTML body whose meta tag says
    the same (checked 2026-10-12 against the live site: the header is there, the body is the 100-odd bytes below)."""
    return httpx.Response(status, content=f'<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0; url={path}"></head></html>'.encode(),
                          headers={"content-type": "text/html", "location": path})
