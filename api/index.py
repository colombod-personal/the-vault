"""Vercel entry point. Vercel detects this file as a FastAPI app: every request that isn't a
file in public/ (served by Vercel's CDN) reaches ``app``.

``app`` must be assigned at the top level of this module: Vercel's builder checks that
statically (tests/test_vercel_entrypoint.py mirrors the check).
"""

import sys
from pathlib import Path

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vault.app import create_app  # noqa: E402


def _create() -> FastAPI:
    try:
        return create_app(serve_static=False)
    except RuntimeError as exc:  # misconfigured deployment: explain instead of crashing every request
        from fastapi.responses import JSONResponse

        fallback = FastAPI()
        reason = str(exc)

        @fallback.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        def not_configured(path: str):
            return JSONResponse({"detail": "The Vault is not configured yet: " + reason}, status_code=503)

        return fallback


app = _create()
