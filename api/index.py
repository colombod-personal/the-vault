"""Vercel entry point: every /api/* request is routed here (see vercel.json)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault.app import create_app  # noqa: E402

try:
    app = create_app(serve_static=False)
except RuntimeError as exc:  # misconfigured deployment: explain instead of crashing every request
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()
    reason = str(exc)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def not_configured(path: str):
        return JSONResponse({"detail": "The Vault is not configured yet: " + reason}, status_code=503)
