"""Twin of the host ChatGPT serves an attached file from, for the tool that takes a file (``vault/file_fetch.py``, vault-0n9.3).

**What this is built from, and what it is not.** The only host name any OpenAI page gives for a file download link is
``files.oaiusercontent.com``, with the file id as the path and signed query parameters, and the link "valid for five minutes"
(developers.openai.com/api/docs/actions/sending-files, read 2026-10-12: GPT Actions, the older mechanism). The Apps SDK reference
for ``_meta["openai/fileParams"]`` (developers.openai.com/plugins/reference, read 2026-10-12) documents ``download_url`` and
``file_id`` but says nothing about its host, lifetime, redirects, size or content type. So this twin is **the documentation's
reading, not a recording**: the answer a real attached file gets (host, status, ``Content-Type``, redirects, expiry) has never been
captured. ``tests/conformance/test_openai_files_live.py`` compares the two by hand once a real, synthetic file's address is
available; until then ``docs/twins.md`` says so. The Vault logs the host and ``Content-Type`` of every real hand-over
(``file_intake``), which is where the first real comparison comes from.

Scenario knobs: :meth:`attach` (a file and the address a chat app would hand over), :meth:`advance` (time passes), :meth:`serve_as` (another
``Content-Type``), :meth:`redirect` (a link that redirects) and the base class's ``fail_next`` and ``outage``.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .base import Request, Twin

HOST = "files.oaiusercontent.com"
ADDRESSES = ["104.18.12.10"]  # a public address, so the Vault's address check passes the way it does for the real host


class OpenAIFilesTwin(Twin):
    name = "openai_files"
    hosts = (HOST,)

    def __init__(self):
        super().__init__()
        self.files: dict[str, dict] = {}
        self.offset = 0.0
        self.route("GET", HOST, "/{file_id}", self._file)

    def reset(self) -> None:
        super().reset()
        self.files.clear()
        self.offset = 0.0

    def attach(self, content: bytes, *, file_name: str = "my_cards.csv", mime_type: str = "text/csv", lifetime: float = 300.0) -> dict:
        """A file the person attached: returns what ChatGPT hands the tool (``download_url``, ``file_id``, ``mime_type``,
        ``file_name``). The link is valid for ``lifetime`` seconds (five minutes, as documented for Actions)."""
        file_id = "file-" + hashlib.sha256(content + file_name.encode()).hexdigest()[:22]
        expires = int(self.now() + lifetime)
        se = datetime.fromtimestamp(expires, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # the expiry the link shows (``se``) is the one the signature covers and the one the twin enforces: one timestamp
        sig = self._sign(file_id, se)
        self.files[file_id] = {"content": content, "mime": mime_type, "name": file_name, "serve_as": mime_type, "redirect": None}
        url = f"https://{HOST}/{file_id}?se={quote(se, safe='')}&sp=r&sig={sig}"
        return {"download_url": url, "file_id": file_id, "mime_type": mime_type, "file_name": file_name}

    def now(self) -> float:
        return time.time() + self.offset

    def advance(self, seconds: float) -> None:
        """Let time pass for this host (a link's five minutes run out) without waiting."""
        self.offset += seconds

    @staticmethod
    def _sign(file_id: str, se: str) -> str:
        return hashlib.sha256(f"twin-sig:{file_id}:{se}".encode()).hexdigest()[:32]

    def serve_as(self, file_id: str, content_type: str) -> None:
        self.files[file_id]["serve_as"] = content_type

    def redirect(self, file_id: str, location: str) -> None:
        self.files[file_id]["redirect"] = location

    def resolve(self, host: str) -> list[str]:
        if host != HOST:
            raise OSError(f"Name or service not known: {host}")
        return list(ADDRESSES)

    def error(self, status: int, code: str, details: str) -> httpx.Response:
        return httpx.Response(status, content=f"<Error><Code>{code}</Code></Error>".encode(),
                              headers={"content-type": "application/xml"})

    def _file(self, req: Request) -> httpx.Response:
        file_id, se = req.params["file_id"], req.query.get("se", "")
        entry = self.files.get(file_id)
        if entry is None or req.query.get("sig") != self._sign(file_id, se):
            return self.error(403, "AuthenticationFailed", "no such file or bad signature")
        try:
            until = datetime.strptime(se, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return self.error(403, "AuthenticationFailed", "bad expiry")
        if self.now() > until:
            return self.error(403, "AuthenticationFailed", "the link has expired")
        if entry["redirect"]:
            return httpx.Response(302, headers={"location": entry["redirect"]})
        body: bytes = entry["content"]
        return httpx.Response(200, content=body, headers={
            "content-type": entry["serve_as"], "content-length": str(len(body)),
            "content-disposition": f'attachment; filename="{entry["name"]}"', "cache-control": "private, max-age=300"})
