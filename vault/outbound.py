"""Where the vault's outbound HTTP calls go.

In production they go to the real services. With ``VAULT_TWINS_URL`` set (local development
only; refused on a public deployment) they go to the digital twin universe (``python -m twins``).
Every call to ``https://<host>/<path>`` becomes ``<VAULT_TWINS_URL>/h/<host>/<path>``, like
DNS pointing the real host names at the twins. Sign-in redirects the vault sends to browsers
are rewritten the same way (:func:`browser_url`).
"""

from __future__ import annotations

import httpx


class TwinRouting(httpx.BaseTransport, httpx.AsyncBaseTransport):
    """An httpx transport, sync and async, that sends every request to the twin server."""

    def __init__(self, twins_url: str):
        self.base = httpx.URL(twins_url.rstrip("/"))
        self._sync = httpx.HTTPTransport()
        self._async = httpx.AsyncHTTPTransport()

    def _rewrite(self, request: httpx.Request) -> httpx.Request:
        url = request.url
        target = self.base.copy_with(raw_path=self.base.raw_path.rstrip(b"/") + b"/h/" + url.host.encode() + url.raw_path)
        headers = httpx.Headers(request.headers)
        headers["host"] = target.netloc.decode()
        return httpx.Request(request.method, target, headers=headers, content=request.content,
                             extensions=request.extensions)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        return self._sync.handle_request(self._rewrite(request))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        return await self._async.handle_async_request(self._rewrite(request))


def transport(settings) -> TwinRouting | None:
    return TwinRouting(settings.twins_url) if settings.twins_url else None


def browser_url(settings, url: str) -> str:
    if not settings.twins_url:
        return url
    u = httpx.URL(url)
    return f"{settings.twins_url.rstrip('/')}/h/{u.host}{u.raw_path.decode()}"
