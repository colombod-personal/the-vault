"""Compute hours and network transfer used this month on Neon's free plan, read from Neon's API (#64).

    NEON_API_KEY=... NEON_PROJECT_ID=... python -m jobs.neon_usage

Storage is checked from inside the database by ``jobs/db_budget.py``; compute hours and egress are only visible to Neon, so this
asks the console API for the project (``GET /api/v2/projects/{id}``) and compares the consumption of the current billing period
with the free plan's limits (checked 2026-10-04, docs/catalog-design.md): 100 CU-hours, 5 GB of transfer and 1 GB of storage.
At 70% of any of them the job **fails** with a clear message, after announcing it to the workflow, whose ``alert`` job opens
or updates a GitHub issue (``jobs/budget_alert.py``).

What is automated, honestly: this runs only when the owner has created a Neon API key and stored it as the ``NEON_API_KEY``
secret (and the project's id as the ``NEON_PROJECT_ID`` variable) in the ``vercel-production`` environment. Without them it prints a
notice and succeeds, and the monthly reminder workflow (``neon-monthly-check.yml``) asks a person to read the console instead.
``compute_time_seconds`` is Neon's count of CPU seconds; at one vCPU per compute unit it is compute-unit seconds, so dividing by 3600 gives
CU-hours. That is an estimate: Neon's console is the authority on what is billed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx

from jobs import budget_alert

API = "https://console.neon.tech/api/v2"
LIMITS = {"compute": ("CU-hours", 100.0), "transfer": ("GB of network transfer", 5.0), "storage": ("GB of storage", 1.0)}
WARN_AT = 0.70
USER_AGENT = "the-vault-budget-guard/0.1 (+https://github.com/colombod-personal/the-vault)"


def fetch(project_id: str, api_key: str, transport: httpx.BaseTransport | None = None) -> dict:
    """The project's consumption this period: ``{"compute": CU-hours, "transfer": GB, "storage": GB, "period": (start, end)}``."""
    with httpx.Client(transport=transport, timeout=30, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json",
                                                                 "User-Agent": USER_AGENT}) as client:
        res = client.get(f"{API}/projects/{project_id}")
    if res.status_code == 401:
        raise SystemExit("neon_usage: Neon refused the API key (401). Create a new key in the Neon console and update the NEON_API_KEY secret.")
    if res.status_code != 200:
        raise SystemExit(f"neon_usage: Neon answered {res.status_code} for the project; check NEON_PROJECT_ID.")
    project = res.json()["project"]
    return {"compute": project["compute_time_seconds"] / 3600, "transfer": project["data_transfer_bytes"] / 1024 ** 3,
            "storage": project["synthetic_storage_size"] / 1024 ** 3,
            "period": (project.get("consumption_period_start"), project.get("consumption_period_end"))}


def evaluate(usage: dict) -> dict:
    out = {}
    for key, (unit, limit) in LIMITS.items():
        out[key] = {"used": round(usage[key], 2), "limit": limit, "unit": unit, "share": usage[key] / limit}
    return out


def message(report: dict, period) -> str:
    over = [f"{r['used']:.1f} of {r['limit']:g} {r['unit']} ({r['share']:.0%})" for r in report.values() if r["share"] >= WARN_AT]
    return (f"Neon usage this billing period ({(period[0] or '?')[:10]} to {(period[1] or '?')[:10]}) has reached 70% of the free plan: "
            + "; ".join(over) + f". Highest: {max(r['share'] for r in report.values()):.0%}.")


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None, now: datetime | None = None) -> dict:
    key, project = os.environ.get("NEON_API_KEY", ""), os.environ.get("NEON_PROJECT_ID", "")
    if not key or not project:
        print("::notice::Neon compute and network use are not tracked automatically: NEON_API_KEY (secret) and NEON_PROJECT_ID (variable) "
              "are not set. The monthly reminder issue asks for a manual check in the Neon console.")
        print(json.dumps({"neon_usage": "not configured"}))
        return {}
    usage = fetch(project, key, transport)
    report = evaluate(usage)
    print(json.dumps({"neon_usage": {k: {"used": r["used"], "limit": r["limit"], "share": f"{r['share']:.0%}"} for k, r in report.items()},
                      "period": usage["period"], "checked": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}))
    if any(r["share"] >= WARN_AT for r in report.values()):
        text = message(report, usage["period"])
        budget_alert.announce("usage", text)
        print(f"::error::{text}")
        raise SystemExit(f"neon_usage: {text} Failing the job on purpose so that it is noticed; a GitHub issue is opened or updated.")
    return report


if __name__ == "__main__":
    main()
