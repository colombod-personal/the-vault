"""Switching Vault account through the provider, from a browser whose Vault sign-in is old (bead vault-wxy.3.1, found by review).

The usual case: the browser's session cookie lives 30 days, so by the time an app connects again its sign-in is older than the
recent-sign-in window and the consent page ("Use a different account") is not shown: the person meets the sign-in page. The way
through is the provider, and that has to be a sign-in as whoever the provider says, not a link to the account the browser still
holds (#347 refuses that, and still does on every other path). These tests go through the Google twin, not the developer sign-in
(which switches by design and so never exercised this), and start the provider trip from the link the sign-in page rendered, the
way a person does: the link carries a one-time token that a page on another site cannot read.
"""

import html
import re

import pytest
from sqlalchemy import select

from tests.test_mcp_oauth import BASE, V1, app, client, db_do, make_client, query_of, settings, universe  # noqa: F401 - fixtures
from tests.test_sign_out import grants, result
from twins.mcp_client import McpClient
from vault.models import Identity


def link_on(page, *, choose: bool = False) -> str:
    """The Google link a sign-in page rendered: the "Continue with Google" button, or (choose) the "use a different account" one."""
    links = re.findall(r'href="(/api/auth/login/google\?[^"]+)"', html.unescape(page.text))
    wanted = [link for link in links if ("account=choose" in link) is choose]
    assert wanted, (choose, links)
    return wanted[0]


def provider_trip(c: McpClient, universe, account, page=None, *, choose: bool = False, path: str | None = None):
    """The browser's trip through the Google twin, from a link on the sign-in page ``page`` (or a given ``path``): the provider
    address it was sent to, and the Vault's answer to the callback."""
    start = c.browser.get(path or link_on(page, choose=choose), follow_redirects=False)
    assert start.status_code in (302, 303, 307), start.text
    location = start.headers["location"]
    callback = universe.google.approve(location, account)
    return location, c.browser.get(callback.url.replace(BASE, ""), follow_redirects=False)


def me(c: McpClient) -> dict:
    return c.browser.get(f"{V1}/me").json()


def identities_of(app, user_id: int) -> list[str]:
    return db_do(app, lambda db: sorted(f"{i.provider}:{i.subject}" for i in db.scalars(select(Identity).where(Identity.user_id == user_id))))


def connect_through_google(c: McpClient, universe, account) -> dict:
    """Sign-in page, the provider, the consent page and the token: what a first connection looks like."""
    page = c.authorize()
    assert "Sign in to connect" in page.text
    _, back = provider_trip(c, universe, account, page)
    assert back.headers["location"].startswith("/oauth/authorize?"), back.headers["location"]
    consent = c.browser.get(back.headers["location"], follow_redirects=False)
    assert "Allow" in consent.text, consent.text
    res = c.redeem(c.returned(c.answer(consent))["code"])
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def two_accounts(universe):
    return (universe.google.add_account("g-ann", "ann@gmail.com", "Ann"),
            universe.google.add_account("g-bob", "bob@gmail.com", "Bob"))


def test_after_sign_out_a_stale_browser_signs_in_as_the_other_account_through_the_provider(app, client, universe, two_accounts, go_stale):
    ann, bob = two_accounts
    first = connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    assert result(client, first["access_token"], "sign_out", confirm=True)["structuredContent"]["signed_out"] is True
    assert grants(app) == 0
    go_stale()  # the browser's Vault sign-in is now older than the window: the consent page is not shown
    stale = client.authorize()
    assert "Sign in to connect" in stale.text and "Allow" not in stale.text
    assert "use a different account" in stale.text and "account=choose" in stale.text  # the way to another account is on the page
    location, back = provider_trip(client, universe, bob, stale, choose=True)
    assert query_of(location)["prompt"] == "select_account"  # the provider is asked to show its chooser, not to re-pick Ann
    assert back.status_code == 303 and back.headers["location"].startswith("/oauth/authorize?"), back.headers["location"]
    page = client.browser.get(back.headers["location"], follow_redirects=False)  # the pending request was not lost
    assert "Signed in as <strong>Bob</strong>" in page.text and "Allow" in page.text
    assert me(client)["id"] != ann_id
    second = client.redeem(client.returned(client.answer(page))["code"]).json()
    assert result(client, second["access_token"], "whoami")["structuredContent"]["user"]["id"] != ann_id
    assert identities_of(app, ann_id) == ["google:g-ann"]  # nothing of Bob's was linked to Ann's account
    assert grants(app) == 1


def test_a_stale_browser_that_signs_in_as_the_same_account_continues_as_it(app, client, universe, two_accounts, go_stale):
    ann, _ = two_accounts
    first = connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    result(client, first["access_token"], "sign_out", confirm=True)
    go_stale()
    stale = client.authorize()
    assert "Sign in to connect" in stale.text
    location, back = provider_trip(client, universe, ann, stale)  # "Continue with Google": no chooser, the person means this account
    assert "prompt" not in query_of(location)
    page = client.browser.get(back.headers["location"], follow_redirects=False)
    assert "Signed in as <strong>Ann</strong>" in page.text and "Allow" in page.text  # the sign-in is recent again
    assert me(client)["id"] == ann_id and identities_of(app, ann_id) == ["google:g-ann"]


def test_a_link_to_the_authorization_page_does_not_end_the_browsers_sign_in(client, universe, two_accounts, go_stale):
    """Nothing on a GET clears the old sign-in (that would be a logout anyone could trigger with a link): only a provider's answer,
    in a flow this browser started from the sign-in page, replaces it."""
    ann, _ = two_accounts
    connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    go_stale()
    for _ in range(2):
        assert "Sign in to connect" in client.authorize().text
        assert me(client)["id"] == ann_id  # still the same session; reads still work


def test_a_login_link_from_another_site_cannot_replace_the_sign_in_even_if_the_provider_answers_silently(app, client, universe,
                                                                                                         two_accounts, go_stale):
    """A page on another site can prime the pending request (a GET of the authorization URL) and then send the browser to the login
    URL; the provider, with a session of its own, may answer without asking. Without the token the sign-in page rendered into its
    links, the answer must not replace the Vault sign-in: nobody is signed out or switched by a link (review of PR 471)."""
    ann, bob = two_accounts
    connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    go_stale()
    stale = client.authorize()  # the primed request (the attacking page cannot read this page)
    token = re.search(r"pending=([\w-]+)", html.unescape(stale.text)).group(1)
    for path in ("/api/auth/login/google?continue=oauth", "/api/auth/login/google?continue=oauth&account=choose",
                 "/api/auth/login/google?continue=oauth&pending=" + "x" * len(token)):
        client.authorize()  # (re-primed: each login consumes what the page rendered)
        _, back = provider_trip(client, universe, bob, path=path)
        assert back.headers["location"] == "/?link_error=recent_sign_in_required&provider=google", path
        assert me(client)["id"] == ann_id and identities_of(app, ann_id) == ["google:g-ann"]


def test_the_token_of_the_sign_in_page_works_once(app, client, universe, two_accounts, go_stale):
    ann, bob = two_accounts
    connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    go_stale()
    stale = client.authorize()
    path = link_on(stale)
    first = client.browser.get(path, follow_redirects=False)  # the person's click, which the provider then abandons
    assert first.status_code in (302, 303, 307)
    _, back = provider_trip(client, universe, bob, path=path)  # the same link again, replayed
    assert back.headers["location"] == "/?link_error=recent_sign_in_required&provider=google"
    assert me(client)["id"] == ann_id


def test_the_pending_request_cannot_be_used_to_link_a_provider_from_another_page(app, client, universe, two_accounts, go_stale):
    """#347 still stands: a stale session (a copied cookie) signing in at the provider outside the consent flow is refused."""
    ann, bob = two_accounts
    connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    go_stale()
    assert "Sign in to connect" in client.authorize().text  # a pending request now waits in the session
    _, back = provider_trip(client, universe, bob, path="/api/auth/login/google")  # a sign-in that does not come from the sign-in page
    assert back.headers["location"] == "/?link_error=recent_sign_in_required&provider=google"
    assert identities_of(app, ann_id) == ["google:g-ann"] and me(client)["id"] == ann_id


def test_a_copied_stale_cookie_in_the_consent_flow_signs_in_as_the_provider_account_and_links_nothing(app, client, make_client, universe,
                                                                                                     two_accounts, go_stale):
    ann, _ = two_accounts
    connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    thief = make_client()
    thief.browser.cookies.update(client.browser.cookies)  # the copy of Ann's (stale once the clock moves) cookie
    go_stale()
    eve = universe.google.add_account("g-eve", "eve@gmail.com", "Eve")
    page = thief.authorize()
    assert "Sign in to connect" in page.text
    _, back = provider_trip(thief, universe, eve, page)
    assert back.headers["location"].startswith("/oauth/authorize?")
    thief_id = me(thief)["id"]
    assert thief_id != ann_id  # the thief is Eve, in an account of her own
    assert identities_of(app, ann_id) == ["google:g-ann"] and identities_of(app, thief_id) == ["google:g-eve"]


def test_use_a_different_account_on_the_consent_page_asks_the_provider_to_choose(app, client, universe, two_accounts):
    """A fresh browser sign-in: the consent page offers the switch, and the sign-in page it leads to asks the provider for its
    chooser (a provider with one signed-in account would otherwise silently continue as the account the person is leaving)."""
    ann, bob = two_accounts
    connect_through_google(client, universe, ann)
    ann_id = me(client)["id"]
    page = client.authorize()
    assert "Use a different account" in page.text
    switched = client.answer(page, decision="switch")
    assert switched.status_code == 303
    sign_in = client.browser.get(switched.headers["location"], follow_redirects=False)
    assert "Sign in to connect" in sign_in.text and "account=choose" in sign_in.text
    location, back = provider_trip(client, universe, bob, sign_in, choose=True)
    assert query_of(location)["prompt"] == "select_account"
    page = client.browser.get(back.headers["location"], follow_redirects=False)
    assert "Signed in as <strong>Bob</strong>" in page.text
    assert me(client)["id"] != ann_id and identities_of(app, ann_id) == ["google:g-ann"]


def test_only_the_oauth_sign_in_asks_for_the_account_chooser(client):
    asked = {}
    for query in ("continue=oauth&account=choose", "account=choose", "continue=oauth", "continue=oauth&account=anything"):
        location = client.browser.get(f"/api/auth/login/google?{query}", follow_redirects=False).headers["location"]
        asked[query] = query_of(location).get("prompt")
    assert asked == {"continue=oauth&account=choose": "select_account", "account=choose": None, "continue=oauth": None,
                     "continue=oauth&account=anything": None}


def test_the_provider_twins_accept_only_the_prompt_values_the_providers_document(universe):
    """The twins' own rule, kept in step with what the providers document (the live comparison is in tests/conformance): Google
    lists none, consent and select_account; Microsoft adds login and create. Anything else is an error at the real provider."""
    for twin, ok, bad in ((universe.google, "select_account", "login_please"), (universe.microsoft, "select_account", "always")):
        assert twin._extra_checks({"prompt": ok}) is None
        assert twin._extra_checks({"prompt": bad}) is not None
    assert universe.google._extra_checks({"prompt": "login"}) is not None  # Google's list has no "login"
    assert universe.microsoft._extra_checks({"prompt": "login"}) is None
