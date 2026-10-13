"""Does the file-host twin (twins/openai_files.py) behave like the host ChatGPT serves an attached file from? By hand only, and not
run yet.

The twin was written from OpenAI's documentation (read 2026-10-12): ``files.oaiusercontent.com``, a signed link "valid for five
minutes" (GPT Actions). The Apps SDK reference documents ``download_url`` for ``_meta["openai/fileParams"]`` but not its host,
lifetime, redirects or content type, and no real address has been captured: the Vault's ``file_intake`` log line (host, content type,
status) from the first real run in the ChatGPT app is the first evidence. When someone has a real, SYNTHETIC file's address (attach a
file made up for the purpose, copy the address the tool received; never a person's file), run once and fix the twin where this fails:

    OPENAI_FILE_CONFORMANCE_URL='https://...' TWINS_LIVE_OPENAI_FILES=1 pytest tests/conformance/test_openai_files_live.py

It sends ONE GET to that address and compares the shape of the answer with the twin's. It is not part of the nightly workflow.
"""

from __future__ import annotations

import os

import httpx
import pytest

from twins import Universe

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("TWINS_LIVE_OPENAI_FILES") != "1" or not os.environ.get("OPENAI_FILE_CONFORMANCE_URL"),
                       reason="by hand only: set TWINS_LIVE_OPENAI_FILES=1 and OPENAI_FILE_CONFORMANCE_URL (a synthetic file's address)"),
]


def test_a_fresh_link_answers_with_the_headers_the_twin_has():
    url = os.environ["OPENAI_FILE_CONFORMANCE_URL"]
    real = httpx.get(url, follow_redirects=False, timeout=30, headers={"Accept-Encoding": "identity"})
    universe = Universe()
    handed = universe.openai_files.attach(b"Folder Name,Quantity\n", file_name="synthetic.csv")
    ours = httpx.Client(transport=universe.transport).get(handed["download_url"], headers={"Accept-Encoding": "identity"})
    assert real.status_code == ours.status_code == 200, (real.status_code, dict(real.headers))
    assert httpx.URL(url).host == handed["download_url"].split("/")[2]  # the host name the twin answers on
    assert real.headers["content-type"].split(";")[0] == ours.headers["content-type"].split(";")[0]
    assert "content-length" in real.headers
