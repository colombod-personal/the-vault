"""CI and deployment security: secrets only reach main, and nothing untrusted runs next to them.

The rules (README → Security):
- Secrets live in the "vercel-production" GitHub environment, which only main may use. A job
  that reads a secret declares that environment and only runs for main.
- Secret-bearing workflows never run on push or pull_request (a branch could change them).
- Vercel deploys only merges to main (vercel.json → ignoreCommand).
- Least privilege: read-only GITHUB_TOKEN, no persisted git credentials, pinned Vercel CLI,
  no ${{ }} expressions inside shell scripts, integrity hashes on CDN scripts.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ENVIRONMENT = "vercel-production"
MAIN_ONLY = ("github.ref == 'refs/heads/main'", "github.event.deployment.ref == github.event.repository.default_branch")


def load(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML reads a bare `on` as true
    return data


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_least_privilege(path):
    wf = load(path)
    assert wf.get("permissions") == {"contents": "read"}, "declare `permissions: contents: read` at the top"
    for job in wf["jobs"].values():
        assert "permissions" not in job or all(v in ("read", "none") for v in job["permissions"].values())
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("actions/checkout"):
                assert (step.get("with") or {}).get("persist-credentials") is False, "checkout without persisting the token"
            if "run" in step:
                assert "${{" not in step["run"], f"pass values through env, not ${{{{ }}}} in the script: {step['run'][:60]}"
                assert "@latest" not in step["run"], "pin the tool version"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_secrets_only_reach_main(path):
    wf = load(path)
    triggers = wf["on"] if isinstance(wf["on"], dict) else {t: None for t in ([wf["on"]] if isinstance(wf["on"], str) else wf["on"])}
    for name, job in wf["jobs"].items():
        uses_secrets = "secrets." in yaml.safe_dump(job)
        if not uses_secrets:
            continue
        assert job.get("environment") == ENVIRONMENT, f"{name}: secrets come from the {ENVIRONMENT} environment"
        assert any(cond in str(job.get("if", "")) for cond in MAIN_ONLY), f"{name}: run only for main"
        assert not {"push", "pull_request", "pull_request_target"} & set(triggers), f"{name}: no push/PR triggers"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_secrets_reach_single_steps_that_install_nothing(path):
    """A secret goes in one step's env, never the job's (every step, even checkout and installs, would
    see it), and never next to a package install whose scripts could read it."""
    for name, job in load(path)["jobs"].items():
        assert "secrets." not in yaml.safe_dump(job.get("env") or {}), f"{name}: secret in the job-level env"
        for step in job.get("steps", []):
            if "secrets." in yaml.safe_dump(step.get("env") or {}):
                assert "uses" not in step, f"{name}: secret passed to an action"
                assert not re.search(r"\b(pip|npm|npx|yarn|pnpm)\b[^\n]*\binstall\b", step.get("run", "")), \
                    f"{name}: installs a package in a step that holds a secret"


def test_no_secret_outside_its_job_env():
    for path in WORKFLOWS:
        wf = load(path)
        assert "secrets." not in yaml.safe_dump({k: v for k, v in wf.items() if k != "jobs"}), path.name


@pytest.mark.parametrize("ref,deploys", [("main", True), ("claude/feature", False), ("", False)])
def test_vercel_deploys_only_main(ref, deploys):
    command = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["ignoreCommand"]
    skipped = subprocess.run(["bash", "-c", command], env={"VERCEL_GIT_COMMIT_REF": ref}).returncode == 0
    assert skipped is not deploys


def test_cdn_scripts_have_integrity_hashes():
    for page in (ROOT / "public").glob("*.html"):
        for tag in re.findall(r"<script[^>]*src=\"https?://[^>]*>", page.read_text(encoding="utf-8")):
            assert 'integrity="sha384-' in tag and 'crossorigin="anonymous"' in tag, f"{page.name}: {tag}"


@pytest.mark.parametrize("path,ignored", [
    (".env.local", True), (".env.production.local", True), (".env.vercel", True), (".env", True),
    (".vercel/project.json", True), (".claude/settings.local.json", True), (".env.example", False),
])
def test_local_secrets_are_never_committed(path, ignored):
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
    assert (result.returncode == 0) is ignored, path
