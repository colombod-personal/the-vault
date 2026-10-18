"""What an import at the size limit costs in memory (vault-0n9.3).

``IMPORT_MAX_BYTES`` (8 MiB by default) is a product rule, not a figure derived from the memory of a function instance (the instance
size has not been read). So the cost of one import at exactly the limit is measured here, on the existing preview path and on the new
assembled-parts path, and must stay under a stated ceiling. The measure is the process's peak resident set size (``ru_maxrss``), taken in
a fresh interpreter (``tests/memory_probe.py``) so it is this import's and not the test run's. The file is synthetic: invented names,
Dragon Shield-shaped, exactly the limit long, with 77,579 rows of about 108 bytes (the most rows a file of this size can hold: the worst
case for memory); the owner's real export has about 243 bytes a row, which is 35,078 rows in the same size.

Measured on 2026-10-18, Python 3.12, Linux, a 5,000-printing catalog, the process holding the app and the test client (peak RSS, MB;
"added" is the peak minus the 152 MB the process held before the file was built):

    path                     rows     seconds   peak   added
    preview, short rows     77,579     3.3       327    +171
    preview, real rows      35,078     1.7       259    +108
    parts,   short rows     77,579     6.8       342    +191   (512 parts of 16 KiB, then one assembly and one preview)
    parts,   real rows      35,078     5.5       277    +126

The ceiling is the worst measure (342) plus about 50%: 512 MB. One import at the limit fits a 1 GB function with room to spare; a
function that ran four of them at the same moment would not. The instance size and its concurrency are not verified.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vault import config

PROBE = Path(__file__).parent / "memory_probe.py"
CEILING_MB = 512  # the worst measure above (342 MB) plus about 50%

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="peak resident size is read with ru_maxrss in KiB (Linux)")


def probe(database_url: str, mode: str, shape: str = "short") -> dict:
    env = {**os.environ, "VAULT_TEST_DATABASE_URL": database_url, "PYTHONPATH": str(PROBE.parent.parent)}
    env.pop("IMPORT_MAX_BYTES", None)  # the default: the limit this measures
    done = subprocess.run([sys.executable, str(PROBE), mode, shape], capture_output=True, text=True, timeout=600, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_preview_of_a_file_of_exactly_the_limit_stays_under_the_memory_ceiling(database_url):
    report = probe(database_url, "preview")
    assert report["bytes"] == config.DEFAULT_IMPORT_MAX_BYTES == 8 * 1024 * 1024
    assert report["status"] == 200 and report["rows"] > 70_000 and report["matched_rows"] == report["rows"], report
    assert report["rss_peak_mb"] < CEILING_MB, report  # measured 327 MB on 2026-10-18


def test_a_file_in_parts_of_exactly_the_limit_is_assembled_and_previewed_under_the_same_ceiling(database_url):
    report = probe(database_url, "parts")
    assert report["bytes"] == 8 * 1024 * 1024
    assert report["status"] == 200 and report["answer"] == "uploaded" and report["rows"] > 70_000, report
    assert report["rss_peak_mb"] < CEILING_MB, report  # measured 342 MB on 2026-10-18
