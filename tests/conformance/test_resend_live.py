"""Does the Resend twin (twins/resend.py) still behave like Resend's API? By hand only, and not run yet.

The twin was written from Resend's documentation (read 2026-10-09) because the owner has not created a Resend account: no key
exists to compare with. When the owner has one, run once and fix the twin where this fails:

    RESEND_CONFORMANCE_KEY=re_... TWINS_LIVE_RESEND=1 pytest tests/conformance/test_resend_live.py

It sends ONE message from Resend's sandbox sender to Resend's own test address (``delivered@resend.dev``, which Resend accepts without
delivering to a person), and never to anyone else. It is not part of the nightly workflow (``tests/test_workflows.py`` checks that
the workflow does not set ``TWINS_LIVE_RESEND``). Until it has run, ``docs/twins.md`` says the twin is the documentation's reading.
"""

from __future__ import annotations

import os

import httpx
import pytest

from twins import Universe

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("TWINS_LIVE_RESEND") != "1" or not os.environ.get("RESEND_CONFORMANCE_KEY"),
                       reason="by hand only: set TWINS_LIVE_RESEND=1 and RESEND_CONFORMANCE_KEY (one message to delivered@resend.dev)"),
]

URL = "https://api.resend.com/emails"
GOOD = {"from": "The Vault <onboarding@resend.dev>", "to": ["delivered@resend.dev"], "subject": "Conformance check", "text": "A test."}


def shape(value):
    return {k: type(v).__name__ for k, v in value.items()} if isinstance(value, dict) else type(value).__name__


@pytest.fixture(scope="module")
def twin():
    universe = Universe()
    universe.resend.add_key("re_twin", domains=())
    with httpx.Client(transport=universe.transport) as client:
        yield client


@pytest.fixture(scope="module")
def real():
    with httpx.Client(timeout=30) as client:
        yield client


def test_a_message_is_accepted_with_the_same_answer_shape(real, twin):
    key = os.environ["RESEND_CONFORMANCE_KEY"]
    got = real.post(URL, json=GOOD, headers={"Authorization": f"Bearer {key}"})
    ours = twin.post(URL, json=GOOD, headers={"Authorization": "Bearer re_twin"})
    assert got.status_code == ours.status_code == 200
    assert shape(got.json()) == shape(ours.json()) == {"id": "str"}


def test_the_same_idempotency_key_answers_the_same_thing(real, twin):
    key = os.environ["RESEND_CONFORMANCE_KEY"]
    headers = {"Authorization": f"Bearer {key}", "Idempotency-Key": "vault-conformance-1"}
    first = real.post(URL, json=GOOD, headers=headers)
    again = real.post(URL, json=GOOD, headers=headers)
    assert first.status_code == again.status_code and first.json() == again.json()
    changed = real.post(URL, json={**GOOD, "subject": "Another"}, headers=headers)
    ours = twin.post(URL, json=GOOD, headers={"Authorization": "Bearer re_twin", "Idempotency-Key": "k"})
    ours_changed = twin.post(URL, json={**GOOD, "subject": "Another"}, headers={"Authorization": "Bearer re_twin", "Idempotency-Key": "k"})
    assert changed.status_code == ours_changed.status_code == 409, (changed.text, ours.text)


@pytest.mark.parametrize("headers,name", [({}, "missing_api_key"), ({"Authorization": "Bearer re_not_a_key"}, "invalid_api_key")])
def test_a_missing_or_wrong_key_is_refused_with_the_same_error(real, twin, headers, name):
    got = real.post(URL, json=GOOD, headers=headers)
    ours = twin.post(URL, json=GOOD, headers=headers)
    assert got.status_code == ours.status_code == 401
    assert got.json()["name"] == ours.json()["name"] == name
    assert shape(got.json()) == shape(ours.json())


def test_a_missing_field_is_refused_in_the_same_family_of_status(real, twin):
    key = os.environ["RESEND_CONFORMANCE_KEY"]
    bad = {k: v for k, v in GOOD.items() if k != "subject"}
    got = real.post(URL, json=bad, headers={"Authorization": f"Bearer {key}"})
    ours = twin.post(URL, json=bad, headers={"Authorization": "Bearer re_twin"})
    assert got.status_code in (400, 422) and ours.status_code in (400, 422)  # the documentation says 400 or 422
    assert set(ours.json()) <= set(got.json())
