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
    assert len(results) == 14


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


def test_vercel_login_redirect_is_reported_as_protection():
    sso = httpx.MockTransport(lambda r: httpx.Response(
        302, headers={"location": "https://vercel.com/sso-api?url=https%3A%2F%2Fthe-vault-x.vercel.app%2F&nonce=1"}))
    results, configured = smoke_test.run("https://the-vault-x.vercel.app", sso)
    assert not configured and [(n, ok) for n, ok, _ in results] == [("Deployment protection", True)]


def test_a_deployment_without_a_database_is_reported_not_broken(monkeypatch):
    # Vercel's fallback app answers 503 "not configured yet" for every path, pages included.
    down = httpx.MockTransport(lambda r: httpx.Response(503, json={"detail": "The Vault is not configured yet: x"}))
    results, configured = smoke_test.run("https://x.test", down)
    assert not configured
    assert [n for n, ok, _ in results if not ok] == ["GET /api/health"]  # the one, expected, finding
    monkeypatch.setattr(smoke_test, "run", lambda base: (results, configured))
    assert smoke_test.main(["https://x.test"]) == 0


def test_the_public_site_behind_a_vercel_login_fails(monkeypatch, capsys):
    """With SMOKE_EXPECT_PUBLIC=1 (mtgvault.cards) a Vercel login wall is an outage, not a skipped preview."""
    sso = httpx.MockTransport(lambda r: httpx.Response(
        302, headers={"location": "https://vercel.com/sso-api?url=https%3A%2F%2Fmtgvault.cards%2F&nonce=1"}))
    monkeypatch.setenv("SMOKE_EXPECT_PUBLIC", "1")
    results, configured = smoke_test.run("https://mtgvault.cards", sso)
    assert configured and [(n, ok) for n, ok, _ in results] == [("Public (no Vercel login)", False)]
    real_run = smoke_test.run
    monkeypatch.setattr(smoke_test, "run", lambda base: real_run(base, sso))
    assert smoke_test.main(["https://mtgvault.cards"]) == 1
    monkeypatch.delenv("SMOKE_EXPECT_PUBLIC")
    assert smoke_test.main(["https://mtgvault.cards"]) == 0  # a preview: reported, not failed
