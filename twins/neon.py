"""Twin of the slice of Neon's console API that ``jobs/neon_usage.py`` reads (``console.neon.tech/api/v2``): one project, with
its consumption for the current billing period. Bearer API keys; a missing or unknown key answers 401 with Neon's error body
(``request_id``, ``code``, ``message``: read 2026-10-07, ``tests/conformance``).

The project object is a subset of the real one (docs: api-docs.neon.tech/reference/getproject): ``compute_time_seconds`` (CPU
seconds, so compute-unit hours at one CU), ``active_time_seconds``, ``data_transfer_bytes`` (egress over the period),
``written_data_bytes``, ``synthetic_storage_size`` (data plus WAL over every branch, in bytes) and the period's start and end. The
real key set was checked only against Neon's documentation and the unauthenticated error: a live check needs an API key
(``NEON_CONFORMANCE_TOKEN``, ``NEON_CONFORMANCE_PROJECT``), which only the owner can supply (docs/catalog-design.md, Neon budget).
"""

from __future__ import annotations

import uuid

import httpx

from .base import Request, Twin, json_response

HOST = "console.neon.tech"


class NeonTwin(Twin):
    name = "neon"
    hosts = (HOST,)

    def __init__(self):
        super().__init__()
        self.keys = {"twin-neon-key"}
        self.projects: dict[str, dict] = {}
        self.route("GET", HOST, "/api/v2/projects/{project}", self._get)

    def add_project(self, project_id: str = "twin-project-123", *, cu_hours: float = 0.0, transfer_gb: float = 0.0, storage_mb: float = 100.0,
                    period: tuple[str, str] = ("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z")) -> dict:
        project = {"id": project_id, "name": "the-vault", "region_id": "aws-eu-west-2", "platform_id": "aws", "pg_version": 16,
                   "compute_time_seconds": int(cu_hours * 3600), "active_time_seconds": int(cu_hours * 3600 * 2),
                   "data_transfer_bytes": int(transfer_gb * 1024 ** 3),
                   "written_data_bytes": 1_000_000, "synthetic_storage_size": int(storage_mb * 1024 ** 2),
                   "consumption_period_start": period[0], "consumption_period_end": period[1]}
        self.projects[project_id] = project
        return project

    def error(self, status, code, details):
        return json_response(status, {"request_id": str(uuid.uuid4()), "code": code, "message": details})

    def _get(self, req: Request) -> httpx.Response:
        auth = req.headers.get("authorization", "")
        if not auth.lower().startswith("bearer ") or auth.split(" ", 1)[1] not in self.keys:
            return self.error(401, "", "supplied credentials do not pass authentication")
        project = self.projects.get(req.params["project"])
        if project is None:
            return self.error(404, "", "project not found")
        return json_response(200, {"project": project})
