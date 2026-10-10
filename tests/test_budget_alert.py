"""The budget guard's issue and the Neon usage check (#64), against the GitHub and Neon twins.

What is automated, and tested here with transport stubs (no secret, no real service): a job that finds storage, compute hours or
network transfer at 70% fails with a clear message and hands it to the workflow, and the workflow's alert job opens, or updates,
one GitHub issue per kind. What is not automated, and says so: compute hours and transfer are only read when the owner supplies a
Neon API key; without one, `jobs.neon_usage` says so and the monthly reminder issue asks a person to look."""

import json
from datetime import date

import httpx
import pytest

from jobs import budget_alert, db_budget, limited_terms, neon_usage
from twins import Universe
from vault.db import Database

REPO = "colombod-personal/the-vault"


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def actions(monkeypatch, tmp_path):
    """The environment a workflow gives the alert job: the run's token and repository."""
    monkeypatch.setenv("GITHUB_TOKEN", "twin-issues-write")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    for name in ("ALERT_KIND", "ALERT_MESSAGE", "STORAGE_ALERT", "USAGE_ALERT", "MONTHLY", "TERMS_REMINDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    return tmp_path / "out"


STORAGE = "Neon storage is at 72% of the 1024 MB free plan (737 MB; alert at 70%, imports stop at 85%). Largest tables: price_snapshots 400 MB."


def test_a_first_alert_opens_one_issue_with_the_message_and_the_hidden_marker(universe, actions, monkeypatch):
    monkeypatch.setenv("STORAGE_ALERT", STORAGE)
    [result] = budget_alert.main(["--from-env"], transport=universe.transport)
    assert result["action"] == "created" and result["kind"] == "storage" and result["number"] == 1
    [issue] = universe.github.open_issues(REPO)
    assert issue["title"] == budget_alert.TITLES["storage"]
    assert STORAGE in issue["body"] and "<!-- vault-budget-alert:storage used=72 -->" in issue["body"] and "docs/catalog-design.md" in issue["body"]
    assert [label["name"] for label in issue["labels"]] == ["neon-budget"]
    call = next(c for c in universe.github.calls if c.method == "POST")
    assert call.headers["authorization"] == "Bearer twin-issues-write" and call.headers["user-agent"].startswith("the-vault-budget-guard")


def test_a_second_alert_updates_the_open_issue_and_comments_only_when_the_figure_moved(universe, actions, monkeypatch):
    monkeypatch.setenv("STORAGE_ALERT", STORAGE)
    budget_alert.main(["--from-env"], transport=universe.transport)
    monkeypatch.setenv("STORAGE_ALERT", STORAGE.replace("72%", "73%").replace("737", "747"))  # a point: update, no new comment
    [again] = budget_alert.main(["--from-env"], transport=universe.transport)
    assert again["action"] == "updated" and again["number"] == 1 and len(universe.github.open_issues(REPO)) == 1
    assert "747 MB" in universe.github.open_issues(REPO)[0]["body"] and "used=73" in universe.github.open_issues(REPO)[0]["body"]
    assert universe.github.comments == {}
    monkeypatch.setenv("STORAGE_ALERT", STORAGE.replace("72%", "79%"))  # six points since the issue was last written: a comment says so
    budget_alert.main(["--from-env"], transport=universe.transport)
    assert [c["body"] for c in universe.github.comments[(REPO, 1)]] == [STORAGE.replace("72%", "79%")]


def test_a_closed_issue_is_not_reopened_a_new_one_is_opened_and_pull_requests_are_ignored(universe, actions, monkeypatch):
    universe.github.add_issue(REPO, budget_alert.TITLES["storage"], "<!-- vault-budget-alert:storage used=70 -->", state="closed")
    universe.github.add_issue(REPO, "A pull request that quotes the marker", "<!-- vault-budget-alert:storage used=71 -->", pull_request=True)
    monkeypatch.setenv("STORAGE_ALERT", STORAGE)
    [result] = budget_alert.main(["--from-env"], transport=universe.transport)
    assert result["action"] == "created" and result["number"] == 3
    assert [i["number"] for i in universe.github.open_issues(REPO) if "pull_request" not in i] == [3]


def test_storage_and_usage_alerts_are_two_issues(universe, actions, monkeypatch):
    monkeypatch.setenv("STORAGE_ALERT", STORAGE)
    monkeypatch.setenv("USAGE_ALERT", "Neon usage this billing period has reached 70% of the free plan: 71.0 of 100 CU-hours (71%). Highest: 71%.")
    results = budget_alert.main(["--from-env"], transport=universe.transport)
    assert [(r["kind"], r["action"]) for r in results] == [("storage", "created"), ("usage", "created")]
    assert {i["title"] for i in universe.github.open_issues(REPO)} == {budget_alert.TITLES["storage"], budget_alert.TITLES["usage"]}


def test_the_monthly_reminder_opens_a_checklist_and_nags_when_last_months_is_still_open(universe, actions, monkeypatch):
    monkeypatch.setenv("MONTHLY", "yes")
    [first] = budget_alert.main(["--from-env"], transport=universe.transport)
    assert first["action"] == "created"
    body = universe.github.open_issues(REPO)[0]["body"]
    assert "CU-hours used this month" in body and "Network transfer" in body and "Restore history" in body and "console" in body
    [second] = budget_alert.main(["--from-env"], transport=universe.transport)
    assert second["action"] == "updated" and "still open" in universe.github.comments[(REPO, 1)][0]["body"]


def test_the_17lands_terms_reminder_lists_the_pages_and_the_licence_sentence_and_says_how_old_the_reading_is(universe, actions, monkeypatch):
    """#417: a monthly issue (limited-terms-monthly.yml), its own marker and label, separate from the Neon check."""
    monkeypatch.setenv("TERMS_REMINDER", "yes")
    monkeypatch.setenv("MONTHLY", "yes")
    monkeypatch.setattr(limited_terms, "today", lambda: date(2026, 12, 1))  # the date recorded in docs/compliance.md is 2026-10-09
    results = budget_alert.main(["--from-env"], transport=universe.transport)
    assert [(r["kind"], r["action"]) for r in results] == [("monthly-check", "created"), ("limited-terms", "created")]  # two issues
    issue = next(i for i in universe.github.open_issues(REPO) if i["title"] == budget_alert.TITLES["limited-terms"])
    for _, url in limited_terms.PAGES:
        assert url in issue["body"]
    assert limited_terms.LICENCE_SENTENCE in issue["body"] and "<!-- vault-budget-alert:limited-terms -->" in issue["body"]
    assert "**2026-10-09**, 53 days ago" in issue["body"] and "Not due yet" in issue["body"] and "docs/compliance.md" in issue["body"]
    assert [label["name"] for label in issue["labels"]] == ["area:data"]
    [again] = [r for r in budget_alert.main(["--from-env"], transport=universe.transport) if r["kind"] == "limited-terms"]
    assert again["action"] == "updated" and "still open" in universe.github.comments[(REPO, issue["number"])][0]["body"]


@pytest.mark.parametrize("day, words", [(date(2027, 1, 7), "**due**"), (date(2027, 3, 1), "The job is refusing to run")])
def test_the_terms_reminder_says_when_a_reading_is_due_and_when_the_job_is_already_refusing(day, words):
    text = limited_terms.reminder_body(now=day)
    assert words in text and f"{(day - date(2026, 10, 9)).days} days ago" in text


def test_a_token_that_cannot_write_issues_fails_with_the_permission_to_add(universe, actions, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "twin-issues-read")
    monkeypatch.setenv("STORAGE_ALERT", STORAGE)
    with pytest.raises(SystemExit, match=r"403 \(Resource not accessible by integration\).*issues: write"):
        budget_alert.main(["--from-env"], transport=universe.transport)
    assert universe.github.open_issues(REPO) == []


def test_without_a_token_or_a_message_it_refuses_to_run(universe, actions, monkeypatch):
    with pytest.raises(SystemExit, match="nothing to report"):
        budget_alert.main(["--from-env"], transport=universe.transport)
    monkeypatch.delenv("GITHUB_TOKEN")
    with pytest.raises(SystemExit, match="GITHUB_TOKEN and GITHUB_REPOSITORY are required"):
        budget_alert.main(["--from-env"], transport=universe.transport)


def test_a_label_github_refuses_does_not_stop_the_alert():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, json.loads(request.content or b"{}")))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if len(seen) == 2:
            return httpx.Response(422, json={"message": "Validation Failed"})
        return httpx.Response(201, json={"number": 9, "html_url": "https://github.com/x/y/issues/9"})

    github = budget_alert.GitHub("t", "x/y", httpx.MockTransport(handler))
    assert budget_alert.upsert_issue(github, "storage", STORAGE)["number"] == 9
    assert seen[1][1]["labels"] == ["neon-budget"] and "labels" not in seen[2][1]


# -- the whole path: the job fails, the workflow hands the message on, the issue opens ---------------------------------------------

@pytest.fixture
def db(database_url):
    database = Database(database_url)
    with database.sessions() as session:
        yield session
    database.engine.dispose()


def test_storage_at_70_percent_ends_as_a_failed_job_and_an_open_issue(db, universe, actions, monkeypatch):
    monkeypatch.setenv("NEON_STORAGE_LIMIT_MB", str(db_budget.usage(db)["database_mb"] / 0.72))
    with pytest.raises(SystemExit, match="Failing the job on purpose"):  # the job's last step
        db_budget.check(db, stage="after the price sync", fail_at_warn=True)
    handed_on = dict(line.split("=", 1) for line in actions.read_text(encoding="utf-8").splitlines())  # what the workflow's outputs hold
    monkeypatch.setenv("STORAGE_ALERT", handed_on["storage_alert"])  # the alert job's input
    [result] = budget_alert.main(["--from-env"], transport=universe.transport)
    assert result["action"] == "created"
    [issue] = universe.github.open_issues(REPO)
    assert "72% of the" in issue["body"] and "Largest tables" in issue["body"] and "<!-- vault-budget-alert:storage used=72 -->" in issue["body"]


# -- Neon compute hours and network transfer ---------------------------------------------------------------------------------------

@pytest.fixture
def neon(monkeypatch, tmp_path):
    monkeypatch.setenv("NEON_API_KEY", "twin-neon-key")
    monkeypatch.setenv("NEON_PROJECT_ID", "twin-project-123")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    return tmp_path / "out"


def test_without_an_api_key_it_says_so_and_does_not_fail(universe, monkeypatch, capsys):
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    monkeypatch.delenv("NEON_PROJECT_ID", raising=False)
    assert neon_usage.main(transport=universe.transport) == {}
    out = capsys.readouterr().out
    assert "not tracked automatically" in out and "monthly reminder" in out and universe.neon.calls == []


def test_below_70_percent_of_every_limit_it_reports_and_passes(universe, neon, capsys):
    universe.neon.add_project(cu_hours=40, transfer_gb=1, storage_mb=300)
    report = neon_usage.main(transport=universe.transport)
    assert round(report["compute"]["share"], 2) == 0.40 and report["compute"]["unit"] == "CU-hours"
    printed = json.loads(capsys.readouterr().out.splitlines()[0])["neon_usage"]
    assert printed["compute"] == {"used": 40.0, "limit": 100.0, "share": "40%"}
    assert not neon.exists()  # nothing was announced


def test_at_70_percent_of_the_compute_hours_it_fails_and_hands_the_message_on(universe, neon):
    universe.neon.add_project(cu_hours=71.5, transfer_gb=1, storage_mb=300)
    with pytest.raises(SystemExit) as stop:
        neon_usage.main(transport=universe.transport)
    assert "71.5 of 100 CU-hours (72%)" in str(stop.value) and "Failing the job on purpose" in str(stop.value)
    line = neon.read_text(encoding="utf-8").strip()
    assert line.startswith("usage_alert=") and "2026-10-01 to 2026-11-01" in line and "\n" not in line


def test_network_transfer_and_storage_count_too(universe, neon):
    universe.neon.add_project(cu_hours=1, transfer_gb=3.6, storage_mb=100)
    with pytest.raises(SystemExit, match=r"3\.6 of 5 GB of network transfer"):
        neon_usage.main(transport=universe.transport)
    universe.neon.add_project(cu_hours=1, transfer_gb=0, storage_mb=800)
    with pytest.raises(SystemExit, match="GB of storage"):
        neon_usage.main(transport=universe.transport)


def test_a_refused_key_or_an_unknown_project_is_a_clear_failure(universe, neon, monkeypatch):
    universe.neon.add_project()
    monkeypatch.setenv("NEON_API_KEY", "an-old-key")
    with pytest.raises(SystemExit, match="refused the API key"):
        neon_usage.main(transport=universe.transport)
    monkeypatch.setenv("NEON_API_KEY", "twin-neon-key")
    monkeypatch.setenv("NEON_PROJECT_ID", "no-such-project")
    with pytest.raises(SystemExit, match="check NEON_PROJECT_ID"):
        neon_usage.main(transport=universe.transport)
    assert not neon.exists()


def test_the_usage_alert_reaches_a_github_issue_too(universe, neon, actions, monkeypatch):
    universe.neon.add_project(cu_hours=80)
    with pytest.raises(SystemExit):
        neon_usage.main(transport=universe.transport)
    monkeypatch.setenv("USAGE_ALERT", neon.read_text(encoding="utf-8").split("=", 1)[1].strip())
    [result] = budget_alert.main(["--from-env"], transport=universe.transport)
    assert result["kind"] == "usage" and "80.0 of 100 CU-hours" in universe.github.open_issues(REPO)[0]["body"]
