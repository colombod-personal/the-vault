"""jobs.vercel_setup against a stand-in for Vercel's REST API (projects, env, domains)."""

import json

import httpx
import pytest

from jobs import vercel_setup


class FakeVercel:
    def __init__(self, domains=(), envs=None):
        self.domains = [{"name": d, "verified": True, "redirect": None} for d in domains]
        self.clock = 1000  # Vercel's millisecond timestamps
        self.envs = [{"id": f"env{i}", "updatedAt": 1, **e} for i, e in enumerate(envs or [])]
        # Each domain -> the deployment serving it, which after a rollback isn't the newest one.
        self.serving = {d: {"id": "dpl_live", "url": "the-vault-live.vercel.app", "createdAt": 500} for d in domains}
        self.redeploys = []
        self.fail_redeploy = False
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer tok"
        path = req.url.path
        if path == "/v10/projects/the-vault/env" and req.method == "GET":
            return httpx.Response(200, json={"envs": self.envs})
        if path == "/v10/projects/the-vault/env" and req.method == "POST":
            new = json.loads(req.content)
            clash = [e for e in self.envs if e["key"] == new["key"] and set(e["target"]) & set(new["target"])]
            if clash and req.url.params.get("upsert") != "true":
                return httpx.Response(400, json={"error": {"code": "ENV_ALREADY_EXISTS"}})
            self.envs = [e for e in self.envs if e not in clash]
            self.clock += 1
            self.envs.append({"id": f"env{len(self.envs)}", "updatedAt": self.clock, **new})
            return httpx.Response(201, json={"created": self.envs[-1]})
        if path.startswith("/v9/projects/the-vault/env/") and req.method == "PATCH":
            env = next(e for e in self.envs if e["id"] == path.rsplit("/", 1)[1])
            self.clock += 1
            env.update(json.loads(req.content), updatedAt=self.clock)
            return httpx.Response(200, json=env)
        if path == "/v9/projects/the-vault/domains":
            return httpx.Response(200, json={"domains": self.domains})
        if path.startswith("/v13/deployments/") and req.method == "GET":
            live = self.serving.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=live) if live else httpx.Response(404, json={"error": {"code": "not_found"}})
        if path == "/v13/deployments" and req.method == "POST":
            if self.fail_redeploy:
                return httpx.Response(500, json={"error": {"code": "internal_error"}})
            self.redeploys.append(json.loads(req.content))
            self.clock += 1
            new = {"id": f"dpl_{self.clock}", "url": "the-vault-new.vercel.app", "createdAt": self.clock}
            self.serving = {d: new for d in self.serving}
            return httpx.Response(200, json=new)
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    def value(self, key, target="production"):
        return next(e["value"] for e in self.envs if e["key"] == key and target in e["target"])


@pytest.fixture(autouse=True)
def token(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    return tmp_path / "summary.md"


def test_first_run_before_any_deploy():
    fake = FakeVercel()
    state = vercel_setup.main([], fake.transport)
    assert state["changed"] == ["SESSION_SECRET (production)", "SESSION_SECRET (preview)"] and state["base_url"] is None
    assert len(fake.value("SESSION_SECRET")) == 64
    assert fake.value("SESSION_SECRET", "preview") != fake.value("SESSION_SECRET")  # previews can't sign production cookies


def test_after_first_deploy_sets_base_url_once_and_keeps_secrets(token):
    fake = FakeVercel(domains=["the-vault-abc123.vercel.app", "the-vault.vercel.app"])
    state = vercel_setup.main([], fake.transport)
    secret = fake.value("SESSION_SECRET")
    assert state["base_url"] == "https://the-vault.vercel.app" and fake.value("BASE_URL") == state["base_url"]
    assert state["redirect_uris"]["google"] == "https://the-vault.vercel.app/api/auth/callback/google"
    again = vercel_setup.main([], fake.transport)
    assert again["changed"] == [] and fake.value("SESSION_SECRET") == secret  # never overwritten
    text = token.read_text(encoding="utf-8")
    assert "Storage → Create Database → Neon" in text and "passkeys only" in text


def test_custom_domain_wins_and_ready_state():
    envs = [{"key": k, "target": ["production"]} for k in
            ("SESSION_SECRET", "DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")]
    envs.append({"key": "SESSION_SECRET", "target": ["preview"]})
    fake = FakeVercel(domains=["the-vault.vercel.app", "vault.example.com"], envs=envs)
    state = vercel_setup.main([], fake.transport)
    assert state["base_url"] == "https://vault.example.com" and state["changed"] == ["BASE_URL"]
    assert state["database"] is True and state["providers"] == ["google"]


def test_shortest_custom_domain_is_preferred():
    fake = FakeVercel(domains=["the-vault.vercel.app", "www.vault.example.com", "vault.example.com"])
    assert vercel_setup.main([], fake.transport)["base_url"] == "https://vault.example.com"


def test_a_shared_session_secret_is_split_per_target():
    fake = FakeVercel(envs=[{"key": "SESSION_SECRET", "value": "shared", "target": ["production", "preview"]}])
    state = vercel_setup.main([], fake.transport)
    assert state["changed"] == ["SESSION_SECRET (production only)", "SESSION_SECRET (preview)"]
    assert fake.value("SESSION_SECRET") == "shared"  # production keeps its secret: nobody is signed out
    assert fake.value("SESSION_SECRET", "preview") not in ("shared", None)
    assert vercel_setup.main([], fake.transport)["changed"] == []


def test_only_variables_production_can_read_count():
    envs = [{"key": k, "target": ["preview"]} for k in ("DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")]
    state = vercel_setup.main([], FakeVercel(envs=envs).transport)
    assert state["database"] is False and state["providers"] == []


def test_provider_credentials_are_asked_for_and_stored_in_production_only(capsys):
    vercel = FakeVercel(domains=["the-vault.vercel.app"],
                        envs=[{"key": "GOOGLE_CLIENT_ID", "value": "old", "target": ["production"], "type": "encrypted"}])
    asked = []
    answers = {"GOOGLE_CLIENT_ID": "new-id.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "s3cret"}
    state = vercel_setup.main(["--provider", "google"], vercel.transport,
                              prompt=lambda name: asked.append(name) or answers[name])
    assert asked == ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
    assert "google" in state["providers"]
    assert {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"} <= set(state["changed"])
    stored = [e for e in vercel.envs if e["key"].startswith("GOOGLE_")]
    assert all(e["target"] == ["production"] for e in stored)
    assert vercel.value("GOOGLE_CLIENT_ID") == answers["GOOGLE_CLIENT_ID"] and len(stored) == 2  # replaced, not duplicated
    out = capsys.readouterr()
    assert "s3cret" not in out.out + out.err  # names only, never values
    assert "https://the-vault.vercel.app/api/auth/callback/google" in out.err


def test_redeploy_applies_what_changed_and_only_then(token):
    """Vercel reads variables when it builds: after setting SESSION_SECRET or BASE_URL the live
    deployment is redeployed so it uses them. Once it is newer than every variable, it isn't."""
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    state = vercel_setup.main(["--redeploy"], fake.transport)
    assert state["redeployed"] == "https://the-vault-new.vercel.app"
    assert fake.redeploys == [{"name": "the-vault", "deploymentId": "dpl_live", "target": "production"}]
    again = vercel_setup.main(["--redeploy"], fake.transport)
    assert again["changed"] == [] and again["redeployed"] is None and len(fake.redeploys) == 1
    assert "Redeployed" in token.read_text(encoding="utf-8")


def test_redeploys_what_serves_the_domain_after_a_rollback():
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    fake.serving["the-vault.vercel.app"] = {"id": "dpl_rolled_back_to", "url": "old.vercel.app", "createdAt": 400}
    vercel_setup.main(["--redeploy"], fake.transport)
    assert fake.redeploys[0]["deploymentId"] == "dpl_rolled_back_to"


def test_a_failed_redeploy_is_retried_by_the_next_run():
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    fake.fail_redeploy = True
    with pytest.raises(httpx.HTTPStatusError):
        vercel_setup.main(["--redeploy"], fake.transport)
    fake.fail_redeploy = False
    state = vercel_setup.main(["--redeploy"], fake.transport)
    assert state["changed"] == [] and state["redeployed"] and len(fake.redeploys) == 1


def test_settings_saved_before_the_first_deploy_is_live_are_applied_later():
    fake = FakeVercel()
    first = vercel_setup.main(["--redeploy"], fake.transport)
    assert first["changed"] and first["redeployed"] is None and fake.redeploys == []
    # The first deployment was building while those variables were saved, then went live.
    fake.domains = [{"name": "the-vault.vercel.app", "verified": True, "redirect": None}]
    fake.serving = {"the-vault.vercel.app": {"id": "dpl_first", "url": "first.vercel.app", "createdAt": 1001}}
    state = vercel_setup.main(["--redeploy"], fake.transport)
    assert state["redeployed"] and fake.redeploys[0]["deploymentId"] == "dpl_first"
    assert vercel_setup.main(["--redeploy"], fake.transport)["redeployed"] is None
