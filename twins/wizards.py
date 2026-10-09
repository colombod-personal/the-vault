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
is the day it was published, not the day it takes effect (``20260819`` says "effective as of August 7, 2026")."""

from __future__ import annotations

import hashlib
import re

import httpx

from .base import Request, Twin, html_response

PAGE_HOST, MEDIA_HOST = "magic.wizards.com", "media.wizards.com"


class WizardsTwin(Twin):
    name = "wizards"
    hosts = (PAGE_HOST, MEDIA_HOST)

    def __init__(self):
        super().__init__()
        self.files: dict[str, str] = {}  # every edition on the CDN, by the date in its file name
        self.edition = "20270303"  # the one the page links
        self.route("GET", PAGE_HOST, "/en/rules", self._page)
        self.route("GET", MEDIA_HOST, "/{year}/downloads/{name}", self._file)

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
