"""Twin of where 17Lands publishes its public data sets: an S3 bucket on ``17lands-public.s3.amazonaws.com``, files under
``/analysis_data/<kind>/<kind>_public.<SET>.<FORMAT>.csv.gz`` with ``<kind>`` one of ``draft_data``, ``game_data`` and
``replay_data`` (the Vault never reads the last).

What the real host does (HEAD requests only, 2026-10-09; no file was downloaded; ``tests/conformance``):
- a file that exists answers 200 with a ``Content-Length``, a ``Last-Modified``, a quoted ``ETag`` and ``Accept-Ranges: bytes``, and
  ``Content-Type: text/csv`` (the body is gzip);
- a file that does not exist answers **403**, not 404 (S3 hides what a bucket holds when it cannot be listed): that is how the Vault
  tells "not published" apart, with a HEAD per set code;
- the page's "Last Updated" date and the file's own ``Last-Modified`` disagree (older sets are refreshed when a new set arrives), so the
  Vault records the file's ``Last-Modified`` and ETag.

:meth:`publish` gzips CSV text; :meth:`publish_raw` serves bytes as they are (a truncated or corrupt file), optionally advertising
another ``Content-Length`` (a file larger than the Vault accepts, without making one)."""

from __future__ import annotations

import gzip
import hashlib
import re
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx

from .base import Request, Twin

HOST = "17lands-public.s3.amazonaws.com"
DENIED = ('<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code><Message>Access Denied</Message>'
          "<RequestId>TWIN</RequestId><HostId>twin</HostId></Error>")


class SeventeenLandsTwin(Twin):
    name = "seventeenlands"
    hosts = (HOST,)

    def __init__(self):
        super().__init__()
        self.files: dict[str, dict] = {}
        self.route("GET", HOST, "/analysis_data/{kind}/{name}", self._file)

    @staticmethod
    def path(kind: str, set_code: str, fmt: str) -> str:
        return f"/analysis_data/{kind}_data/{kind}_data_public.{set_code}.{fmt}.csv.gz"

    def publish(self, kind: str, set_code: str, fmt: str, csv_text: str, *, last_modified: datetime | None = None) -> None:
        """Serve ``csv_text`` (gzipped) as the file of this kind, set and format. Publishing again changes the ETag only if the
        content changed, like an S3 object."""
        self.publish_raw(kind, set_code, fmt, gzip.compress(csv_text.encode("utf-8"), mtime=0), last_modified=last_modified)

    def publish_raw(self, kind: str, set_code: str, fmt: str, body: bytes, *, last_modified: datetime | None = None,
                    advertised_length: int | None = None) -> None:
        self.files[self.path(kind, set_code, fmt)] = {
            "body": body, "etag": '"' + hashlib.md5(body).hexdigest() + '"', "length": advertised_length,
            "modified": last_modified or datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)}

    def unpublish(self, kind: str, set_code: str, fmt: str) -> None:
        self.files.pop(self.path(kind, set_code, fmt), None)

    def reset(self) -> None:
        super().reset()
        self.files.clear()

    def error(self, status: int, code: str, details: str) -> httpx.Response:
        return httpx.Response(status, content=DENIED.encode(), headers={"content-type": "application/xml"})

    def _file(self, req: Request) -> httpx.Response:
        if not re.fullmatch(r"(draft|game|replay)_data_public\.[A-Za-z0-9]+\.[A-Za-z0-9]+\.csv\.gz", req.params["name"]):
            return self.error(403, "AccessDenied", "")
        entry = self.files.get(req.path)
        if entry is None:
            return self.error(403, "AccessDenied", "")  # S3 answers 403 for an object that does not exist
        body: bytes = entry["body"]
        headers = {"content-type": "text/csv", "accept-ranges": "bytes", "etag": entry["etag"], "server": "AmazonS3",
                   "last-modified": format_datetime(entry["modified"], usegmt=True),
                   "content-length": str(entry["length"] if entry["length"] is not None else len(body))}
        if req.method == "HEAD":
            return httpx.Response(200, headers=headers)
        wanted = req.headers.get("range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", wanted)
        if match and (match.group(1) or match.group(2)):
            first = int(match.group(1) or 0)
            last = int(match.group(2)) if match.group(2) else len(body) - 1
            part = body[first:last + 1]
            return httpx.Response(206, content=part, headers={**headers, "content-length": str(len(part)),
                                                              "content-range": f"bytes {first}-{first + len(part) - 1}/{len(body)}"})
        return httpx.Response(200, content=body, headers={**headers, "content-length": str(len(body))})
