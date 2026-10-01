"""The Vercel deployment's shape. Vercel's Python builder detects api/index.py as a FastAPI app
and statically requires a top-level ``app`` assignment. Requests that aren't files in public/
go to that app with their real path, so an internal rewrite (e.g. to /api/index) would break
every route."""

import ast
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


def test_app_is_assigned_at_module_top_level():
    tree = ast.parse((ROOT / "api" / "index.py").read_text(encoding="utf-8"))
    targets = [t.id for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
               for t in (node.targets if isinstance(node, ast.Assign) else [node.target]) if isinstance(t, ast.Name)]
    assert "app" in targets


def test_vercel_json_routes_requests_to_the_app_unchanged():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert "rewrites" not in config and "outputDirectory" not in config
    assert config["regions"] == ["fra1"] and "api/index.py" in config["functions"]


def test_misconfigured_deployment_explains_itself(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")  # on Vercel without DATABASE_URL: create_app refuses sqlite
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/x.db")
    monkeypatch.syspath_prepend(str(ROOT))
    sys.modules.pop("api.index", None)
    index = importlib.import_module("api.index")
    assert isinstance(index.app, FastAPI)
    res = TestClient(index.app).get("/api/v1/collection")
    assert res.status_code == 503 and "not configured yet" in res.json()["detail"]
    sys.modules.pop("api.index", None)


def test_postgres_engine_suits_neons_pooler(monkeypatch):
    """Neon's pooled DATABASE_URL is PgBouncer (transaction mode): no server-side prepared statements."""
    from vault import db

    seen = {}
    monkeypatch.setattr(db, "create_engine", lambda url, **kw: seen.update(url=url, **kw))
    db.make_engine("postgres://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
    assert seen["url"].startswith("postgresql+psycopg://") and "channel_binding=require" in seen["url"]
    assert seen["connect_args"] == {"prepare_threshold": None} and seen["pool_pre_ping"] is True


def test_entrypoint_is_named_explicitly_for_vercel():
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    module, attr = config["tool"]["vercel"]["entrypoint"].split(":")
    assert (ROOT / (module.replace(".", "/") + ".py")).is_file() and attr == "app"


def test_base_url_comes_from_vercel_when_not_set(monkeypatch):
    from vault.config import Settings

    for name in ("BASE_URL", "VERCEL_ENV", "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_BRANCH_URL", "VERCEL_URL"):
        monkeypatch.delenv(name, raising=False)
    assert Settings().base_url == "http://localhost:8000"
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "the-vault-wintermute2.vercel.app")
    monkeypatch.setenv("VERCEL_URL", "the-vault-abc123-wintermute2.vercel.app")
    assert Settings().base_url == "https://the-vault-wintermute2.vercel.app"
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_BRANCH_URL", "the-vault-git-feature-wintermute2.vercel.app")
    assert Settings().base_url == "https://the-vault-git-feature-wintermute2.vercel.app"
    monkeypatch.setenv("BASE_URL", "https://vault.example.com/")
    assert Settings().base_url == "https://vault.example.com"


def test_a_vercel_deployment_refuses_a_plain_http_base_url(monkeypatch):
    # Secure cookies and the session-secret check hang on https: on Vercel, http is a misconfiguration.
    from vault.config import Settings

    monkeypatch.setenv("VERCEL", "1")
    plain = Settings(database_url="postgresql://db.example/vault", base_url="http://vault.example.com",
                     session_secret="dev-insecure-secret")
    with pytest.raises(RuntimeError, match="BASE_URL must be https"):
        plain.check()
    Settings(database_url="postgresql://db.example/vault", base_url="https://vault.example.com",
             session_secret="s" * 32).check()  # the right setup still starts
