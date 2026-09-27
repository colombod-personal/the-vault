"""Vercel entry point: every /api/* request is routed here (see vercel.json)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault.app import create_app  # noqa: E402

app = create_app(serve_static=False)
