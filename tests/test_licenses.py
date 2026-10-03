"""The Vault is MIT. Keep its server dependencies compatible: no GPL or AGPL, and LGPL only where
reviewed (Psycopg; see THIRD_PARTY_NOTICES.md)."""

import re
import tomllib
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
LGPL_REVIEWED = {"psycopg", "psycopg-binary", "psycopg-c", "psycopg-pool"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_closure() -> dict[str, metadata.Distribution]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    todo = [Requirement(d) for d in deps]
    seen: dict[str, metadata.Distribution] = {}
    while todo:
        req = todo.pop()
        key = norm(req.name)
        if key in seen:
            continue
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            continue  # optional/platform extra not installed here
        seen[key] = dist
        extras = set(req.extras) or {""}
        for sub in dist.requires or []:
            r = Requirement(sub)
            if r.marker is None or any(r.marker.evaluate({"extra": e}) for e in extras):
                todo.append(r)
    return seen


def licence(dist: metadata.Distribution) -> str:
    m = dist.metadata
    parts = [m.get("License-Expression") or "", m.get("License") or ""]
    parts += [c for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
    return " ".join(parts)


def test_no_gpl_and_lgpl_only_where_reviewed():
    problems = []
    for name, dist in runtime_closure().items():
        text = licence(dist)
        copyleft = re.search(r"\b(A?GPL|GNU (Affero )?General Public|GNU Lesser|LGPL)", text)
        if not copyleft:
            continue
        is_lgpl = re.search(r"LGPL|Lesser", text)
        if not is_lgpl or name not in LGPL_REVIEWED:
            problems.append(f"{name}: {text.strip()[:80]}")
    assert problems == [], "copyleft dependency needs a licence review (THIRD_PARTY_NOTICES.md): " + "; ".join(problems)


def test_direct_dependencies_are_in_the_notices():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    names = {"python-multipart": "python-multipart", "psycopg": "psycopg", "webauthn": "py_webauthn",
             "itsdangerous": "itsdangerous"}
    missing = [d for d in (Requirement(x).name for x in deps) if names.get(d, d).lower() not in notices]
    assert missing == []
