"""The conditions of the rule that lets a provider's answer replace a stale Vault sign-in (vault/auth.py ``callback``), each pinned on its
own (the independent read of PR 471 found that removing any one of them passed the whole suite).

The rule replaces the old sign-in only when ALL of these hold: the sign-in is stale, a live pending authorization request exists, the
login came from a link of the sign-in page carrying its one-time token, there is no app flow, and it is not a reviewer's demo session.
The tests in test_sign_out_provider.py pin the stale, pending and token conditions; these pin the other three.
"""

from urllib.parse import quote

import pytest

from tests.test_mcp_oauth import BASE, app, client, db_do, make_client, universe  # noqa: F401 - fixtures
from tests.test_sign_out_provider import connect_through_google, identities_of, link_on, me, provider_trip, two_accounts  # noqa: F401
from vault import recent_signin
from vault.config import Settings

PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, session_secret="test", dev_login=True, base_url=BASE,
                    google_client_id="google-web", google_client_secret="x", reviewer_passphrase=PASSPHRASE,
                    oauth_rate_limit=1000, oauth_register_rate_limit=1000,
                    oauth_fetch_limit=100_000, oauth_fetch_ip_limit=100_000)


def test_a_fresh_sign_in_with_a_pending_request_still_links_another_provider_account_as_before(app, client, universe, two_accounts,
                                                                                               monkeypatch):
    """The replacement is only for a STALE sign-in: a recent one that signs in at the provider with another account links it to
    the account it holds, exactly as it did before this change (#347 allows a link in the window)."""
    ann, bob = two_accounts
    connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    real = recent_signin.now_ts
    monkeypatch.setattr(recent_signin, "now_ts", lambda: real() + 700)  # the sign-in is stale: the page and its token are rendered
    page = client.authorize()
    assert "Sign in to connect" in page.text
    monkeypatch.setattr(recent_signin, "now_ts", real)  # ... and the person signed in again meanwhile: fresh, the request still pending
    _, back = provider_trip(client, universe, bob, page)
    assert back.headers["location"].startswith("/oauth/authorize?")
    assert me(client)["id"] == ann_id  # still Ann's account
    assert identities_of(app, ann_id) == ["google:g-ann", "google:g-bob"]  # Bob's sign-in method was linked to it, as before


def test_an_app_flow_is_not_replaced_and_goes_back_to_the_app_with_the_error(app, client, universe, two_accounts, go_stale):
    """The Vault app's own sign-in (``app_redirect_uri``) keeps the #347 answer even when the browser also holds a pending
    authorization request and the link carried its token."""
    ann, bob = two_accounts
    connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    go_stale()
    page = client.authorize()
    path = link_on(page) + "&app_redirect_uri=" + quote("vault://auth", safe="") + "&code_challenge=" + "a" * 43
    _, back = provider_trip(client, universe, bob, path=path)
    assert back.headers["location"] == "vault://auth?error=recent_sign_in_required"
    assert me(client)["id"] == ann_id and identities_of(app, ann_id) == ["google:g-ann"]


def test_a_reviewers_demo_session_switches_account_exactly_as_before(app, client, universe, two_accounts, go_stale):
    """A store reviewer's demo session never takes a sign-in method of its own (#345): signing in at a provider switches accounts.
    The replacement rule leaves it alone (its outcome here is the same either way, so this pins the behaviour, not the guard)."""
    _, bob = two_accounts
    page = client.authorize()  # signed out: the pending request and its token
    assert client.sign_in_as_reviewer(PASSPHRASE).status_code == 200
    demo_id = me(client)["id"]
    go_stale()
    _, back = provider_trip(client, universe, bob, page)
    assert back.headers["location"].startswith("/oauth/authorize?")
    bob_id = me(client)["id"]
    assert bob_id != demo_id
    assert identities_of(app, bob_id) == ["google:g-bob"] and not [i for i in identities_of(app, demo_id) if i.startswith("google:")]
