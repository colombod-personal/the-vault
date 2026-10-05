"""Twin of where Wizards of the Coast publishes the Comprehensive Rules: the rules page on ``magic.wizards.com``
links the current edition's TXT on ``media.wizards.com`` (as the real page does, checked 2026-10-05: the link is in
the server HTML and the file name carries the edition date). :meth:`publish` sets the edition being served."""

from __future__ import annotations

import httpx

from .base import Request, Twin, html_response

PAGE_HOST, MEDIA_HOST = "magic.wizards.com", "media.wizards.com"


class WizardsTwin(Twin):
    name = "wizards"
    hosts = (PAGE_HOST, MEDIA_HOST)

    def __init__(self):
        super().__init__()
        self.text, self.edition = "", "20270303"
        self.route("GET", PAGE_HOST, "/en/rules", self._page)
        self.route("GET", MEDIA_HOST, "/{year}/downloads/{name}", self._file)

    def publish(self, text: str, edition: str = "20270303") -> None:
        self.text, self.edition = text, edition

    def reset(self) -> None:
        super().reset()
        self.text = ""

    @property
    def url(self) -> str:
        return f"https://{MEDIA_HOST}/{self.edition[:4]}/downloads/MagicCompRules%20{self.edition}.txt"

    def _page(self, req: Request) -> httpx.Response:
        return html_response(f'<a href="{self.url.replace("%20", " ")}">TXT</a> <a href="{self.url[:-4]}.pdf">PDF</a>')

    def _file(self, req: Request) -> httpx.Response:
        if not self.text or self.edition not in str(req.raw.url):
            return httpx.Response(404, text="not found")
        return httpx.Response(200, content=self.text.encode("utf-8"), headers={"content-type": "text/plain"})
