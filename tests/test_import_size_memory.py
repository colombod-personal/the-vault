"""What an import at the two limits costs in memory (vault-0n9.3).

``IMPORT_MAX_BYTES`` (8 MiB by default) and ``IMPORT_MAX_ROWS`` (100,000) are product rules, not figures derived from the memory of a
function instance (the instance size has not been read). So the cost of one import at exactly both limits is measured here, on the
existing preview path and on the new assembled-parts path, and must stay under a stated ceiling. The measure is the process's peak
resident set size (``ru_maxrss``), taken in a fresh interpreter (``tests/memory_probe.py``) so it is this import's and not the test
run's. The file is synthetic: invented names, Dragon Shield-shaped, exactly 8 MiB long with exactly 100,000 rows (shape ``worst``:
the most rows the two limits let in, all matched to the catalog; 100,000 rows that match nothing peaked at 378 MB in the delta
review of PR #483, which the ceiling also covers).

Why two limits: a review of PR #483 built a file of exactly 8 MiB with 29-byte rows (291,740 rows) and measured a peak of 577 MB
(preview) and 595 MB (parts) with no row limit; memory follows rows more than bytes. With the row limit that file is refused before it is
parsed (``tests/test_import_size_limit.py``).

Measured on 2026-10-18, Python 3.12, Linux, a 5,000-printing catalog, the process holding the app and the test client (peak RSS, MB;
"added" is the peak minus the 151 MB the process held before the file was built):

    path                             rows      seconds   peak   added
    preview, worst case (8 MiB)     100,000      3.4      331    +179
    parts,   worst case (8 MiB)     100,000      7.1      340    +189   (512 parts of 16 KiB, then one assembly and one preview)
    preview, 108-byte rows           77,579      3.3      327    +171
    parts,   108-byte rows           77,579      6.8      342    +191
    preview, real rows (243 bytes)   35,078      1.7      259    +108
    parts,   real rows               35,078      5.5      277    +126

The ceiling is the worst measure (340) plus about 50%: 512 MB. One import at the limits fits a 1 GB function with room to spare; a
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
CEILING_MB = 512  # the worst measure above (340 MB) plus about 50%

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="peak resident size is read with ru_maxrss in KiB (Linux)")


def probe(database_url: str, mode: str, shape: str = "worst") -> dict:
    env = {**os.environ, "VAULT_TEST_DATABASE_URL": database_url, "PYTHONPATH": str(PROBE.parent.parent)}
    env.pop("IMPORT_MAX_BYTES", None)  # the defaults: the limits this measures
    env.pop("IMPORT_MAX_ROWS", None)
    done = subprocess.run([sys.executable, str(PROBE), mode, shape], capture_output=True, text=True, timeout=600, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_preview_of_a_file_of_exactly_the_limit_stays_under_the_memory_ceiling(database_url):
    report = probe(database_url, "preview")
    assert report["bytes"] == config.DEFAULT_IMPORT_MAX_BYTES == 8 * 1024 * 1024
    assert report["status"] == 200 and report["rows"] == config.DEFAULT_IMPORT_MAX_ROWS == 100_000, report
    assert report["matched_rows"] == report["rows"] and report["rss_peak_mb"] < CEILING_MB, f"peak {report['rss_peak_mb']} MB, before {report['rss_before_mb']} MB, {report['seconds']} s"  # measured 331 MB on 2026-10-18


def test_a_file_in_parts_of_exactly_the_limit_is_assembled_and_previewed_under_the_same_ceiling(database_url):
    report = probe(database_url, "parts")
    assert report["bytes"] == 8 * 1024 * 1024
    assert report["status"] == 200 and report["answer"] == "uploaded" and report["rows"] == 100_000, report
    assert report["rss_peak_mb"] < CEILING_MB, f"peak {report['rss_peak_mb']} MB, before {report['rss_before_mb']} MB, {report['seconds']} s"  # measured 340 MB on 2026-10-18
