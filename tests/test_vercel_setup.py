"""jobs.vercel_setup against a stand-in for Vercel's REST API (projects, env, domains)."""

import json

import httpx
import pytest

from jobs import vercel_setup


class FakeVercel:
    def __init__(self, domains=(), envs=None):
        self.domains = [{"name": d, "verified": True, "redirect": None} for d in domains]
        self.envs = list(envs or [])
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer tok"
        path = req.url.path
        if path == "/v10/projects/the-vault/env" and req.method == "GET":
            return httpx.Response(200, json={"envs": self.envs})
        if path == "/v10/projects/the-vault/env" and req.method == "POST":
            self.envs.append(json.loads(req.content))
            return httpx.Response(201, json={"created": self.envs[-1]})
        if path == "/v9/projects/the-vault/domains":
            return httpx.Response(200, json={"domains": self.domains})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    def value(self, key):
        return next(e["value"] for e in self.envs if e["key"] == key)


@pytest.fixture(autouse=True)
def token(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    return tmp_path / "summary.md"


def test_first_run_before_any_deploy():
    fake = FakeVercel()
    state = vercel_setup.main([], fake.transport)
    assert state["changed"] == ["SESSION_SECRET"] and state["base_url"] is None
    assert len(fake.value("SESSION_SECRET")) == 64


def test_after_first_deploy_sets_base_url_once_and_keeps_secrets(token):
    fake = FakeVercel(domains=["the-vault-abc123.vercel.app", "the-vault.vercel.app"])
    state = vercel_setup.main([], fake.transport)
    secret = fake.value("SESSION_SECRET")
    assert state["base_url"] == "https://the-vault.vercel.app" and fake.value("BASE_URL") == state["base_url"]
    assert state["redirect_uris"]["google"] == "https://the-vault.vercel.app/api/auth/callback/google"
    again = vercel_setup.main([], fake.transport)
    assert again["changed"] == [] and fake.value("SESSION_SECRET") == secret  # never overwritten
    text = token.read_text(encoding="utf-8")
    assert "Storage → Create Database → Neon" in text and "no provider yet" in text


def test_custom_domain_wins_and_ready_state():
    envs = [{"key": k, "target": ["production"]} for k in
            ("SESSION_SECRET", "DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")]
    fake = FakeVercel(domains=["the-vault.vercel.app", "vault.example.com"], envs=envs)
    state = vercel_setup.main([], fake.transport)
    assert state["base_url"] == "https://vault.example.com" and state["changed"] == ["BASE_URL"]
    assert state["database"] is True and state["providers"] == ["google"]


def test_shortest_custom_domain_is_preferred():
    fake = FakeVercel(domains=["the-vault.vercel.app", "www.vault.example.com", "vault.example.com"])
    assert vercel_setup.main([], fake.transport)["base_url"] == "https://vault.example.com"
