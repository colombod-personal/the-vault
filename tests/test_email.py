"""The mail sender (vault/email.py) against the Resend twin (twins/resend.py): what it sends, what it retries, what it never does.

Nothing here reaches the network: the twin answers on api.resend.com inside the process, and ``universe.escapes`` stays empty.
"""

import httpx
import pytest

from twins import Universe
from vault.config import Settings
from vault.email import EmailError, EmailMessage, ResendSender, make_sender, mask_address, usable_address

KEY = "re_test_key"


@pytest.fixture
def universe():
    u = Universe()
    u.resend.add_key(KEY)
    yield u
    assert not u.escapes, f"calls left the twin universe: {u.escapes}"


def sender(universe, key=KEY, from_address="The Vault <login@mtgvault.cards>"):
    return ResendSender(key, from_address, universe.transport)


MESSAGE = EmailMessage(to="ann@example.com", subject="Confirm it's you", text="Your code: 123456", html="<p>123456</p>")


def test_a_message_is_posted_to_resend_with_the_bearer_key(universe):
    sender(universe).send(MESSAGE)
    [call] = universe.resend.calls
    assert (call.method, call.host, call.path) == ("POST", "api.resend.com", "/emails")
    assert call.headers["authorization"] == f"Bearer {KEY}" and call.headers["idempotency-key"]
    [mail] = universe.resend.sent
    assert mail["from"] == "The Vault <login@mtgvault.cards>" and mail["to"] == ["ann@example.com"]
    assert mail["subject"] == "Confirm it's you" and mail["text"] == "Your code: 123456" and mail["html"] == "<p>123456</p>"


def test_a_bad_key_is_refused_and_not_retried(universe):
    with pytest.raises(EmailError) as err:
        sender(universe, key="re_wrong").send(MESSAGE)
    assert "401" in err.value.reason and "invalid_api_key" in err.value.reason and err.value.retryable is False
    assert len(universe.resend.calls) == 1 and not universe.resend.sent
    assert "re_wrong" not in str(err.value)  # the key is never in an error


def test_an_unverified_sending_domain_is_refused_like_resend_does(universe):
    with pytest.raises(EmailError) as err:
        sender(universe, from_address="Vault <login@example.org>").send(MESSAGE)
    assert "403" in err.value.reason and len(universe.resend.calls) == 1


def test_a_server_failure_is_retried_once_with_the_same_idempotency_key(universe):
    universe.resend.fail_next("/emails", 503, times=1)
    sender(universe).send(MESSAGE)
    first, second = universe.resend.calls
    assert first.status == 503 and second.status == 200
    assert first.headers["idempotency-key"] == second.headers["idempotency-key"]
    assert len(universe.resend.sent) == 1


def test_two_failures_in_a_row_give_up_after_one_retry(universe):
    universe.resend.fail_next("/emails", 500, times=5)
    with pytest.raises(EmailError) as err:
        sender(universe).send(MESSAGE)
    assert err.value.retryable is True and len(universe.resend.calls) == 2 and not universe.resend.sent


def test_a_rate_limit_answer_is_not_retried(universe):
    universe.resend.fail_next("/emails", 429, body={"statusCode": 429, "name": "rate_limit_exceeded", "message": "slow down"})
    with pytest.raises(EmailError):
        sender(universe).send(MESSAGE)
    assert len(universe.resend.calls) == 1


def test_a_network_failure_is_retried_once_and_then_reported():
    attempts = []

    def refuse(request):
        attempts.append(request)
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(EmailError) as err:
        ResendSender(KEY, "The Vault <login@mtgvault.cards>", httpx.MockTransport(refuse)).send(MESSAGE)
    assert len(attempts) == 2 and err.value.retryable is True
    assert KEY not in str(err.value)


def test_the_call_times_out_after_five_seconds():
    assert ResendSender(KEY, "x@y.z")._timeout == 5.0


def test_the_twin_replays_an_idempotent_request_and_refuses_a_changed_one(universe):
    http = universe.client()
    body = {"from": "Vault <login@mtgvault.cards>", "to": ["a@example.com"], "subject": "s", "text": "t"}
    headers = {"Authorization": f"Bearer {KEY}", "Idempotency-Key": "k1"}
    first = http.post("https://api.resend.com/emails", json=body, headers=headers)
    again = http.post("https://api.resend.com/emails", json=body, headers=headers)
    assert first.status_code == again.status_code == 200 and first.json() == again.json() and len(universe.resend.sent) == 1
    other = http.post("https://api.resend.com/emails", json={**body, "subject": "different"}, headers=headers)
    assert other.status_code == 409 and other.json()["name"] == "invalid_idempotent_request"


def test_the_twin_refuses_what_resend_refuses(universe):
    http = universe.client()
    url = "https://api.resend.com/emails"
    good = {"from": "Vault <login@mtgvault.cards>", "to": "a@example.com", "subject": "s", "text": "t"}
    assert http.post(url, json=good).json()["name"] == "missing_api_key"
    assert http.post(url, json=good, headers={"Authorization": "Bearer nope"}).status_code == 401
    auth = {"Authorization": f"Bearer {KEY}"}
    assert http.post(url, json={k: v for k, v in good.items() if k != "subject"}, headers=auth).status_code == 422
    assert http.post(url, json={k: v for k, v in good.items() if k != "text"}, headers=auth).status_code == 422
    assert http.post(url, json={**good, "to": []}, headers=auth).status_code == 422
    assert http.get(url, headers=auth).status_code == 405
    universe.resend.daily_quota = 0
    assert http.post(url, json=good, headers=auth).json()["name"] == "daily_quota_exceeded"
    assert not universe.resend.sent


def test_without_a_key_there_is_no_sender():
    assert make_sender(Settings(database_url="postgresql://x")) is None
    sender_ = make_sender(Settings(database_url="postgresql://x", resend_api_key=KEY, email_from="Vault <a@b.cd>"))
    assert isinstance(sender_, ResendSender) and sender_.from_address == "Vault <a@b.cd>"


def test_the_default_sender_address_is_the_vaults():
    assert Settings(database_url="postgresql://x").email_from == "The Vault <login@mtgvault.cards>"
    assert Settings(database_url="postgresql://x").recent_signin_seconds == 600


@pytest.mark.parametrize("address,masked", [
    ("ann@example.com", "***@e***.com"), ("diego.colombo@d-mail.co.uk", "***@d***.uk"), ("x@localhost", "***@l***"),
])
def test_an_address_is_masked_to_its_first_domain_letter_and_its_ending(address, masked):
    assert mask_address(address) == masked


@pytest.mark.parametrize("address,ok", [
    ("ann@example.com", True), (None, False), ("", False), ("ann", False), ("a@b", False), ("a b@c.de", False),
    ("a@@c.de", False), ("a\r\nBcc: x@y.zz@c.de", False), ("<a@c.de>", False), ("a@" + "b" * 320 + ".de", False),
])
def test_only_something_that_can_be_a_mailbox_is_mailed(address, ok):
    assert usable_address(address) is ok
