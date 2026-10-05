"""Twin of the slice of Vercel's REST API that ``jobs/vercel_setup.py`` uses (``api.vercel.com``).

- Environment variables: list (``/v10/projects/{p}/env``), create or upsert, change targets
  (``PATCH /v9/.../env/{id}``) and delete. Values are never listed, like the real API.
- Domains: ``/v9/projects/{p}/domains``.
- The project (``GET``/``PATCH /v9/projects/{p}``): only ``autoExposeSystemEnvs`` (off for a new
  project here, so the setup has to turn it on), which gives functions ``VERCEL_GIT_COMMIT_SHA``.
- Deployments: ``GET /v13/deployments/{id or host}`` answers the deployment a host serves now (so
  after a rollback, not the newest one), and ``POST /v13/deployments`` with ``deploymentId``
  redeploys it, which here goes live at once and takes over the project's production domains.
  The rebuild uses the source deployment's commit, or main's latest (:attr:`VercelTwin.head`)
  with ``withLatestCommit``. Commits are kept on the side (:attr:`VercelTwin.commits`): a
  deployment made with the CLI has no ``gitSource``.

Errors are Vercel's ``{"error": {"code", "message"}}``; a missing or unknown token answers 403
with ``invalidToken`` (or ``missingToken``). Times are milliseconds since the epoch, always increasing.
"""

from __future__ import annotations

import itertools
import time

from .base import Request, Twin, json_response

HOST = "api.vercel.com"


class VercelTwin(Twin):
    name = "vercel"
    hosts = (HOST,)

    def __init__(self):
        super().__init__()
        self.tokens = {"twin-vercel-token"}
        self.projects: dict[str, dict] = {}
        self.deployments: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}  # host -> deployment id serving it
        self.redeployed_from: list[str] = []
        self.head = "c1"  # main's latest commit
        self.commits: dict[str, str] = {}  # deployment id -> the commit it was built from
        self.on_lookup = None  # called after each deployment lookup: lets a test race a release in
        self._ids = itertools.count(1)
        self._last = 0
        r = self.route
        r("GET", HOST, "/v9/projects/{project}", self._get_project)
        r("PATCH", HOST, "/v9/projects/{project}", self._patch_project)
        r("GET", HOST, "/v10/projects/{project}/env", self._list_env)
        r("POST", HOST, "/v10/projects/{project}/env", self._add_env)
        r("PATCH", HOST, "/v9/projects/{project}/env/{env_id}", self._patch_env)
        r("DELETE", HOST, "/v9/projects/{project}/env/{env_id}", self._delete_env)
        r("GET", HOST, "/v9/projects/{project}/domains", self._domains)
        r("GET", HOST, "/v13/deployments/{ref}", self._get_deployment)
        r("POST", HOST, "/v13/deployments", self._redeploy)

    def error(self, status, code, details):
        return json_response(status, {"error": {"code": code, "message": details}})

    def reset(self) -> None:
        super().reset()
        self.projects.clear()
        self.deployments.clear()
        self.aliases.clear()
        self.redeployed_from.clear()
        self.commits.clear()
        self.head = "c1"
        self.on_lookup = None

    def now(self) -> int:
        self._last = max(self._last + 1, int(time.time() * 1000))
        return self._last

    # -- scenario set-up -------------------------------------------------------------------
    def add_project(self, name: str, domains=(), envs=()) -> dict:
        """``envs``: ``{"key", "target", "value"?}`` dicts, stored as they would be by hand."""
        project = {"id": f"prj_{next(self._ids)}", "name": name, "envs": [], "domains": [],
                   "autoExposeSystemEnvs": False}
        self.projects[name] = project
        for d in domains:
            self.add_domain(name, d)
        for env in envs:
            self.store_env(name, env["key"], env.get("value", "x"), env["target"])
        return project

    def add_domain(self, project: str, name: str, **fields) -> None:
        self.projects[project]["domains"].append({"name": name, "verified": True, "redirect": None,
                                                  "gitBranch": None, **fields})

    def store_env(self, project: str, key: str, value: str, target) -> dict:
        now = self.now()
        env = {"id": f"env_{next(self._ids)}", "key": key, "value": value, "type": "encrypted",
               "target": list(target), "createdAt": now, "updatedAt": now}
        self.projects[project]["envs"].append(env)
        return env

    def deploy(self, project: str, meta: dict | None = None, *, live: bool = True, commit: str | None = None) -> dict:
        """A production deployment (as from a merge to main, of ``commit`` or main's latest);
        ``live`` points the domains at it."""
        n = next(self._ids)
        dpl = {"id": f"dpl_{n}", "url": f"{project}-{n}.vercel.app", "name": project, "target": "production",
               "readyState": "READY", "createdAt": self.now(), "meta": dict(meta or {})}
        self.deployments[dpl["id"]] = dpl
        self.commits[dpl["id"]] = commit or self.head
        if live:
            self.promote(project, dpl["id"])
        return dpl

    def promote(self, project: str, deployment_id: str) -> None:
        """Point the production domains at a deployment (a promotion or an Instant Rollback)."""
        for d in self.projects[project]["domains"]:
            self.aliases[d["name"]] = deployment_id

    def env(self, project: str, key: str, target: str = "production") -> dict | None:
        return next((e for e in self.projects[project]["envs"] if e["key"] == key and target in e["target"]), None)

    def redeploys(self) -> list[str]:
        """The deployment each successful redeploy rebuilt, oldest first."""
        return list(self.redeployed_from)

    # -- the API ---------------------------------------------------------------------------
    def _auth(self, req: Request):
        auth = req.headers.get("authorization", "")
        if not auth:
            return json_response(403, {"error": {"code": "forbidden", "message": "The request is missing an authentication token",
                                                 "missingToken": True}})
        if not auth.startswith("Bearer ") or auth[7:] not in self.tokens:
            return json_response(403, {"error": {"code": "forbidden", "message": "Not authorized",
                                                 "invalidToken": True}})
        return None

    def _project(self, req: Request):
        denied = self._auth(req)
        if denied:
            return None, denied
        project = self.projects.get(req.params["project"])
        if project is None:
            return None, self.error(404, "not_found", "Project not found")
        return project, None

    @staticmethod
    def _shown(project: dict) -> dict:
        return {"id": project["id"], "name": project["name"], "autoExposeSystemEnvs": project["autoExposeSystemEnvs"]}

    def _get_project(self, req: Request):
        project, err = self._project(req)
        return err or json_response(200, self._shown(project))

    def _patch_project(self, req: Request):
        project, err = self._project(req)
        if err:
            return err
        project.update({k: v for k, v in req.json().items() if k == "autoExposeSystemEnvs"})
        return json_response(200, self._shown(project))

    @staticmethod
    def _listed(env: dict) -> dict:
        return {k: v for k, v in env.items() if k != "value"}

    def _list_env(self, req: Request):
        project, err = self._project(req)
        return err or json_response(200, {"envs": [self._listed(e) for e in project["envs"]]})

    def _add_env(self, req: Request):
        project, err = self._project(req)
        if err:
            return err
        new = req.json()
        clash = [e for e in project["envs"] if e["key"] == new["key"] and set(e["target"]) & set(new["target"])]
        if clash and req.query.get("upsert") != "true":
            return self.error(400, "ENV_ALREADY_EXISTS", f"A variable named {new['key']} already exists")
        project["envs"] = [e for e in project["envs"] if e not in clash]
        env = self.store_env(project["name"], new["key"], new["value"], new["target"])
        return json_response(201, {"created": self._listed(env)})

    def _find_env(self, project: dict, env_id: str):
        return next((e for e in project["envs"] if e["id"] == env_id), None)

    def _patch_env(self, req: Request):
        project, err = self._project(req)
        if err:
            return err
        env = self._find_env(project, req.params["env_id"])
        if env is None:
            return self.error(404, "not_found", "Environment variable not found")
        env.update({k: v for k, v in req.json().items() if k in ("target", "value")}, updatedAt=self.now())
        return json_response(200, self._listed(env))

    def _delete_env(self, req: Request):
        project, err = self._project(req)
        if err:
            return err
        env = self._find_env(project, req.params["env_id"])
        if env is None:
            return self.error(404, "not_found", "Environment variable not found")
        project["envs"].remove(env)
        return json_response(200, self._listed(env))

    def _domains(self, req: Request):
        project, err = self._project(req)
        return err or json_response(200, {"domains": project["domains"]})

    def _get_deployment(self, req: Request):
        denied = self._auth(req)
        if denied:
            return denied
        ref = req.params["ref"]
        dpl = self.deployments.get(self.aliases.get(ref, ref)) or next(
            (d for d in self.deployments.values() if d["url"] == ref), None)
        if self.on_lookup:
            self.on_lookup()
        return json_response(200, dpl) if dpl else self.error(404, "not_found", "Deployment not found")

    def _redeploy(self, req: Request):
        denied = self._auth(req)
        if denied:
            return denied
        body = req.json() or {}
        source = self.deployments.get(body.get("deploymentId", ""))
        if source is None or body.get("name") != source["name"]:
            return self.error(404, "not_found", "Deployment not found")
        self.redeployed_from.append(source["id"])
        commit = self.head if body.get("withLatestCommit") else self.commits[source["id"]]
        return json_response(200, self.deploy(source["name"], {**source["meta"], **(body.get("meta") or {})},
                                              commit=commit))
