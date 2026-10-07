"""Twin of the slice of GitHub's REST API that ``jobs/budget_alert.py`` uses (``api.github.com``): list a repository's issues,
open one, update one and comment on one. Answers the way the real API does (read 2026-10-07, ``tests/conformance``):

- Reading a public repository's issues needs no token. The list includes pull requests (they carry a ``pull_request`` key).
- Writing needs a token (``Authorization: Bearer ...``): none answers 401 ``Requires authentication``; a token that lacks
  ``issues: write`` (a workflow's ``GITHUB_TOKEN`` with read-only permissions) answers 403 ``Resource not accessible by integration``.
- Every request needs a ``User-Agent`` (GitHub refuses a request without one with 403).
- Labels named when opening an issue are created if they do not exist.

The issue shape is a subset of the real one: every field it has is real, none is invented.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

import httpx

from .base import Request, Twin, json_response

HOST = "api.github.com"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class GitHubTwin(Twin):
    name = "github"
    hosts = (HOST,)

    def __init__(self):
        super().__init__()
        self.tokens: dict[str, str] = {"twin-issues-write": "write", "twin-issues-read": "read"}  # token -> its issues permission
        self.repos: dict[str, list[dict]] = {}
        self.comments: dict[tuple[str, int], list[dict]] = {}
        self._numbers: dict[str, itertools.count] = {}
        self.route("GET", HOST, "/repos/{owner}/{repo}/issues", self._list)
        self.route("POST", HOST, "/repos/{owner}/{repo}/issues", self._create)
        self.route("PATCH", HOST, "/repos/{owner}/{repo}/issues/{number}", self._update)
        self.route("POST", HOST, "/repos/{owner}/{repo}/issues/{number}/comments", self._comment)

    # -- state ---------------------------------------------------------------------------
    def add_issue(self, repo: str, title: str, body: str = "", *, labels: tuple[str, ...] = (), state: str = "open", pull_request: bool = False) -> dict:
        issues = self.repos.setdefault(repo, [])
        number = next(self._numbers.setdefault(repo, itertools.count(1)))
        issue = {"number": number, "title": title, "body": body, "state": state, "labels": [{"name": n} for n in labels],
                 "html_url": f"https://github.com/{repo}/{'pull' if pull_request else 'issues'}/{number}",
                 "user": {"login": "github-actions[bot]"}, "comments": 0, "created_at": _now(), "updated_at": _now()}
        if pull_request:
            issue["pull_request"] = {"url": f"https://api.github.com/repos/{repo}/pulls/{number}"}
        issues.insert(0, issue)
        return issue

    def open_issues(self, repo: str) -> list[dict]:
        return [i for i in self.repos.get(repo, []) if i["state"] == "open"]

    def reset(self) -> None:
        super().reset()
        self.repos.clear()
        self.comments.clear()
        self._numbers.clear()

    def error(self, status, code, details):
        return json_response(status, {"message": details, "status": str(status)})

    # -- the API -------------------------------------------------------------------------
    def _guard(self, req: Request, write: bool) -> httpx.Response | None:
        if not req.headers.get("user-agent"):
            return json_response(403, {"message": "Request forbidden by administrative rules. Please make sure your request has a User-Agent header "
                                                  "(https://docs.github.com/en/rest/overview/resources-in-the-rest-api#user-agent-required).", "status": "403"})
        if not write:
            return None
        auth = req.headers.get("authorization", "")
        token = auth.split(" ", 1)[1] if " " in auth else ""
        if not token:
            return json_response(401, {"message": "Requires authentication", "documentation_url": "https://docs.github.com/rest", "status": "401"})
        if token not in self.tokens:
            return json_response(401, {"message": "Bad credentials", "documentation_url": "https://docs.github.com/rest", "status": "401"})
        if self.tokens[token] != "write":
            return json_response(403, {"message": "Resource not accessible by integration", "documentation_url": "https://docs.github.com/rest", "status": "403"})
        return None

    def _repo(self, req: Request) -> str:
        return f"{req.params['owner']}/{req.params['repo']}"

    def _list(self, req: Request) -> httpx.Response:
        if (refused := self._guard(req, write=False)):
            return refused
        state = req.query.get("state", "open")
        per_page = max(1, min(int(req.query.get("per_page", 30)), 100))
        page = max(1, int(req.query.get("page", 1)))
        issues = [i for i in self.repos.get(self._repo(req), []) if state == "all" or i["state"] == state]
        wanted = [n for n in req.query.get("labels", "").split(",") if n]
        issues = [i for i in issues if all(n in {label["name"] for label in i["labels"]} for n in wanted)]
        return json_response(200, issues[(page - 1) * per_page: page * per_page])

    def _create(self, req: Request) -> httpx.Response:
        if (refused := self._guard(req, write=True)):
            return refused
        body = req.json() or {}
        if not str(body.get("title") or "").strip():
            return json_response(422, {"message": "Validation Failed", "errors": [{"resource": "Issue", "code": "missing_field", "field": "title"}], "status": "422"})
        issue = self.add_issue(self._repo(req), body["title"], body.get("body") or "", labels=tuple(body.get("labels") or ()))
        return json_response(201, issue)

    def _find(self, req: Request) -> dict | None:
        number = int(req.params["number"])
        return next((i for i in self.repos.get(self._repo(req), []) if i["number"] == number and "pull_request" not in i), None)

    def _update(self, req: Request) -> httpx.Response:
        if (refused := self._guard(req, write=True)):
            return refused
        issue = self._find(req)
        if issue is None:
            return json_response(404, {"message": "Not Found", "status": "404"})
        body = req.json() or {}
        for key in ("title", "body", "state"):
            if key in body:
                issue[key] = body[key]
        issue["updated_at"] = _now()
        return json_response(200, issue)

    def _comment(self, req: Request) -> httpx.Response:
        if (refused := self._guard(req, write=True)):
            return refused
        issue = self._find(req)
        if issue is None:
            return json_response(404, {"message": "Not Found", "status": "404"})
        comment = {"id": len(self.comments) + 1, "body": (req.json() or {}).get("body", ""), "user": {"login": "github-actions[bot]"}, "created_at": _now()}
        self.comments.setdefault((self._repo(req), issue["number"]), []).append(comment)
        issue["comments"] += 1
        return json_response(201, comment)
