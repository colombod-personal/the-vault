"""Check a deployed Vault from the outside: pages, API, auth walls, agents, headers, region.

    python -m jobs.smoke_test https://the-vault.vercel.app

Writes a table to stdout (and to the GitHub job summary). Exits 1 if something is broken.
A deployment that answers 503 "not configured yet" is reported as such, not as broken.
"""

from __future__ import annotations

import json
import os
import sys

import httpx


def run(base: str, transport: httpx.BaseTransport | None = None) -> tuple[list[tuple[str, bool, str]], bool]:
    base = base.rstrip("/")
    results: list[tuple[str, bool, str]] = []
    configured = True
    headers = {"User-Agent": "the-vault-smoke-test"}
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    if bypass:  # Vercel Deployment Protection (previews): the project's automation bypass
        headers["x-vercel-protection-bypass"] = bypass
    with httpx.Client(base_url=base, timeout=30, follow_redirects=False, transport=transport, headers=headers) as http:
        first = http.get("/")
        location = first.headers.get("location", "")
        walled = (first.status_code in (301, 302, 303, 307, 308) and "vercel.com" in location) or \
            (first.status_code == 401 and "vercel" in first.text.lower() and "The Vault" not in first.text)
        if walled:  # Vercel Authentication: a redirect to vercel.com's login (or its 401 page)
            return [("Deployment protection", True, "this deployment is behind Vercel Authentication; set the "
                     "VERCEL_AUTOMATION_BYPASS_SECRET repository secret to test it")], False

        def check(name, method, path, want, test=None, **kw):
            try:
                res = http.request(method, path, **kw)
            except httpx.HTTPError as exc:
                results.append((name, False, f"{type(exc).__name__}: {exc}"))
                return None
            ok = res.status_code == want and (test is None or test(res))
            results.append((name, ok, f"{res.status_code} {res.headers.get('content-type', '')[:40]}"))
            return res

        health = check("GET /api/health", "GET", "/api/health", 200)
        if health is not None and health.status_code == 503:
            configured = False
            results[-1] = ("GET /api/health", False, "503 not configured yet: " + health.text[:200])
        home = check("GET / (web app)", "GET", "/", 200, lambda r: "The Vault" in r.text)
        for page in ("/credits.html", "/privacy.html", "/llms.txt"):
            check(f"GET {page}", "GET", page, 200)
        if configured:
            check("GET /api/v1 (hypermedia root)", "GET", "/api/v1", 200, lambda r: r.json().get("version") == "1")
            prov = check("GET /api/auth/providers", "GET", "/api/auth/providers", 200)
            if prov is not None and prov.status_code == 200:
                results[-1] = (results[-1][0], results[-1][1], results[-1][2] + " providers=" + json.dumps(prov.json().get("providers")))
            check("GET /api/v1/collection needs sign-in", "GET", "/api/v1/collection", 401,
                  lambda r: r.headers.get("content-type", "").startswith("application/problem+json"))
            check("POST /api/mcp needs a token", "POST", "/api/mcp", 401,
                  json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            check("GET /api/openapi.json", "GET", "/api/openapi.json", 200, lambda r: "/api/v1/collection/cards" in r.text)
            check("Cross-site POST refused", "POST", "/api/v1/decks/parse", 403,
                  json={"text": "1 Sol Ring"}, headers={"Origin": "https://evil.example"})
        if home is not None:
            hsts = "strict-transport-security" in home.headers
            results.append(("Security headers on /", hsts and home.headers.get("x-content-type-options") == "nosniff",
                            "HSTS " + ("yes" if hsts else "missing")))
        region = health.headers.get("x-vercel-id", "") if health is not None else ""
        results.append(("Function region (x-vercel-id)", "fra1" in region.split("::")[-2:-1] or "fra1" in region, region or "no header"))
    return results, configured


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    results, configured = run(argv[0])
    lines = [f"### Smoke test: {argv[0]}", "", "| check | ok | detail |", "|---|---|---|"]
    lines += [f"| {name} | {'✅' if ok else '❌'} | {detail} |" for name, ok, detail in results]
    text = "\n".join(lines) + "\n"
    sys.stdout.buffer.write(text.encode("utf-8"))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    broken = [n for n, ok, _ in results if not ok and not (not configured and n == "GET /api/health")]
    if os.environ.get("SMOKE_EXPECT_REGION") is None:  # previews may run elsewhere; only production must be fra1
        broken = [n for n in broken if n != "Function region (x-vercel-id)"]
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
