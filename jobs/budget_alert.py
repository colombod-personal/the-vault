"""Open or update the GitHub issue that says a Neon limit is near (#64), from a workflow, with the workflow's own token.

    GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo ALERT_KIND=storage ALERT_MESSAGE="..." python -m jobs.budget_alert
    python -m jobs.budget_alert --from-env        # every alert the workflow collected (STORAGE_ALERT, USAGE_ALERT, MONTHLY, TERMS_REMINDER)

How the pieces fit (docs/catalog-design.md, "Neon budget"):

1. ``jobs/db_budget.py`` (storage) and ``jobs/neon_usage.py`` (compute hours, network transfer) **fail the job** at 70%, after
   the job's own work, and call :func:`announce`, which writes the message to the step's ``GITHUB_OUTPUT``.
2. A second job in the same workflow, ``alert``, holds only ``issues: write`` and the run's ``github.token``: no database address,
   no Vercel token, no environment. It receives the message as an input and calls this module, which opens an issue, or
   updates the open one for that kind (one issue per kind, found by a hidden marker in its body) and comments when the
   figure moved by five points or more. Nothing is ever closed here: closing an alert is a person's decision.
3. ``.github/workflows/neon-monthly-check.yml`` opens a reminder issue on the 1st of every month, for the things that cannot be
   read from the database or without an API key.
4. ``.github/workflows/limited-terms-monthly.yml`` does the same for the 17Lands terms re-read (#417): kind ``limited-terms``, whose
   text and date come from ``jobs/limited_terms.py`` and ``docs/compliance.md``.

There are no secrets in this code: the token comes from the environment and is only sent to ``api.github.com``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import httpx

from jobs import limited_terms

API = "https://api.github.com"
USER_AGENT = "the-vault-budget-guard/0.1 (+https://github.com/colombod-personal/the-vault)"
LABEL = "neon-budget"
COMMENT_EVERY_POINTS = 5
TITLES = {
    "storage": "Neon storage is at 70% or more of the free plan (budget guard)",
    "usage": "Neon compute or network use is at 70% or more of the free plan this month (budget guard)",
    "monthly-check": "Neon monthly check: compute hours, network transfer and restore history",
    "limited-terms": "17Lands terms re-read: the data set pages and the licence sentence (monthly reminder)",
}
LABELS = {"limited-terms": "area:data"}  # every other kind uses LABEL
MONTHLY_BODY = """It is the first of the month. The budget guard watches database **storage** on every job run and, when a Neon API key is
set (`NEON_API_KEY` secret and `NEON_PROJECT_ID` variable in the `vercel-production` environment), compute hours and network transfer
too. If no key is set, or to double-check, read these in the Neon console (Billing, Usage) and write them below:

- [ ] Compute: CU-hours used this month ____ of 100 (alert at 70)
- [ ] Network transfer this month ____ of 5 GB (alert at 3.5 GB)
- [ ] Storage ____ of 1 GB (the jobs check this every day; alert at 700 MB)
- [ ] Restore history: does it count toward storage? (check once, after a real job run; note the answer here)

Limits as checked on 2026-10-04 against Neon's free plan page; see `docs/catalog-design.md` ("Neon budget"). Close this issue
when the check is done; the next one opens on the 1st of next month.
"""


def announce(kind: str, message: str) -> None:
    """Hand an alert to the workflow: the step's output ``<kind>_alert`` (read by the ``alert`` job). A no-op outside Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"{kind}_alert={' '.join(message.split())}\n")


class GitHub:
    def __init__(self, token: str, repo: str, transport: httpx.BaseTransport | None = None):
        self.repo = repo
        self.client = httpx.Client(transport=transport, base_url=API, timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT})

    def open_issue_with(self, marker_kind: str) -> dict | None:
        """The open issue (never a pull request) carrying this kind's hidden marker, or None. Looks at the newest 300 open issues."""
        for page in (1, 2, 3):
            res = self.client.get(f"/repos/{self.repo}/issues", params={"state": "open", "per_page": 100, "page": page})
            res.raise_for_status()
            batch = res.json()
            for issue in batch:
                if "pull_request" not in issue and f"<!-- vault-budget-alert:{marker_kind}" in (issue.get("body") or ""):
                    return issue
            if len(batch) < 100:
                return None
        return None

    def create(self, title: str, body: str, label: str = LABEL) -> dict:
        res = self.client.post(f"/repos/{self.repo}/issues", json={"title": title, "body": body, "labels": [label]})
        if res.status_code == 422:  # the label could not be used: the alert matters more than its label
            res = self.client.post(f"/repos/{self.repo}/issues", json={"title": title, "body": body})
        res.raise_for_status()
        return res.json()

    def update(self, number: int, body: str) -> None:
        self.client.patch(f"/repos/{self.repo}/issues/{number}", json={"body": body}).raise_for_status()

    def comment(self, number: int, body: str) -> None:
        self.client.post(f"/repos/{self.repo}/issues/{number}/comments", json={"body": body}).raise_for_status()


def percent_in(text: str) -> int | None:
    found = re.search(r"\b(\d{1,4})%", text)
    return int(found.group(1)) if found else None


def upsert_issue(github: GitHub, kind: str, message: str) -> dict:
    """Open the issue for ``kind``, or bring the open one up to date. Returns ``{"action", "number", "url"}``."""
    title = TITLES[kind]
    used = percent_in(message)
    marker = f"<!-- vault-budget-alert:{kind}" + (f" used={used}" if used is not None else "") + " -->"
    detail = MONTHLY_BODY if kind == "monthly-check" else limited_terms.reminder_body() if kind == "limited-terms" else (
        f"{message}\n\nThe budget guard failed the job on purpose so that this is noticed. What to do: `docs/catalog-design.md`, "
        "\"Neon budget\" (free space, thin or drop data, or move plan). This issue is updated while the figure stays above 70%; "
        "close it when it is below.")
    body = f"{marker}\n{detail}"
    existing = github.open_issue_with(kind)
    if existing is None:
        made = github.create(title, body, LABELS.get(kind, LABEL))
        return {"action": "created", "number": made["number"], "url": made.get("html_url")}
    recorded = re.search(r"<!-- vault-budget-alert:\S+ used=(\d+)", existing.get("body") or "")
    was = int(recorded.group(1)) if recorded else None
    github.update(existing["number"], body)
    if kind in ("monthly-check", "limited-terms"):
        github.comment(existing["number"], "A new month has started and the previous check is still open: please do it now.")
    elif used is not None and (was is None or abs(used - was) >= COMMENT_EVERY_POINTS):
        github.comment(existing["number"], message)
    return {"action": "updated", "number": existing["number"], "url": existing.get("html_url")}


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> list[dict]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-env", action="store_true", help="STORAGE_ALERT, USAGE_ALERT, MONTHLY and TERMS_REMINDER (non-empty ones)")
    args = parser.parse_args(argv)
    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        raise SystemExit("budget_alert: GITHUB_TOKEN and GITHUB_REPOSITORY are required (a workflow provides both)")
    if args.from_env:
        wanted = [(k, os.environ.get(v, "")) for k, v in (("storage", "STORAGE_ALERT"), ("usage", "USAGE_ALERT"), ("monthly-check", "MONTHLY"),
                                                          ("limited-terms", "TERMS_REMINDER"))]
        wanted = [(k, m) for k, m in wanted if m]
    else:
        wanted = [(os.environ.get("ALERT_KIND", ""), os.environ.get("ALERT_MESSAGE", "") or "monthly")]
    if not wanted or any(k not in TITLES for k, _ in wanted):
        raise SystemExit(f"budget_alert: nothing to report, or an unknown kind (one of {', '.join(TITLES)})")
    github = GitHub(token, repo, transport)
    try:
        results = [upsert_issue(github, kind, message) | {"kind": kind} for kind, message in wanted]
    except httpx.HTTPStatusError as exc:
        reason = exc.response.json().get("message", "") if exc.response.headers.get("content-type", "").startswith("application/json") else ""
        hint = (" The workflow's token needs `permissions: issues: write` for this job." if exc.response.status_code in (401, 403) else "")
        raise SystemExit(f"budget_alert: GitHub answered {exc.response.status_code} ({reason}) to {exc.request.method} {exc.request.url.path}.{hint}") from exc
    print(json.dumps(results))
    return results


if __name__ == "__main__":
    main()
    sys.exit(0)
