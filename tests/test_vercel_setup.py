"""jobs.vercel_setup against a stand-in for Vercel's REST API (projects, env, domains)."""

import httpx
import pytest

from jobs import vercel_setup
from twins import Universe


class FakeVercel:
    """A Vercel project on the Vercel twin, with a production deployment once it has domains."""

    def __init__(self, domains=(), envs=None, deployed=None):
        self.universe = Universe()
        self.twin = self.universe.vercel
        self.twin.add_project("the-vault", domains, envs or [])
        self.live = self.twin.deploy("the-vault") if (domains if deployed is None else deployed) else None
        self.transport = self.universe.transport

    @property
    def envs(self):
        return self.twin.projects["the-vault"]["envs"]

    @property
    def redeploys(self):
        return self.twin.redeploys()

    def value(self, key, target="production"):
        env = self.twin.env("the-vault", key, target)
        return env and env["value"]

    def serving(self, domain="the-vault.vercel.app"):
        return self.twin.deployments[self.twin.aliases[domain]]


@pytest.fixture(autouse=True)
def token(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_TOKEN", "twin-vercel-token")
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
    deployment is redeployed so it uses them. Once built from the current ones, it isn't."""
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    state = vercel_setup.main(["--redeploy"], fake.transport)
    assert state["redeployed"] == f"https://{fake.serving()['url']}" and fake.serving()["id"] != fake.live["id"]
    assert len(fake.redeploys) == 1
    again = vercel_setup.main(["--redeploy"], fake.transport)
    assert again["changed"] == [] and again["redeployed"] is None and len(fake.redeploys) == 1
    assert "Redeployed" in token.read_text(encoding="utf-8")


def test_a_merge_deploy_is_stamped_once():
    """A deployment made by a merge carries no fingerprint, so the next run redeploys it once."""
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    vercel_setup.main(["--redeploy"], fake.transport)
    fake.twin.deploy("the-vault")  # the next merge to main
    assert vercel_setup.main(["--redeploy"], fake.transport)["redeployed"]
    assert vercel_setup.main(["--redeploy"], fake.transport)["redeployed"] is None


def test_redeploys_what_serves_the_domain_after_a_rollback():
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    fake.twin.deploy("the-vault")  # a newer release, rolled back from:
    fake.twin.promote("the-vault", fake.live["id"])
    vercel_setup.main(["--redeploy"], fake.transport)
    assert fake.redeploys == [fake.live["id"]]  # the release that serves the domain, not the newest


def test_a_redeploy_never_puts_older_code_back():
    """Rebuilding the live deployment takes main's latest commit, not the one it was built from."""
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    fake.twin.head = "c2"  # merged, its build still running
    vercel_setup.main(["--redeploy"], fake.transport)
    assert fake.twin.commits[fake.serving()["id"]] == "c2"


def test_a_release_going_live_meanwhile_is_left_alone():
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    vercel_setup.main(["--redeploy"], fake.transport)  # stamped
    fake.twin.store_env("the-vault", "EXAMPLE", "x", ["production"])  # something to apply
    lookups = []

    def merge_lands():
        lookups.append(1)
        if len(lookups) == 1:
            fake.twin.head = "c2"
            fake.twin.deploy("the-vault")
    fake.twin.on_lookup = merge_lands
    state = vercel_setup.main(["--redeploy"], fake.transport)
    assert state["redeployed"] is None and len(fake.redeploys) == 1  # the merge's release stays
    assert fake.twin.commits[fake.serving()["id"]] == "c2"
    fake.twin.on_lookup = None
    assert vercel_setup.main(["--redeploy"], fake.transport)["redeployed"]  # and is stamped next run


def test_a_failed_redeploy_is_retried_by_the_next_run():
    fake = FakeVercel(domains=["the-vault.vercel.app"])
    fake.twin.fail_next("/v13/deployments", 500)
    with pytest.raises(httpx.HTTPStatusError):
        vercel_setup.main(["--redeploy"], fake.transport)
    state = vercel_setup.main(["--redeploy"], fake.transport)
    assert state["changed"] == [] and state["redeployed"] and len(fake.redeploys) == 1


@pytest.mark.parametrize("change", ["deleted", "no longer in production"])
def test_removing_a_production_variable_redeploys(change):
    """Deleting DEV_LOGIN (as the checklist asks) must reach the live site, though nothing is newer."""
    fake = FakeVercel(domains=["the-vault.vercel.app"], envs=[{"key": "DEV_LOGIN", "value": "1", "target": ["production"]}])
    vercel_setup.main(["--redeploy"], fake.transport)
    env = fake.twin.env("the-vault", "DEV_LOGIN")
    if change == "deleted":
        fake.envs.remove(env)
    else:
        env["target"] = ["development"]  # a direct edit that leaves updatedAt alone
    state = vercel_setup.main(["--redeploy"], fake.transport)
    assert state["changed"] == [] and state["redeployed"] and len(fake.redeploys) == 2


def test_settings_saved_before_the_first_deploy_is_live_are_applied_later():
    fake = FakeVercel()
    first = vercel_setup.main(["--redeploy"], fake.transport)
    assert first["changed"] and first["redeployed"] is None and fake.redeploys == []
    fake.twin.add_domain("the-vault", "the-vault.vercel.app")
    fake.twin.deploy("the-vault")  # the first deployment goes live
    state = vercel_setup.main(["--redeploy"], fake.transport)
    assert state["redeployed"] and len(fake.redeploys) == 1
    assert vercel_setup.main(["--redeploy"], fake.transport)["redeployed"] is None
