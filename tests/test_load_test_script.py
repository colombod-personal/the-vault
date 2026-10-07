"""scripts/load_test.py decides pass or fail for the "no 5xx under parallel use" criterion (#169), so what it counts as a failure is
tested: a load test that calls a 500 fine is worse than none."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location("load_test", Path(__file__).parent.parent / "scripts" / "load_test.py")
load_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load_test)


def answer(status=200, headers=None, **body):
    return httpx.Response(status, headers=headers or {}, content=json.dumps(body).encode())


def tool_result(content, is_error=True):
    return answer(result={"isError": is_error, "structuredContent": content})


@pytest.mark.parametrize("res, outcome", [
    (tool_result({"card": {}}, is_error=False), "ok"),
    (tool_result({"status": 404, "detail": "No card with that name"}), "not_found"),
    (tool_result({"status": 429, "detail": "Try again in 5 seconds.", "retry_after_seconds": 5}), "429"),
    (tool_result({"status": 503, "detail": "busy", "retry_after_seconds": 5}), "503"),
    (tool_result({"status": 500, "detail": "boom"}), "5xx"),
    (tool_result({"status": 502, "detail": "bad gateway"}), "5xx"),
    (tool_result({"status": 400, "detail": "bad"}), "bad_answer"),
    (answer(500, error="x"), "5xx"),
    (answer(504), "5xx"),
    (answer(503, {"Retry-After": "5"}), "503"),
    (answer(429, {"Retry-After": "9"}), "429"),
    (answer(error={"code": -32603, "message": "Internal error"}), "rpc_error"),
    (httpx.Response(200, content=b"<html>"), "bad_answer"),
    (answer(401), "bad_answer"),
])
def test_what_an_answer_counts_as(res, outcome):
    assert load_test.classify(res, "get_card_oracle", load_test.Tally())[0] == outcome


def test_a_429_or_503_without_a_retry_hint_fails_the_run():
    tally = load_test.Tally()
    load_test.classify(answer(429), "t", tally)
    load_test.classify(tool_result({"status": 503, "detail": "busy"}), "t", tally)
    load_test.classify(tool_result({"status": 429, "detail": "slow down"}), "t", tally)
    assert tally.retry_after_missing == 3 and tally.bad() == 3


def test_a_busy_answer_is_a_failure_except_where_the_phase_expects_it():
    tally = load_test.Tally()
    tally.add("get_rule", "503", 0.1)
    tally.add("get_card_oracle", "503", 0.1)
    tally.add("get_card_oracle", "ok", 0.1)
    assert tally.bad() == 2
    tally.busy_ok = frozenset({"get_rule"})
    assert tally.bad() == 1


def test_no_answer_at_all_is_a_failure_and_rate_limits_are_not():
    tally = load_test.Tally()
    tally.add("t", "transport", 60.0, "ReadTimeout")
    tally.add("t", "429", 0.1)
    tally.add("t", "404", 0.1)
    assert tally.bad() == 1 and tally.total("429") == 1


def test_a_run_against_a_server_counts_what_it_answers():
    """run_load end to end against a stand-in server: parallel agents, every tool of the plan, a refused connection."""
    seen = []

    def server(request: httpx.Request) -> httpx.Response:
        call = json.loads(request.content)["params"]["name"]
        seen.append(call)
        if call == "get_rule":
            return httpx.Response(500, text="Internal Server Error")
        return tool_result({"ok": True}, is_error=False)

    real = httpx.Client
    httpx.Client = lambda **kw: real(transport=httpx.MockTransport(server), **kw)
    try:
        tally = load_test.run_load("http://vault.test", "vault_pat_x", clients=3, calls=6, burst=2, mix="catalog", card="Lightning Bolt", timeout=5)
    finally:
        httpx.Client = real
    assert tally.total() == 18 and set(seen) == {"get_card_oracle", "search_rules", "get_rule"}
    assert tally.total("5xx") == seen.count("get_rule") and tally.bad() == tally.total("5xx") > 0
