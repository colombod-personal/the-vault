"""#393: a merge conflict left in a tracked text file (it happened in a design doc on main) fails here."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKERS = ("<<<<<<< ", ">>>>>>> ")


def test_no_tracked_text_file_holds_a_merge_conflict_marker():
    names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    found = []
    for name in names:
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a binary file
        found += [f"{name}:{n}" for n, line in enumerate(text.splitlines(), 1) if line.startswith(MARKERS)]
    assert not found, f"unresolved merge conflict markers: {found[:10]}"
