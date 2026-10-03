"""Configure the Vercel project from CI, so the only manual steps are the ones that need a
person's login: the Vercel token, connecting Neon, and the sign-in providers' consoles.

    VERCEL_TOKEN=... python -m jobs.vercel_setup [--project the-vault] [--scope TEAM]

It sets what the app needs and nobody has to type:
- ``SESSION_SECRET``: random, created once, never overwritten. Production and Preview each get
  their own, so code on a preview can't sign production session cookies. (An older shared one is
  narrowed to Production and Preview gets a new one.)
- ``BASE_URL``: the production domain. A custom domain is preferred over ``*.vercel.app``,
  and it is only set if missing.

Then it reports what is still missing (the database, sign-in providers) and the redirect URIs to
register, as JSON on stdout and as a checklist in the GitHub job summary.

Store a sign-in provider's credentials in Production, typed at a hidden prompt so they never
land in a file, the shell history or a chat:

    VERCEL_TOKEN=... python -m jobs.vercel_setup --scope TEAM --provider google

Variables only reach new deployments: ``--redeploy`` rebuilds production when this run changed one.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
from pathlib import Path

import httpx

API = "https://api.vercel.com"
PROVIDERS = {
    "google": ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
    "microsoft": ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET"),
    "facebook": ("FACEBOOK_CLIENT_ID", "FACEBOOK_CLIENT_SECRET"),
    "apple": ("APPLE_CLIENT_ID", "APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY"),
}


class Vercel:
    def __init__(self, token: str, project: str, scope: str | None = None, transport: httpx.BaseTransport | None = None):
        self.project = project
        self.params = {"slug": scope} if scope else {}
        self.http = httpx.Client(base_url=API, transport=transport, timeout=30,
                                 headers={"Authorization": f"Bearer {token}"})

    def _get(self, path: str) -> dict:
        res = self.http.get(path, params=self.params)
        res.raise_for_status()
        return res.json()

    def envs(self) -> list[dict]:
        """The project's variables: id, key and target list (values are never read)."""
        out = []
        for env in self._get(f"/v10/projects/{self.project}/env").get("envs", []):
            target = env.get("target") or []
            out.append({"id": env.get("id"), "key": env["key"], "target": [target] if isinstance(target, str) else target})
        return out

    def env_keys(self) -> dict[str, set[str]]:
        """Variable name -> the targets (production, preview, development) it is set for."""
        keys: dict[str, set[str]] = {}
        for env in self.envs():
            keys.setdefault(env["key"], set()).update(env["target"])
        return keys

    def set_targets(self, env_id: str, targets: list[str]) -> None:
        res = self.http.patch(f"/v9/projects/{self.project}/env/{env_id}", params=self.params, json={"target": targets})
        res.raise_for_status()

    def add_env(self, key: str, value: str, targets: list[str], *, replace: bool = False) -> None:
        params = {**self.params, "upsert": "true"} if replace else self.params
        res = self.http.post(f"/v10/projects/{self.project}/env", params=params,
                             json={"key": key, "value": value, "type": "encrypted", "target": targets})
        res.raise_for_status()

    def production_domain(self) -> str | None:
        domains = [d for d in self._get(f"/v9/projects/{self.project}/domains").get("domains", [])
                   if not d.get("redirect") and d.get("verified", True) and not d.get("gitBranch")]
        custom = [d["name"] for d in domains if not d["name"].endswith(".vercel.app")]
        default = [d["name"] for d in domains if d["name"].endswith(".vercel.app")]
        return (sorted(custom, key=len) or sorted(default, key=len) or [None])[0]

    def redeploy_production(self) -> str | None:
        """Rebuild the live production deployment so it reads the variables just set.
        Returns its address, or None when nothing has been deployed to production yet."""
        project_id = self._get(f"/v9/projects/{self.project}")["id"]
        res = self.http.get("/v6/deployments", params={**self.params, "projectId": project_id,
                                                         "target": "production", "state": "READY", "limit": 1})
        res.raise_for_status()
        live = res.json().get("deployments", [])
        if not live:
            return None
        res = self.http.post("/v13/deployments", params={**self.params, "forceNew": "1"},
                             json={"name": self.project, "deploymentId": live[0]["uid"], "target": "production"})
        res.raise_for_status()
        return f"https://{res.json()['url']}"


def _session_secrets(v: Vercel) -> list[str]:
    """One SESSION_SECRET per target: preview code must not hold production's signing key."""
    changed = []
    envs = [e for e in v.envs() if e["key"] == "SESSION_SECRET"]
    for e in envs:
        if "production" in e["target"] and "preview" in e["target"]:
            v.set_targets(e["id"], [t for t in e["target"] if t != "preview"])
            e["target"] = [t for t in e["target"] if t != "preview"]
            changed.append("SESSION_SECRET (production only)")
    for target in ("production", "preview"):
        if not any(target in e["target"] for e in envs):
            v.add_env("SESSION_SECRET", secrets.token_hex(32), [target])
            changed.append(f"SESSION_SECRET ({target})")
    return changed


def configure(v: Vercel) -> dict:
    changed = _session_secrets(v)
    keys = v.env_keys()
    base_url = None
    domain = v.production_domain()
    if "production" not in keys.get("BASE_URL", set()) and domain:
        base_url = f"https://{domain}"
        v.add_env("BASE_URL", base_url, ["production"])
        changed.append("BASE_URL")
    elif domain:
        base_url = f"https://{domain}"
    # Only what Production can read counts: a variable set just for Preview doesn't configure the site.
    production = {k for k, targets in keys.items() if "production" in targets}
    providers = [p for p, names in PROVIDERS.items() if all(n in production for n in names)]
    return {
        "changed": changed,
        "base_url": base_url,
        "database": "DATABASE_URL" in production,
        "providers": providers,
        "redirect_uris": {p: f"{base_url}/api/auth/callback/{p}" for p in PROVIDERS} if base_url else {},
        "dev_login_set": "DEV_LOGIN" in keys,
    }


SECRET_NAMES = {"GOOGLE_CLIENT_SECRET", "MICROSOFT_CLIENT_SECRET", "FACEBOOK_CLIENT_SECRET"}


def ask(name: str) -> str:
    """One provider setting from the terminal. Secrets are hidden; Apple's key is read from its .p8 file."""
    if name == "APPLE_PRIVATE_KEY":
        return Path(input("Path to the Sign in with Apple key (.p8): ").strip()).expanduser().read_text()
    value = (getpass.getpass if name in SECRET_NAMES else input)(f"{name}: ").strip()
    if not value:
        sys.exit(f"{name} is empty: nothing was changed")
    return value


def set_provider(v: Vercel, provider: str, prompt=ask) -> list[str]:
    """Store one provider's settings in Production (replacing old values). Returns the names set."""
    names = PROVIDERS[provider]
    domain = v.production_domain()
    if domain:
        print(f"Register this redirect URI with {provider}: https://{domain}/api/auth/callback/{provider}", file=sys.stderr)
    values = {name: prompt(name) for name in names}  # ask for everything first: all or nothing
    for name, value in values.items():
        v.add_env(name, value, ["production"], replace=True)
    return list(names)


def checklist(state: dict) -> str:
    lines = ["### Vercel setup", ""]
    tick = lambda ok: "✅" if ok else "⬜"  # noqa: E731
    if state["changed"]:
        lines.append(f"Set automatically this run: {', '.join(state['changed'])}")
        lines.append("")
    lines.append(f"- {tick(state['base_url'])} Address: {state['base_url'] or 'appears after the first production deploy'}")
    lines.append(f"- {tick(state['database'])} Database: "
                 + ("connected" if state["database"] else "Vercel → the-vault → Storage → Create Database → Neon, "
                    "region Frankfurt, connect to Production and Preview, then re-run this workflow"))
    lines.append(f"- {tick(state['providers'])} Sign-in: "
                 + (", ".join(state["providers"]) if state["providers"]
                    else "passkeys only. For Google, run `python -m jobs.vercel_setup --provider google` "
                         "(README → Sign-in providers)"))
    if state["redirect_uris"]:
        lines += ["", "Redirect URIs to register with each provider:", ""]
        lines += [f"- {p}: `{uri}`" for p, uri in state["redirect_uris"].items()]
    if state.get("redeployed"):
        lines += ["", f"Redeployed production so the new settings apply: {state['redeployed']}"]
    if state["dev_login_set"]:
        lines += ["", "⚠️ DEV_LOGIN is set in Vercel: remove it (the app refuses to start with it on https)."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None, prompt=ask) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default="the-vault")
    parser.add_argument("--scope", default=os.environ.get("VERCEL_SCOPE") or None)
    parser.add_argument("--provider", choices=sorted(PROVIDERS),
                        help="store this sign-in provider's credentials in Production (asks for them)")
    parser.add_argument("--redeploy", action="store_true",
                        help="redeploy production when a variable changed (variables only apply to new deployments)")
    args = parser.parse_args(argv)
    token = os.environ.get("VERCEL_TOKEN")
    if not token:
        sys.exit("VERCEL_TOKEN is not set")
    vercel = Vercel(token, args.project, args.scope, transport)
    stored = set_provider(vercel, args.provider, prompt) if args.provider else []
    state = configure(vercel)
    state["changed"] += stored
    state["redeployed"] = vercel.redeploy_production() if args.redeploy and state["changed"] else None
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(checklist(state))
    print(json.dumps(state))
    return state


if __name__ == "__main__":
    main()
