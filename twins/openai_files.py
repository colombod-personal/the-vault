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

Scenario knobs: :meth:`attach` (a file and the address a chat app would hand over), :meth:`expire`, :meth:`serve_as` (another
``Content-Type``), :meth:`redirect` (a link that redirects) and the base class's ``fail_next`` and ``outage``.
"""

from __future__ import annotations

import hashlib
import time

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
        self.route("GET", HOST, "/{file_id}", self._file)

    def reset(self) -> None:
        super().reset()
        self.files.clear()

    def attach(self, content: bytes, *, file_name: str = "my_cards.csv", mime_type: str = "text/csv", lifetime: float = 300.0) -> dict:
        """A file the person attached: returns what ChatGPT hands the tool (``download_url``, ``file_id``, ``mime_type``,
        ``file_name``). The link is valid for ``lifetime`` seconds (five minutes, as documented for Actions)."""
        file_id = "file-" + hashlib.sha256(content + file_name.encode()).hexdigest()[:22]
        sig = hashlib.sha256(b"twin-sig:" + file_id.encode()).hexdigest()[:32]
        self.files[file_id] = {"content": content, "mime": mime_type, "name": file_name, "sig": sig,
                               "expires": time.time() + lifetime, "serve_as": mime_type, "redirect": None}
        url = f"https://{HOST}/{file_id}?se=2026-10-12T10%3A00%3A00Z&sp=r&sig={sig}"
        return {"download_url": url, "file_id": file_id, "mime_type": mime_type, "file_name": file_name}

    def expire(self, file_id: str) -> None:
        self.files[file_id]["expires"] = 0

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
        entry = self.files.get(req.params["file_id"])
        if entry is None or req.query.get("sig") != entry["sig"]:
            return self.error(403, "AuthenticationFailed", "no such file or bad signature")
        if time.time() > entry["expires"]:
            return self.error(403, "AuthenticationFailed", "the link has expired")
        if entry["redirect"]:
            return httpx.Response(302, headers={"location": entry["redirect"]})
        body: bytes = entry["content"]
        return httpx.Response(200, content=body, headers={
            "content-type": entry["serve_as"], "content-length": str(len(body)),
            "content-disposition": f'attachment; filename="{entry["name"]}"', "cache-control": "private, max-age=300"})
