"""Combos on demand from Commander Spellbook, through its twin: attributed to them and linked, one call
per question, nothing stored, and a failing upstream is a clear 502 that never breaks anything else."""

import pytest
from fastapi.testclient import TestClient

from tests.test_deck_api import CARDS
from twins import Universe
from vault import catalog_sync as cs
from vault import combos
from vault.app import create_app
from vault.config import Settings

V1 = "/api/v1/decks"
DECK = "Commander\n1 Test Commander\n\nDeck\n1 Thassa's Oracle\n1 Test Rock\n"


@pytest.fixture
def universe():
    u = Universe()
    yield u
    assert not u.escapes, u.escapes


@pytest.fixture
def client(database_url, universe):
    settings = Settings(database_url=database_url, session_secret="t", dev_login=True, base_url="http://testserver")
    app = create_app(settings, serve_static=False, transport=universe.transport)
    with TestClient(app) as c:
        assert c.post("/api/auth/dev-login").status_code == 200
        extra = [dict(CARDS[0], oracle_id=f"9000000{i}-0000-0000-0000-000000000000", name=n)
                 for i, n in enumerate(["Thassa's Oracle", "Demonic Consultation"])]
        with app.state.db.sessions() as db:
            cs.sync_oracle_cards(db, CARDS + extra)
            cs.record_source(db, "oracle_cards", version="v", rows=1)
            db.commit()
        yield c
    app.state.db.engine.dispose()


def test_combos_in_the_deck_and_one_card_short_are_attributed_to_commander_spellbook(client, universe):
    deck = "Deck\n1 Thassa's Oracle\n1 Demonic Consultation\n1 Test Rock\n"
    r = client.post(f"{V1}/combos", json={"text": deck}).json()
    combo = r["result"]["included"][0]
    assert combo["cards"] == ["Thassa's Oracle", "Demonic Consultation"] or combo["cards"] == ["Demonic Consultation", "Thassa's Oracle"]
    assert combo["missing"] == []
    assert combo["url"].startswith("https://commanderspellbook.com/combo/") and "Win the game" in combo["produces"]
    assert combo["description"].startswith("Cast Demonic Consultation")
    blocks = r["provenance"]
    assert blocks[0]["kind"] == "source" and blocks[0]["source"] == "Commander Spellbook" and blocks[0]["notice"]
    assert blocks[1]["kind"] == "computed" and blocks[1]["inputs"][0]["source"] == "Commander Spellbook"
    short = client.post(f"{V1}/combos", json={"text": "Deck\n1 Thassa's Oracle\n1 Test Rock\n"}).json()["result"]
    assert short["included"] == [] and short["almost_included"][0]["missing"] == ["Demonic Consultation"]
    assert any("community" in n for n in short["notes"])


def test_one_call_goes_out_per_question_and_the_deck_is_not_stored(client, universe):
    client.post(f"{V1}/combos", json={"text": DECK})
    calls = [c for c in universe.spellbook.calls if c.path == "/find-my-combos"]
    assert len(calls) == 1 and calls[0].headers["user-agent"].startswith("the-vault/")
    assert client.get("/api/v1/decks").json()["total"] == 0


def test_a_failing_upstream_is_a_502_and_the_rest_still_works(client, universe):
    universe.spellbook.fail_next("/find-my-combos", 500)
    res = client.post(f"{V1}/combos", json={"text": DECK})
    assert res.status_code == 502 and "Commander Spellbook" in res.json()["detail"]
    assert client.post(f"{V1}/stats", json={"text": DECK}).status_code == 200
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 200


def test_a_malformed_answer_is_a_502_not_a_crash(client, universe):
    universe.spellbook.fail_next("/find-my-combos", 200, body={"unexpected": True})
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 502


def test_the_answer_says_it_lists_only_combos_commander_spellbook_knows(client, universe):
    """A real council run (2026-10-05) told a pod a deck had 'no infinite combos' because this found none, while the deck held a
    token engine Spellbook does not list (#172)."""
    result = client.post(f"{V1}/combos", json={"text": "Deck\n1 Test Rock\n"}).json()["result"]
    assert result["included"] == [] and "Only combos known to Commander Spellbook" in result["limits"]
    assert "never tell a player it is combo-free" in result["limits"]
    from vault.api import mcp
    assert "finding none does not mean the deck has no infinite combos" in mcp.BY_NAME["find_combos"].description


# -- the rate limit and the circuit breaker (#20, docs/data-sources.md: "a small rate limit and a circuit breaker") --------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(combos, "GUARD", combos.UpstreamGuard(rate_per_minute=5, failures=3, cooldown=60, max_cooldown=300, clock=c))
    return c


def upstream_calls(universe):
    return len([c for c in universe.spellbook.calls if c.path == "/find-my-combos"])


def test_three_failures_in_a_row_open_the_breaker_and_then_no_call_goes_out(client, universe, clock):
    universe.spellbook.fail_next("/find-my-combos", 500, times=3)
    assert [client.post(f"{V1}/combos", json={"text": DECK}).status_code for _ in range(3)] == [502, 502, 502]
    assert upstream_calls(universe) == 3 and combos.GUARD.state == "open"
    refused = client.post(f"{V1}/combos", json={"text": DECK})
    assert refused.status_code == 503 and int(refused.headers["retry-after"]) >= 1
    assert "not calling it" in refused.json()["detail"] and "Commander Spellbook" in refused.json()["detail"]
    assert upstream_calls(universe) == 3  # the refused call made no request at all
    assert client.post(f"{V1}/stats", json={"text": DECK}).status_code == 200  # the rest of the Vault is untouched


def test_after_the_cooldown_one_probe_goes_out_and_a_success_closes_the_breaker(client, universe, clock):
    universe.spellbook.fail_next("/find-my-combos", 500, times=3)
    for _ in range(3):
        client.post(f"{V1}/combos", json={"text": DECK})
    clock.now += 61
    assert combos.GUARD.state == "half-open"
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 200
    assert upstream_calls(universe) == 4 and combos.GUARD.state == "closed"
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 200


def test_a_failed_probe_reopens_the_breaker_for_twice_as_long(client, universe, clock):
    universe.spellbook.fail_next("/find-my-combos", 500, times=4)
    for _ in range(3):
        client.post(f"{V1}/combos", json={"text": DECK})
    clock.now += 61
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 502  # the probe failed
    assert combos.GUARD.state == "open"
    clock.now += 61  # a minute is no longer enough: the cooldown doubled to two
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 503
    clock.now += 60
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 200


def test_only_one_probe_goes_out_at_a_time():
    clock = Clock()
    guard = combos.UpstreamGuard(rate_per_minute=10, failures=1, cooldown=30, clock=clock)
    guard.before_call()
    guard.failure()
    clock.now += 31
    guard.before_call()  # the probe
    with pytest.raises(combos.ComboServiceBusy):
        guard.before_call()  # a second one waits for the probe's verdict
    guard.success()
    guard.before_call()


def test_successes_in_between_keep_the_breaker_closed(client, universe, clock):
    for fail in (True, True, False, True, True):
        if fail:
            universe.spellbook.fail_next("/find-my-combos", 500)
        client.post(f"{V1}/combos", json={"text": DECK})
    assert combos.GUARD.state == "closed" and upstream_calls(universe) == 5


def test_the_rate_limit_refuses_the_call_before_it_is_made_and_resets_after_a_minute(client, universe, clock):
    assert [client.post(f"{V1}/combos", json={"text": DECK}).status_code for _ in range(5)] == [200] * 5
    limited = client.post(f"{V1}/combos", json={"text": DECK})
    assert limited.status_code == 503 and "5 times in the last minute" in limited.json()["detail"]
    assert int(limited.headers["retry-after"]) >= 1 and upstream_calls(universe) == 5
    assert combos.GUARD.state == "closed"  # being limited is not a failure
    clock.now += 61
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 200


def test_an_upstream_429_with_retry_after_opens_the_breaker_at_once(client, universe, clock):
    universe.spellbook.fail_next("/find-my-combos", 429, retry_after="120")
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 502
    refused = client.post(f"{V1}/combos", json={"text": DECK})
    assert refused.status_code == 503 and 100 <= int(refused.headers["retry-after"]) <= 120
    clock.now += 121
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 200


def test_a_refusal_of_our_own_request_is_not_the_services_failure(client, universe, clock):
    universe.spellbook.fail_next("/find-my-combos", 400, times=4)
    assert [client.post(f"{V1}/combos", json={"text": DECK}).status_code for _ in range(4)] == [502] * 4
    assert combos.GUARD.state == "closed"
    assert client.post(f"{V1}/combos", json={"text": DECK}).status_code == 200


def test_a_timeout_counts_as_a_failure(universe, clock):
    import httpx

    def hang(request):
        raise httpx.ReadTimeout("slow", request=request)

    for _ in range(3):
        with pytest.raises(combos.ComboServiceError):
            combos.ask([("Sol Ring", 1)], [], httpx.MockTransport(hang))
    assert combos.GUARD.state == "open"
