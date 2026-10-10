"""CI and deployment security: secrets only reach main, and nothing untrusted runs next to them.

The rules (docs/developing.md → Security):
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
import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ENVIRONMENT = "vercel-production"
# Not github.event.deployment.ref: Vercel deploys a commit SHA, so that never names a branch.
MAIN_ONLY = ("github.ref == 'refs/heads/main'",)


def load(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML reads a bare `on` as true
    return data


# The only jobs that may write issues (#64: the budget guard opens or updates a GitHub issue, which needs `issues: write`). Each one
# holds no secret and no environment (test_the_issue_writers_hold_nothing_else): a read-only token everywhere else.
ISSUE_WRITERS = {("sync-prices.yml", "alert"), ("sync-catalog.yml", "alert"), ("sync-limited.yml", "alert"), ("neon-monthly-check.yml", "remind"),
                 ("issue-progress.yml", "progress")}  # writes the Progress block of the issues a PR names (AGENTS.md section 8)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_least_privilege(path):
    wf = load(path)
    assert wf.get("permissions") == {"contents": "read"}, "declare `permissions: contents: read` at the top"
    for name, job in wf["jobs"].items():
        if (path.name, name) in ISSUE_WRITERS:
            assert job["permissions"] == {"contents": "read", "issues": "write"}, f"{name}: contents read and issues write, nothing more"
        else:
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


def _bash() -> str:
    """The POSIX shell Vercel runs the command in. On Windows `bash` on the PATH can be the WSL launcher, which fails
    with no distribution installed, so use the bash that ships with Git when it is there."""
    if os.name == "nt":
        git = shutil.which("git")
        for parent in (Path(git).parents if git else []):
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return "bash"


@pytest.mark.parametrize("ref,deploys", [("main", True), ("claude/feature", False), ("", False)])
def test_vercel_deploys_only_main(ref, deploys):
    command = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["ignoreCommand"]
    skipped = subprocess.run([_bash(), "-c", command], env={"VERCEL_GIT_COMMIT_REF": ref}).returncode == 0
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


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_to_commits(path):
    """A tag like @v4 can be moved to new code; a full commit SHA can't. Jobs here hold production
    secrets, so every action is pinned to the commit that was reviewed."""
    for name, job in load(path)["jobs"].items():
        for step in job.get("steps", []):
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), f"{name}: pin {step['uses']} to a SHA"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_vercel_build_never_sees_the_token(path):
    """`vercel build` runs the project's install and build steps, which must not be able to read the
    deployment token: only `pull`, `link` and `deploy` get it."""
    for job in load(path)["jobs"].values():
        for step in job.get("steps", []):
            for line in step.get("run", "").splitlines():
                if "vercel build" in line:
                    assert "env -u VERCEL_TOKEN" in line and "--token" not in line, line.strip()


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_the_vercel_build_never_sees_pulled_secrets(path):
    """`vercel pull` writes the project's environment (database URL, OAuth secrets) to
    .vercel/.env.*; those files are deleted before `vercel build` runs the install and build steps."""
    for job in load(path)["jobs"].values():
        for step in job.get("steps", []):
            pulled = False
            for line in step.get("run", "").splitlines():
                if "vercel pull" in line:
                    pulled = True
                elif "rm -f .vercel/.env." in line:
                    pulled = False
                elif "vercel build" in line:
                    assert not pulled, "pulled env files are still on disk when `vercel build` runs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_python_packages_are_installed_only_at_hash_checked_versions(path):
    """A version range lets a newly published (or compromised) release run next to the secrets:
    every pip install takes exact, hash-checked versions from a lock file. The one git
    dependency (mtg-toolkits) is pinned to a commit and built without fetching anything."""
    for job in load(path)["jobs"].values():
        for step in job.get("steps", []):
            for line in step.get("run", "").splitlines():
                cmd = line.split("#")[0].strip()
                if not re.match(r"(python -m )?pip3? install\b", cmd):
                    continue
                assert "--no-deps" in cmd, cmd
                assert ("--require-hashes" in cmd and re.search(r"-r (jobs/requirements-ops|requirements-lock)\.txt", cmd)) \
                    or ("--no-build-isolation" in cmd and "-r requirements-vcs.txt" in cmd), cmd


def test_the_locks_cover_every_dependency():
    """requirements-lock.txt (hash-checked) holds every dependency the app, its tests, the library
    and the build need; requirements-vcs.txt pins the library to the same commit as pyproject."""
    import tomllib

    def names(text):
        return {re.split(r"[\[<>=@ ;]", line.strip(), maxsplit=1)[0].lower().replace("_", "-")
                for line in text.splitlines() if line.strip() and not line.startswith((" ", "#", "-"))}

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wanted = names("\n".join(project["dependencies"] + project["optional-dependencies"]["dev"]))
    wanted |= {"httpx", "hatchling"}  # mtg-toolkits' dependency, and the build backend
    locked = names((ROOT / "requirements-lock.txt").read_text())
    assert wanted - {"mtg-toolkits"} <= locked, wanted - locked
    assert all(re.search(rf"^{re.escape(n)}==\S+ \\\n\s+--hash=sha256:", (ROOT / "requirements-lock.txt").read_text(), re.M | re.I)
               for n in locked)
    [vcs] = [d for d in project["dependencies"] if d.startswith("mtg-toolkits")]
    assert (ROOT / "requirements-vcs.txt").read_text().strip() == vcs
    assert vcs in (ROOT / "requirements.txt").read_text()
    assert "httpx==" in (ROOT / "jobs" / "requirements-ops.txt").read_text()


def test_a_newer_production_deploy_cancels_the_older_public_site_check():
    # An older run waits for a release a newer deployment replaced, so it could only fail. The
    # group is on the job: preview deployment events skip the job and must not cancel the check.
    data = load(ROOT / ".github" / "workflows" / "public-site.yml")
    assert "concurrency" not in data
    job = data["jobs"]["public"]["concurrency"]
    assert job["cancel-in-progress"] is True
    # Both production triggers share one group; daily and manual runs keep their own.
    assert "(github.event_name == 'deployment_status' || github.event_name == 'workflow_run') && 'deploy' || github.run_id" in job["group"]


def test_waiting_for_the_deployed_commit_is_bounded():
    # A server that accepts the connection and then hangs must not use up the job's timeout.
    script = load(ROOT / ".github" / "workflows" / "public-site.yml")["jobs"]["public"]["steps"][-1]["run"]
    assert "--max-time" in script and "deadline=$((SECONDS + 300))" in script


def test_the_public_site_is_checked_after_the_manual_deploy_too():
    # deploy.yml (the Vercel CLI fallback) makes production deployments without a deployment_status
    # event; its own smoke test treats a login wall as a preview, so public-site runs after it.
    data = load(ROOT / ".github" / "workflows" / "public-site.yml")
    manual = load(ROOT / ".github" / "workflows" / "deploy.yml")["name"]
    assert data["on"]["workflow_run"] == {"workflows": [manual], "types": ["completed"]}
    condition = data["jobs"]["public"]["if"]
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    assert "github.event.workflow_run.head_branch == 'main'" in condition


def test_the_public_site_check_runs_the_smoke_test_of_the_deployed_release():
    # main may move on after a deploy; newer smoke-test code must not judge an older release.
    steps = load(ROOT / ".github" / "workflows" / "public-site.yml")["jobs"]["public"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.deployment.sha || github.event.workflow_run.head_sha || github.sha }}"
    assert checkout["with"]["persist-credentials"] is False


def test_the_public_site_check_waits_for_the_deployment_itself_not_its_commit():
    # A redeploy of the same commit (vercel_setup redeploys after a settings change) reports the same
    # commit as the release it replaces; only the deployment's own address tells them apart.
    data = load(ROOT / ".github" / "workflows" / "public-site.yml")
    job = data["jobs"]["public"]
    assert "EXPECT_COMMIT" not in job["env"]
    assert job["env"]["EXPECT_DEPLOYMENT"] == (
        "${{ github.event.deployment_status.environment_url || github.event.deployment_status.target_url }}")
    script = job["steps"][-1]["run"]
    assert '.get("deployment")' in script and '[ "$live" = "$expected" ]' in script


def test_jobs_run_on_a_pinned_runner_image_not_ubuntu_latest():
    """ubuntu-latest moves to a new Ubuntu on GitHub's schedule (26 from 2026-10-19): the lockfile install, the Postgres
    service and the Node build must be moved to a new image deliberately (#180)."""
    for path in WORKFLOWS:
        for name, job in load(path)["jobs"].items():
            assert job["runs-on"] != "ubuntu-latest", f"{path.name}: {name}"


def test_the_issue_writers_hold_nothing_else_and_nothing_else_writes_issues():
    """The budget guard's alert jobs need `issues: write` to open a GitHub issue, so they get it and nothing else: no environment, no
    secret (not even a Neon key or the database address), no push or pull-request trigger, the run's own `github.token`, and the one
    command that talks to GitHub. Every other job stays read-only (test_least_privilege)."""
    found = set()
    for path in WORKFLOWS:
        wf = load(path)
        triggers = wf["on"] if isinstance(wf["on"], dict) else {t: None for t in ([wf["on"]] if isinstance(wf["on"], str) else wf["on"])}
        for name, job in wf["jobs"].items():
            if "issues" not in (job.get("permissions") or {}):
                continue
            found.add((path.name, name))
            assert "secrets." not in yaml.safe_dump(job), f"{path.name}/{name}: an issue writer holds no secret"
            assert "environment" not in job, f"{path.name}/{name}: and no environment (the vercel-production one carries the secrets)"
            if (path.name, name) == ("issue-progress.yml", "progress"):
                # The one exception: it must run when a pull request opens or merges, to write that pull request's Progress block
                # into the issues it names. It stays safe by running the DEFAULT branch's copy of the script (never the pull request's),
                # reading the description only as data, holding no secret, and writing nothing but issue comments and bodies.
                assert set(triggers) == {"pull_request"} and "pull_request_target" not in triggers
                checkout = next(s for s in job["steps"] if "checkout" in s.get("uses", ""))
                assert "default_branch" in checkout["with"]["ref"], "never check out the pull request's own code with a write token"
                assert all("github.event.pull_request.head" not in str(s) for s in job["steps"])
                continue
            assert not {"push", "pull_request", "pull_request_target"} & set(triggers), f"{path.name}/{name}: no push or PR trigger"
            assert "refs/heads/main" in str(job.get("if", "")), f"{path.name}/{name}: only for main"
            runs = [s["run"] for s in job["steps"] if "run" in s]
            assert any(r.startswith("python -m jobs.budget_alert") for r in runs)
            env = next(s["env"] for s in job["steps"] if "run" in s and "jobs.budget_alert" in s["run"])
            assert env["GITHUB_TOKEN"] == "${{ github.token }}"
    assert found == ISSUE_WRITERS


def test_the_sync_workflows_pass_the_guards_messages_to_an_alert_job_and_check_neon_usage():
    prices = load(ROOT / ".github" / "workflows" / "sync-prices.yml")
    catalog = load(ROOT / ".github" / "workflows" / "sync-catalog.yml")
    for wf, outputs in ((prices, {"storage_alert", "usage_alert"}), (catalog, {"storage_alert"})):
        assert set(wf["jobs"]["sync"]["outputs"]) == outputs
        alert = wf["jobs"]["alert"]
        assert alert["needs"] == "sync" and "always()" in alert["if"]
    steps = {s.get("id"): s for s in prices["jobs"]["sync"]["steps"]}
    assert steps["sync"]["run"].startswith("python -m jobs.sync_prices")
    usage = steps["usage"]
    assert usage["run"].startswith("python -m jobs.neon_usage") and usage["if"] == "${{ !cancelled() }}"  # reported even if the sync failed
    assert usage["env"]["NEON_API_KEY"] == "${{ secrets.NEON_API_KEY }}" and "NEON_PROJECT_ID" in usage["env"]
    assert "python -m jobs.sync_catalog" in {s.get("id"): s for s in catalog["jobs"]["sync"]["steps"]}["load"]["run"]
    limited = load(ROOT / ".github" / "workflows" / "sync-limited.yml")  # #178: the same guard, in the same shape
    assert set(limited["jobs"]["sync"]["outputs"]) == {"storage_alert"}
    assert limited["jobs"]["alert"]["needs"] == "sync" and "always()" in limited["jobs"]["alert"]["if"]
    assert "python -m jobs.sync_limited" in {s.get("id"): s for s in limited["jobs"]["sync"]["steps"]}["load"]["run"]


def test_the_limited_job_runs_weekly_or_by_hand_only_from_main_and_stays_off_until_the_owner_names_it():
    """#178: the weekly 17Lands job. The switch is the existing repository variable CATALOG_SOURCES (read by the job, which does nothing
    when `limited_17lands` is not in it); the workflow never sets it, takes the sets and formats as inputs passed through env, and
    has a timeout (a cap: the run time is measured by the first real run)."""
    wf = load(ROOT / ".github" / "workflows" / "sync-limited.yml")
    assert len(wf["on"]["schedule"]) == 1 and re.fullmatch(r"\d+ \d+ \* \* 1", wf["on"]["schedule"][0]["cron"])  # Mondays
    assert set(wf["on"]) == {"schedule", "workflow_dispatch"} and set(wf["on"]["workflow_dispatch"]["inputs"]) == {"sets", "formats", "force"}
    job = wf["jobs"]["sync"]
    assert job["if"] == "github.ref == 'refs/heads/main'" and job["environment"] == ENVIRONMENT and job["timeout-minutes"] == 120
    assert wf["concurrency"] == ENVIRONMENT
    load_step = next(s for s in job["steps"] if s.get("id") == "load")
    assert load_step["env"]["CATALOG_SOURCES"] == "${{ vars.CATALOG_SOURCES }}"
    assert set(load_step["env"]) == {"CATALOG_SOURCES", "LIMITED_SETS", "LIMITED_FORMATS", "LIMITED_FORCE"}
    text = (ROOT / ".github" / "workflows" / "sync-limited.yml").read_text(encoding="utf-8")
    assert "gh variable" not in text and "CATALOG_SOURCES=" not in text and "GITHUB_ENV" in text  # nothing here sets the variable


def test_a_monthly_reminder_issue_covers_what_the_guard_cannot_read():
    wf = load(ROOT / ".github" / "workflows" / "neon-monthly-check.yml")
    assert wf["on"]["schedule"] == [{"cron": "23 7 1 * *"}]  # the 1st of every month
    assert "workflow_dispatch" in wf["on"]


def test_no_scheduled_job_contacts_archidekt():
    """Owner rule (#79, #132): Archidekt is read on a person's request, one public deck, never searched or crawled by a
    scheduled job. The nightly conformance run must not enable the live Archidekt checks, and those checks never search."""
    workflow = (ROOT / ".github" / "workflows" / "twins-conformance.yml").read_text(encoding="utf-8")
    assert "TWINS_LIVE_ARCHIDEKT" not in workflow and "archidekt" not in workflow.lower()
    conformance = (ROOT / "tests" / "conformance" / "test_conformance.py").read_text(encoding="utf-8")
    assert "decks/v3" not in conformance.split("# -- Archidekt")[1].split("# -- Vercel")[0].replace("# Archidekt is read", "")
    assert conformance.count("@manual_archidekt") == 2  # both live Archidekt checks need the manual switch
