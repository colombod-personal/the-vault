"""Probe the live product the way a person or an assistant meets it, and print what it found (issue #242).

No credentials: everything here is public or must be refused to a stranger. Each check names the issues whose criteria it
supports. Run: ``python scripts/verify_production.py [https://mtgvault.cards]``; exit code 1 if any check fails.
The signed-in checks (tools, decks, collection) are run through a connected assistant and recorded in the issues.
"""

from __future__ import annotations

import json
import re
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://mtgvault.cards").rstrip("/")
UA = {"User-Agent": "the-vault-verify/1 (+https://github.com/colombod-personal/the-vault)"}
results: list[tuple[str, bool, str, str]] = []


def check(name: str, issues: str, fn) -> None:
    try:
        ok, detail = fn()
    except Exception as exc:  # a probe that crashes is a failed check, with its reason
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    results.append((name, ok, issues, detail))


def get(path, **kw):
    return httpx.get(BASE + path, headers=UA, timeout=30, follow_redirects=False, **kw)


def public_page(path, needles):
    def run():
        r = get(path)
        missing = [n for n in needles if n.lower() not in r.text.lower()]
        return r.status_code == 200 and not missing, f"{r.status_code}, {len(r.text)} bytes" + (f", missing {missing}" if missing else "")
    return run


check("home page loads", "#157", public_page("/", ["The Vault"]))
check("connect page: Claude and ChatGPT steps, no token needed", "#227 #39 #157",
      public_page("/connect.html", ['id="claude"', 'id="chatgpt"', "Add custom MCP server", "mtgvault.cards/api/mcp", "npx skills add"]))
check("connect page shows the live address, not an old one", "#157",
      lambda: (not re.search(r"https?://[a-z0-9-]+\.vercel\.app", get("/connect.html").text)
               and "https://mtgvault.cards/api/mcp" in get("/connect.html").text, "the shown address is https://mtgvault.cards/api/mcp, no *.vercel.app"))
check("privacy page", "#227 #238 #62", public_page("/privacy.html", ["privacy"]))
check("credits page names the sources", "#62", public_page("/credits.html", ["Scryfall"]))
check("llms.txt names the connector tools", "#31 #39", public_page("/llms.txt", ["council_brief", "refresh_deck", "update_owned_cards", "show_owned_printings", "search_rules"]))
check("terms page exists (directories require it)", "#238", public_page("/terms.html", ["Fan Content"]))
check("support page exists (directories require it)", "#238", public_page("/support.html", ["issues"]))


def mcp_needs_a_token():
    r = httpx.post(BASE + "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=UA, timeout=30)
    return r.status_code == 401 and "resource_metadata" in r.headers.get("www-authenticate", ""), \
        f"{r.status_code} {r.headers.get('www-authenticate', '')[:140]}"


check("MCP server refuses strangers and says where to sign in", "#42 #41", mcp_needs_a_token)


def as_metadata():
    d = get("/.well-known/oauth-authorization-server").json()
    ok = (d.get("code_challenge_methods_supported") == ["S256"] and d.get("client_id_metadata_document_supported") is True
          and "private_key_jwt" in d.get("token_endpoint_auth_methods_supported", []) and "none" in d["token_endpoint_auth_methods_supported"])
    return ok, f"S256 only, CIMD, auth methods {d.get('token_endpoint_auth_methods_supported')}, algs {d.get('token_endpoint_auth_signing_alg_values_supported')}"


check("OAuth server metadata: PKCE S256, client-id documents, public and private_key_jwt clients", "#43 #45 #210", as_metadata)


def resource_metadata():
    d = get("/.well-known/oauth-protected-resource").json()
    return d.get("resource", "").endswith("/api/mcp") and d.get("scopes_supported") == ["read", "write"], json.dumps(d)[:200]


check("protected resource metadata", "#42", resource_metadata)


def authorize_refuses_unknown_client():
    r = get("/oauth/authorize", params={"response_type": "code", "client_id": "https://evil.invalid/c.json", "redirect_uri": "https://evil.invalid/cb",
                                         "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s"})
    return r.status_code in (400, 401) and "evil.invalid" not in r.headers.get("location", ""), f"{r.status_code}, no redirect to the stranger"


check("authorize refuses an unknown client and does not redirect to it", "#43 #45 #41", authorize_refuses_unknown_client)


def token_refuses_garbage():
    r = httpx.post(BASE + "/oauth/token", data={"grant_type": "authorization_code", "client_id": "https://chatgpt.com/oauth/client.json",
                                                 "code": "nope", "redirect_uri": "https://chatgpt.com/connector_platform_oauth_redirect", "code_verifier": "x" * 43},
                   headers=UA, timeout=30)
    return r.status_code == 401 and r.json().get("error") == "invalid_client", f"{r.status_code} {r.text[:120]}"


check("token endpoint refuses ChatGPT's client without a signed assertion", "#44 #210", token_refuses_garbage)


def no_anonymous_catalog():
    codes = {p: get(p, params=q).status_code for p, q in (("/api/v1/catalog/cards", {"name": "Lightning Bolt"}), ("/api/v1/catalog/sets", {}),
                                                         ("/api/v1/catalog/rules/search", {"q": "deathtouch"}), ("/api/v1/catalog/rules", {}),
                                                         ("/api/v1/catalog/rules/100.1", {}), ("/api/v1/catalog/cards/736f0d31-9052-5d04-b5d5-727ebc7f5cc9/rulings", {}))}
    codes["POST /api/v1/cards/lookup"] = httpx.post(BASE + "/api/v1/cards/lookup", json={"identifiers": [{"name": "Sol Ring"}]}, headers=UA, timeout=30).status_code
    return all(c == 401 for c in codes.values()), str(codes)


check("no anonymous card, rules or catalog API (Scryfall's terms)", "#62 #192", no_anonymous_catalog)


def api_needs_sign_in():
    codes = {p: get(p).status_code for p in ("/api/v1/collection", "/api/v1/decks", "/api/v1/imports", "/api/v1/me/tokens", "/api/v1/shares")}
    return all(c == 401 for c in codes.values()), str(codes)


check("every private route refuses a stranger", "#3 #62", api_needs_sign_in)


def security_headers():
    h = get("/").headers
    need = ["strict-transport-security", "x-content-type-options", "content-security-policy"]
    missing = [n for n in need if n not in h]
    return not missing, f"missing {missing}" if missing else "HSTS, nosniff, CSP present"


check("security headers", "#3", security_headers)


def openapi_lists_the_routes():
    r = get("/api/openapi.json")
    paths = r.json().get("paths", {}) if r.status_code == 200 else {}
    need = ["/api/v1/decks/{deck_id}/refresh", "/api/v1/council", "/api/v1/collection/printings", "/api/v1/collection/changes/preview"]
    missing = [p for p in need if p not in paths]
    return not missing, f"{len(paths)} routes" + (f", missing {missing}" if missing else "")


check("the new routes are live (refresh, council, printings, owned-card edits)", "#217 #220 #205 #83", openapi_lists_the_routes)


def mcp_tools_listed_after_sign_in_only():
    r = httpx.post(BASE + "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=UA, timeout=30)
    return r.status_code == 401, f"{r.status_code}: the tool list needs a token"


check("tool list is not public", "#42 #44", mcp_tools_listed_after_sign_in_only)

width = max(len(n) for n, *_ in results)
failed = 0
print(f"Production probe of {BASE} at {time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
for name, ok, issues, detail in results:
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  [{issues}]  {detail}")
print(f"\n{len(results) - failed} passed, {failed} failed")
sys.exit(1 if failed else 0)
