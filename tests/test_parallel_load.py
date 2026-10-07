"""Parallel use of the production MCP server (#169): five agents calling tools at once got 500s and "server isn't
responding". What these tests keep true: a tool call never needs two database connections at the same time (so a small
pool queues the calls instead of starving them), a connection that cannot be made is retried, every failure is a clean
answer with a retry hint (never a bare 500), a refusal for rate limits says when to come back, a failing Wizards or
Archidekt cannot hold the server up, and the log names what failed (error class, route, duration, pool state) without
naming anyone. ``scripts/load_test.py`` is the same shape against a running server (docs/ai-integration-testing.md)."""

import dataclasses
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.test_agents import CSV, auth, make_token, rpc
from tests.test_catalog_api import BOLT, load, serve_rules
from vault.app import create_app
from vault.config import Settings

V1 = "/api/v1"


def tool(client, token, tool_name, **arguments):
    res = rpc(client, "tools/call", {"name": tool_name, "arguments": arguments}, token)
    assert res.status_code == 200, res.text[:300]
    return res.json()["result"]


@pytest.fixture
def small_pool(database_url, monkeypatch):
    """An app whose pool has ONE connection and no overflow: any code that needs a second connection while it holds the
    first (the old MCP request did, and the catalog's rate limit) waits for the pool timeout and answers 503."""
    monkeypatch.setenv("DB_POOL_SIZE", "1")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "3")
    app = create_app(Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver"),
                     serve_static=False)
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def small(small_pool):
    load(small_pool)
    with TestClient(small_pool) as person:
        assert person.post("/api/auth/dev-login").status_code == 200
        person.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")})
        token = make_token(person)
        with TestClient(small_pool) as bot:
            yield bot, token, small_pool


def test_a_catalog_tool_call_needs_only_one_connection_at_a_time(small):
    bot, token, _ = small
    started = time.monotonic()
    result = tool(bot, token, "get_card_oracle", name="Lightning Bolt")
    assert result["isError"] is False and result["structuredContent"]["card"]["name"] == "Lightning Bolt"
    assert time.monotonic() - started < 2.5, "the call waited for a second connection (the pool timeout is 3 s)"


def test_parallel_tool_calls_queue_for_a_small_pool_instead_of_failing(small):
    """Five agents with several calls in flight each, on a pool of one: every call is answered, none with a 503."""
    bot, token, _ = small
    calls = [("get_card_oracle", {"name": "Lightning Bolt"}), ("get_collection_summary", {}), ("search_rules", {"query": "sample rule"}),
             ("get_rule", {"number": "100.1"}), ("list_decks", {})]
    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(lambda c: tool(bot, token, c[0], **c[1]), calls * 6))
    assert [r["isError"] for r in results] == [False] * 30, [r["structuredContent"] for r in results if r["isError"]][:2]


def test_the_rest_api_needs_one_connection_too(small):
    bot, token, _ = small
    h = auth(token)
    assert bot.get(f"{V1}/catalog/cards", params={"name": "Lightning Bolt"}, headers=h).status_code == 200
    assert bot.post(f"{V1}/decks/stats", json={"text": "1 Lightning Bolt"}, headers=h).status_code in (200, 400)


def test_a_refused_connection_is_retried(app, signed_in, monkeypatch):
    """A Neon compute that is waking up, or a database at its connection limit, refuses a connection now and accepts it a
    moment later: the next attempt is the answer, not a 500."""
    monkeypatch.setenv("DB_CONNECT_RETRY_DELAY", "0.01")
    real, refused = psycopg.connect, []

    def flaky(*args, **kwargs):
        if len(refused) < 2:
            refused.append(1)
            raise psycopg.errors.TooManyConnections("FATAL: too many connections for role")
        return real(*args, **kwargs)

    app.state.db.engine.dispose()  # no pooled connection to reuse: the next request has to connect
    monkeypatch.setattr(psycopg, "connect", flaky)
    assert signed_in.get(f"{V1}/me").status_code == 200
    assert len(refused) == 2


def test_a_wrong_password_is_not_retried(app, signed_in, monkeypatch):
    monkeypatch.setenv("DB_CONNECT_RETRY_DELAY", "0.01")
    attempts = []

    def refused(*args, **kwargs):
        attempts.append(1)
        raise psycopg.errors.InvalidPassword("password authentication failed")

    app.state.db.engine.dispose()
    monkeypatch.setattr(psycopg, "connect", refused)
    assert signed_in.get(f"{V1}/me").status_code == 503
    assert attempts == [1]


@pytest.fixture
def down(app, signed_in, monkeypatch):
    """The database refuses every connection."""
    monkeypatch.setenv("DB_CONNECT_RETRY_DELAY", "0.01")
    app.state.db.engine.dispose()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(psycopg.errors.CannotConnectNow("the database system is starting up")))
    return signed_in


def test_a_database_that_cannot_be_reached_is_a_503_with_a_retry_hint(down):
    res = down.get(f"{V1}/collection")
    assert res.status_code == 503 and res.headers["retry-after"] == "5"
    assert res.headers["content-type"].startswith("application/problem+json") and "try again" in res.json()["detail"].lower()
    assert res.headers["x-request-id"]


def test_on_the_mcp_endpoint_the_same_failure_is_a_json_rpc_error_with_a_retry_hint(app, signed_in, monkeypatch):
    token = make_token(signed_in)
    monkeypatch.setenv("DB_CONNECT_RETRY_DELAY", "0.01")
    app.state.db.engine.dispose()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(psycopg.errors.TooManyConnections("too many")))
    with TestClient(app) as bot:
        res = rpc(bot, "tools/list", token=token)
    body = res.json()
    assert res.status_code == 503 and res.headers["retry-after"] == "5"
    assert body["jsonrpc"] == "2.0" and body["error"]["code"] == -32603
    assert "try again" in body["error"]["message"].lower() and body["error"]["data"]["retryAfterSeconds"] == 5
    assert body["error"]["data"]["requestId"] == res.headers["x-request-id"]


def test_a_cold_start_while_the_database_wakes_does_not_crash_the_instance(database_url, monkeypatch):
    """create_app migrates at import. A database that is still waking then used to raise out of the import, so the whole
    function instance failed (a 500 for the request that triggered the cold start); now the instance comes up and the
    first request that needs the database finishes the job."""
    from vault.db import Database

    from sqlalchemy.exc import OperationalError

    real, calls = Database.migrate, []

    def waking(self):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("connect", {}, psycopg.errors.CannotConnectNow("the database system is starting up"))
        return real(self)

    monkeypatch.setattr(Database, "migrate", waking)
    app = create_app(Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver"), serve_static=False)
    try:
        with TestClient(app) as client:
            assert client.post("/api/auth/dev-login").status_code == 200
            assert client.get(f"{V1}/me").status_code == 200
        assert len(calls) == 2
    finally:
        app.state.db.engine.dispose()


# -- unexpected failures ------------------------------------------------------------------------

def boom(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr("vault.api.catalog_api.q.find_card", explode)


@pytest.fixture
def loaded_agent(app, signed_in):
    load(app)
    signed_in.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")})
    return signed_in, make_token(signed_in)


def test_an_unexpected_exception_is_a_problem_answer_with_a_request_id_and_is_logged(loaded_agent, app, monkeypatch, caplog):
    signed_in, token = loaded_agent
    boom(monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as bot, caplog.at_level(logging.INFO, logger="vault.access"):
        res = bot.get(f"{V1}/catalog/cards", params={"name": "Lightning Bolt"}, headers=auth(token))
    rid = res.headers["x-request-id"]
    body = res.json()
    assert res.status_code == 500 and res.headers["content-type"].startswith("application/problem+json")
    assert body["request_id"] == rid and rid in body["detail"] and body["retry_after_seconds"] >= 1
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "vault.access"]
    failed = [e for e in lines if e["event"] == "unhandled_exception"]
    assert failed and failed[0]["request_id"] == rid and failed[0]["error_class"] == "RuntimeError"
    assert failed[0]["route"] == f"{V1}/catalog/cards" and failed[0]["duration_ms"] >= 0 and "checked_out" in failed[0]["pool"]
    text = json.dumps(lines)
    assert token["token"] not in text and "Lightning Bolt" not in text and "nobody planned" not in text, "no tokens, names or messages in the log"


def test_in_an_mcp_tool_call_it_is_a_tool_error_with_a_retry_hint_not_a_bare_500(loaded_agent, app, monkeypatch):
    signed_in, token = loaded_agent
    boom(monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as bot:
        res = rpc(bot, "tools/call", {"name": "get_card_oracle", "arguments": {"name": "Lightning Bolt"}}, token)
    result = res.json()["result"]
    content = result["structuredContent"]
    assert res.status_code == 200 and result["isError"] is True and content["status"] == 500
    assert content["retry_after_seconds"] >= 1 and content["request_id"] == res.headers["x-request-id"]
    assert "Try" in content["detail"] and content["request_id"] in content["detail"]


def test_an_exception_in_the_mcp_server_itself_is_a_json_rpc_internal_error(loaded_agent, app, monkeypatch):
    signed_in, token = loaded_agent

    def explode():
        raise RuntimeError("views broke")

    monkeypatch.setattr("vault.api.mcp.mcp_ui.resources", explode)
    with TestClient(app, raise_server_exceptions=False) as bot:
        res = rpc(bot, "resources/list", token=token, id=7)
    body = res.json()
    assert res.status_code == 200 and body["id"] == 7 and body["error"]["code"] == -32603
    assert body["error"]["data"]["retryAfterSeconds"] >= 1 and body["error"]["data"]["requestId"] == res.headers["x-request-id"]


def test_a_busy_pool_in_a_tool_call_says_when_to_retry(loaded_agent, app, monkeypatch):
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    signed_in, token = loaded_agent

    def busy_once_in_the_api(*args, **kwargs):
        raise PoolTimeout("QueuePool limit of size 6 overflow 14 reached, connection timed out")

    with TestClient(app) as bot:
        monkeypatch.setattr("vault.api.catalog_api.q.find_card", busy_once_in_the_api)
        result = rpc(bot, "tools/call", {"name": "get_card_oracle", "arguments": {"name": "Lightning Bolt"}}, token).json()["result"]
    assert result["isError"] and result["structuredContent"]["status"] == 503 and result["structuredContent"]["retry_after_seconds"] == 5


# -- rate limits ---------------------------------------------------------------------------------

def test_the_catalog_limit_answers_429_with_retry_after_and_says_how_long(database_url):
    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver", catalog_rate_limit=2)
    app = create_app(settings, serve_static=False)
    load(app)
    try:
        with TestClient(app) as person:
            person.post("/api/auth/dev-login")
            token = make_token(person)
            rest = [person.get(f"{V1}/catalog/cards", params={"name": "Lightning Bolt"}, headers=auth(token)) for _ in range(3)]
            assert [r.status_code for r in rest] == [200, 200, 429]
            wait = int(rest[2].headers["retry-after"])
            assert 1 <= wait <= 60 and rest[2].json()["detail"] == f"Too many catalog requests. Try again in {wait} seconds."
            assert rest[2].headers["content-type"].startswith("application/problem+json")
            via_mcp = tool(person, token, "get_card_oracle", name="Lightning Bolt")  # the same minute: still over the limit
        content = via_mcp["structuredContent"]
        assert via_mcp["isError"] and content["status"] == 429 and 1 <= content["retry_after_seconds"] <= 60
        assert "Try again in" in content["detail"] and "seconds" in content["detail"]
    finally:
        app.state.db.engine.dispose()


# -- Wizards ---------------------------------------------------------------------------------------

@pytest.fixture
def rules_down(loaded_agent, app):
    signed_in, token = loaded_agent
    universe = serve_rules(app)
    app.state.rules_live.reset(universe.transport)  # a cold instance: nothing cached
    return universe, token


def test_wizards_being_down_is_a_503_with_a_short_retry_hint_in_rest_and_in_mcp(rules_down, app):
    universe, token = rules_down
    universe.wizards.outage = True
    with TestClient(app) as bot:
        res = bot.get(f"{V1}/catalog/rules/100.1", headers=auth(token))
        result = tool(bot, token, "get_rule", number="100.1")
    assert res.status_code == 503 and 1 <= int(res.headers["retry-after"]) <= 60 and "Wizards" in res.json()["detail"]
    content = result["structuredContent"]
    assert result["isError"] and content["status"] == 503 and 1 <= content["retry_after_seconds"] <= 60


def test_a_rules_file_that_is_not_text_is_a_503_not_a_500(rules_down, app):
    universe, token = rules_down

    def wizards(request):
        if request.url.host == "magic.wizards.com":
            return httpx.Response(200, text='<a href="https://media.wizards.com/2027/downloads/MagicCompRules 20270303.txt">TXT</a>')
        return httpx.Response(200, content=bytes([0xFF, 0xFE, 0xFA, 0x20, 0x80, 0x81]))  # not UTF-8

    app.state.rules_live.reset(httpx.MockTransport(wizards))
    with TestClient(app, raise_server_exceptions=False) as bot:
        res = bot.get(f"{V1}/catalog/rules/100.1", headers=auth(token))
    assert res.status_code == 503 and "Retry-After" in res.headers


def test_a_failed_read_of_the_rules_is_remembered_so_parallel_calls_do_not_each_wait_for_wizards(rules_down, app):
    """Every rules call used to try Wizards again, one after another behind a lock held during the download: ten calls
    meant ten timeouts in a row and no thread left for anything else."""
    universe, token = rules_down
    universe.wizards.outage = True
    before = len(universe.wizards.calls)
    with TestClient(app) as bot, ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: bot.get(f"{V1}/catalog/rules/100.1", headers=auth(token)).status_code, range(16)))
    assert codes == [503] * 16 and len(universe.wizards.calls) - before <= 2


def test_a_stale_edition_is_served_while_one_call_refreshes_it():
    from vault.rules_live import LiveRules
    from twins.universe import Universe
    from tests.test_rules_live import TEXT

    universe = Universe(seed=False)
    universe.wizards.publish(TEXT)
    now = [0.0]
    live = LiveRules(transport=universe.transport, page_ttl=10, clock=lambda: now[0])
    version = live.edition().version
    now[0] = 11  # the cached edition is due for a check
    release, entered = threading.Event(), threading.Event()
    real = universe.wizards.handle

    def slow(req):
        entered.set()
        release.wait(5)
        return real(req)

    universe.wizards.handle = slow
    refresher = threading.Thread(target=live.edition)
    refresher.start()
    assert entered.wait(5)
    started = time.monotonic()
    assert live.edition().version == version  # the stale edition, at once
    assert time.monotonic() - started < 1
    release.set()
    refresher.join(5)


# -- Archidekt -------------------------------------------------------------------------------------

def test_the_archidekt_fetch_does_not_hold_a_database_connection(app, signed_in):
    from vault import archidekt_cache

    held = []

    def fetch(deck_id):
        held.append(db.in_transaction())
        return {"id": deck_id, "name": "d"}

    with app.state.db.sessions() as db:
        archidekt_cache.read(db, 4242, fetch)
    assert held == [False], "the connection was held during the call to Archidekt (which can take seconds)"


def test_archidekt_answering_429_is_a_quick_503_with_retry_after_not_a_minute_of_sleeping(database_url):
    """The Archidekt client waits 30 s after a 429 and tries three more times: more than the function's time limit, with a
    database connection and a thread held the whole while."""
    from twins.universe import Universe

    universe = Universe()
    deck_id = universe.archidekt.add_deck("A deck", "someone", [(1, "Sol Ring")])["id"]
    universe.archidekt.fail_next("*", 429, times=20)
    app = create_app(Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver"),
                     serve_static=False, transport=universe.transport)
    try:
        with TestClient(app) as person:
            person.post("/api/auth/dev-login")
            token = make_token(person)
            started = time.monotonic()
            res = person.get(f"{V1}/archidekt/decks/{deck_id}", headers=auth(token))
            took = time.monotonic() - started
        assert res.status_code == 503 and int(res.headers["retry-after"]) >= 1 and "Archidekt" in res.json()["detail"]
        assert took < 12, f"{took:.0f} s"
    finally:
        app.state.db.engine.dispose()


def test_parallel_reads_of_one_archidekt_deck_ask_archidekt_once(app, signed_in):
    from vault import archidekt_cache

    asked = []

    def fetch(deck_id):
        asked.append(deck_id)
        time.sleep(0.3)
        return {"id": deck_id, "name": "d"}

    def read(_):
        with app.state.db.sessions() as db:
            return archidekt_cache.read(db, 777, fetch)["vault_cache"]["from_cache"]

    with ThreadPoolExecutor(6) as pool:
        answers = list(pool.map(read, range(6)))
    assert asked == [777] and sorted(answers) == [False] + [True] * 5


# -- the log -----------------------------------------------------------------------------------------

def test_every_mcp_tool_call_leaves_one_structured_line_without_personal_data(loaded_agent, app, caplog):
    signed_in, token = loaded_agent
    with TestClient(app) as bot, caplog.at_level(logging.INFO, logger="vault.access"):
        tool(bot, token, "get_card_oracle", name="Lightning Bolt")
        tool(bot, token, "get_collection_summary")
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "vault.access"]
    calls = [e for e in lines if e["event"] == "mcp_tool"]
    assert [c["tool"] for c in calls] == ["get_card_oracle", "get_collection_summary"]
    for c in calls:
        assert c["status"] == 200 and c["duration_ms"] >= 0 and c["request_id"] and set(c["pool"]) >= {"size", "checked_out", "overflow"}
    text = json.dumps(lines)
    assert token["token"] not in text and "Lightning Bolt" not in text and "vault_pat_" not in text


def test_slow_and_failed_requests_are_logged_as_warnings_with_the_route_and_pool_state(loaded_agent, app, caplog, monkeypatch):
    signed_in, token = loaded_agent
    monkeypatch.setattr("vault.observability.SLOW_MS", 0)
    with TestClient(app) as bot, caplog.at_level(logging.INFO, logger="vault.access"):
        bot.get(f"{V1}/catalog/status", headers=auth(token))
    slow = [json.loads(r.getMessage()) for r in caplog.records if r.name == "vault.access" and r.levelno == logging.WARNING]
    assert slow and slow[0]["event"] == "slow_request" and slow[0]["route"] == f"{V1}/catalog/status" and "pool" in slow[0]


def test_the_pool_state_is_readable():
    from vault import observability
    from vault.db import make_engine

    engine = make_engine("postgresql://u:p@localhost:5432/none")
    state = observability.pool_state(engine)
    assert state == {"size": 6, "checked_out": 0, "overflow": -6, "max_overflow": 14}
    engine.dispose()
