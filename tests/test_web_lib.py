"""The browser's JavaScript helpers (public/lib), tested with Node's test runner."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")
def test_javascript_helpers():
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests" / "js").glob("*.test.mjs"))
    assert files
    res = subprocess.run(["node", "--test", *files], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
