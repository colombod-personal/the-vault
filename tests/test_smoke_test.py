"""jobs/smoke_test.py against a real app (healthy), a not-configured one, and Vercel's protection wall."""

import httpx
from fastapi.testclient import TestClient

from jobs import smoke_test


class Passthrough(httpx.BaseTransport):
    """Send requests into a TestClient, adding what Vercel would add."""

    def __init__(self, client, region="fra1"):
        self.client, self.region = client, region

    def handle_request(self, request):
        res = self.client.request(request.method, request.url.raw_path.decode(), headers=dict(request.headers),
                                  content=request.content)
        headers = {k: v for k, v in res.headers.items() if k.lower() not in ("content-encoding", "content-length")} | {"strict-transport-security": "max-age=63072000",
                                       "x-content-type-options": "nosniff", "x-vercel-id": f"fra1::{self.region}::abc"}
        return httpx.Response(res.status_code, headers=headers, content=res.content)


def test_healthy_deployment(settings):
    from vault.app import create_app

    from dataclasses import replace

    production = replace(settings, base_url="https://vault.test", dev_login=False)  # as deployed
    with TestClient(create_app(production, serve_static=True), base_url="https://vault.test") as client:
        results, configured = smoke_test.run("https://vault.test", Passthrough(client))
    assert configured and [(n, d) for n, ok, d in results if not ok] == []
    assert len(results) == 13


def test_protected_preview_is_reported_not_failed(monkeypatch):
    wall = httpx.MockTransport(lambda r: httpx.Response(401, text="<title>Authentication Required</title> Vercel"))
    results, configured = smoke_test.run("https://preview.test", wall)
    assert not configured and results[0][0] == "Deployment protection" and results[0][1]


def test_not_configured_deployment(monkeypatch):
    def handler(request):
        if request.url.path == "/api/health":
            return httpx.Response(503, json={"detail": "The Vault is not configured yet: DATABASE_URL is not set"})
        return httpx.Response(200, text="<html>The Vault</html>")
    results, configured = smoke_test.run("https://x.test", httpx.MockTransport(handler))
    assert not configured and "not configured yet" in dict((n, d) for n, _, d in results)["GET /api/health"]
