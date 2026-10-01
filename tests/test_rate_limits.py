"""Rate limits on the sign-in endpoints (vault.ratelimit): counted in the database, so they hold
across serverless instances, keyed by a hash of the client's IP."""

import dataclasses
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from twins.authenticator import SoftAuthenticator
from vault import ratelimit
from vault.app import create_app
from vault.db import Base, Database
from vault.models import PasskeyChallenge, RateHit

POSTGRES = os.environ.get("VAULT_TEST_POSTGRES_URL")
DATABASES = ["sqlite"] + (["postgres"] if POSTGRES else [])

ENDPOINTS = [  # (method, path): every way in, each with its own bucket
    ("POST", "/api/auth/passkey/signup/options"), ("POST", "/api/auth/passkey/signup/verify"),
    ("POST", "/api/auth/passkey/register/options"), ("POST", "/api/auth/passkey/register/verify"),
    ("POST", "/api/auth/passkey/login/options"), ("POST", "/api/auth/passkey/login/verify"),
    ("POST", "/api/v1/auth/native/apple"), ("POST", "/api/v1/auth/token"),
    ("GET", "/api/auth/login/google"), ("GET", "/api/auth/callback/google"), ("POST", "/api/auth/callback/apple"),
    ("POST", "/api/facebook/data-deletion"), ("GET", "/api/facebook/deletion-status?code=x"),
]


def limited_client(settings, **limits):
    limits = {"auth_rate_limit": 2, "auth_verify_rate_limit": 2, **limits}
    return TestClient(create_app(dataclasses.replace(settings, base_url="http://localhost", **limits),
                                 serve_static=False), base_url="http://localhost")


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_every_sign_in_endpoint_is_limited(settings, method, path):
    with limited_client(settings, auth_rate_limit=1, auth_verify_rate_limit=1) as c:
        assert c.request(method, path).status_code != 429
        res = c.request(method, path)
    assert res.status_code == 429, path
    assert res.headers["content-type"].startswith("application/problem+json")
    assert 1 <= int(res.headers["retry-after"]) <= 60


def test_limits_per_bucket_and_window(settings, monkeypatch):
    now = [6000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: now[0])
    with limited_client(settings) as c:
        statuses = [c.post("/api/auth/passkey/login/options").status_code for _ in range(3)]
        assert statuses == [200, 200, 429]
        assert c.post("/api/auth/passkey/signup/options", json={}).status_code == 200  # another bucket
        now[0] += 45
        res = c.post("/api/auth/passkey/login/options")
        assert res.status_code == 429 and res.headers["retry-after"] == "15"
        now[0] += 15  # the next minute
        assert c.post("/api/auth/passkey/login/options").status_code == 200


def test_old_windows_are_deleted(settings, monkeypatch):
    now = [6000.0]
    monkeypatch.setattr(ratelimit.time, "time", lambda: now[0])
    with limited_client(settings) as c:
        c.post("/api/auth/passkey/login/options")
        now[0] += 60 * 10
        c.post("/api/auth/passkey/login/options")
        with c.app.state.db.sessions() as s:
            assert [r.minute for r in s.scalars(select(RateHit))] == [110]


def test_forwarded_headers_count_only_on_vercel(settings):
    spoofed = [{"x-forwarded-for": f"198.51.100.{i}"} for i in range(3)]
    with limited_client(settings) as c:  # not on Vercel: anyone could send these headers
        assert [c.get("/api/auth/login/google", headers=h).status_code for h in spoofed][-1] == 429
    with limited_client(settings, on_vercel=True, database_url=settings.database_url + "-vercel") as c:
        assert [c.get("/api/auth/login/google", headers=h).status_code for h in spoofed][-1] != 429
        same = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
        assert [c.get("/api/auth/login/google", headers=same).status_code for _ in range(3)][-1] == 429
        assert c.get("/api/auth/login/google", headers={"x-real-ip": "203.0.113.8"}).status_code != 429
        with c.app.state.db.sessions() as s:  # no IP addresses are stored
            keys = s.scalars(select(RateHit.key)).all()
        assert len(keys) == 5 and all(len(k) == 64 and "203.0" not in k and "198.51" not in k for k in keys)


@pytest.mark.parametrize("backend", DATABASES)
def test_hits_are_counted_exactly_under_concurrency(backend, tmp_path):
    db = Database(POSTGRES if backend == "postgres" else f"sqlite:///{tmp_path}/rl.db")
    if backend == "postgres":
        Base.metadata.drop_all(db.engine)
        with db.engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    db.migrate()
    counts, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(5):
            with db.sessions() as s:
                counts.append(ratelimit.hit(s, "k" * 64, 7))
                s.commit()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(counts) == list(range(1, 41))
    db.engine.dispose()


def test_pending_passkey_challenges_are_capped(settings):
    with limited_client(settings, auth_rate_limit=100, passkey_challenge_cap=2) as c:
        assert [c.post("/api/auth/passkey/login/options").status_code for _ in range(3)] == [200, 200, 429]
        res = c.post("/api/auth/passkey/signup/options", json={})
        assert res.status_code == 429 and res.headers["retry-after"]
        with c.app.state.db.sessions() as s:  # expired challenges make room again
            s.execute(PasskeyChallenge.__table__.update().values(expires=time.time() - 1))
            s.commit()
        options = c.post("/api/auth/passkey/signup/options", json={}).json()
        res = c.post("/api/auth/passkey/signup/verify",
                     json={"credential": SoftAuthenticator().create(options, "http://localhost")})
        assert res.status_code == 200
        with c.app.state.db.sessions() as s:
            assert s.scalar(select(PasskeyChallenge.id)) is None  # the expired ones were deleted


def test_each_provider_has_its_own_counter_but_made_up_ones_share_one(settings):
    """Using up Google sign-in doesn't block Apple. A made-up provider in the URL can't open a
    fresh counter to get past the limit."""
    with limited_client(settings, auth_rate_limit=1) as c:
        assert c.get("/api/auth/login/google", follow_redirects=False).status_code != 429
        assert c.get("/api/auth/login/google", follow_redirects=False).status_code == 429
        assert c.get("/api/auth/login/apple", follow_redirects=False).status_code != 429
        assert c.get("/api/auth/login/made-up-1", follow_redirects=False).status_code != 429
        assert c.get("/api/auth/login/made-up-2", follow_redirects=False).status_code == 429
