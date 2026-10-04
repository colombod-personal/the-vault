"""Who is asking: Client ID Metadata Documents (with the SSRF abuse cases) and dynamic registration."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from twins import Universe
from twins.mcp_client import GOOD_HOST, PUBLIC, McpClient
from twins.base import json_response
from vault import oauth_clients as oc
from vault.app import create_app
from vault.config import Settings
from vault.models import OAuthClient

BASE = "http://testserver"


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, session_secret="test", dev_login=True, base_url=BASE,
                    oauth_rate_limit=1000, oauth_register_rate_limit=1000, oauth_client_cap=5, oauth_cimd_cap=5,
                    oauth_fetch_limit=1000)


@pytest.fixture
def universe(settings):
    u = Universe()
    yield u
    assert not u.escapes


@pytest.fixture
def app(settings, universe):
    app = create_app(settings, serve_static=False, transport=universe.transport, resolver=universe.resolve)
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def browser(app):
    c = TestClient(app)
    assert c.post("/api/auth/dev-login").status_code == 200
    return c


def clients_of(app):
    with app.state.db.sessions() as db:
        return list(db.scalars(select(OAuthClient)))


def start(browser, client_id, redirect="https://app.example/callback", **extra):
    c = McpClient(lambda: browser, client_id, redirect)
    c.browser = browser
    return c, c.authorize(**extra)


def contacted(universe, host):
    return [c for c in universe.client_hosts.calls if c.host == host]


# -- addresses and URLs ------------------------------------------------------------------------

@pytest.mark.parametrize("address,public", [
    ("93.184.216.34", True), ("8.8.8.8", True), ("2606:4700:4700::1111", True),
    ("10.0.0.1", False), ("172.16.5.5", False), ("192.168.1.1", False), ("127.0.0.1", False), ("0.0.0.0", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("224.0.0.1", False), ("240.0.0.1", False),
    ("192.0.2.1", False), ("::1", False), ("::", False), ("fe80::1", False), ("fc00::1", False), ("ff02::1", False),
    ("::ffff:127.0.0.1", False), ("::ffff:10.1.2.3", False), ("::ffff:8.8.8.8", True),
    ("64:ff9b::7f00:1", False), ("64:ff9b::808:808", True), ("2002:7f00:1::", False), ("2001:0:4136:e378::1", False),
    ("not an address", False), ("", False),
])
def test_only_public_addresses_are_public(address, public):
    assert oc.public_address(address) is public


@pytest.mark.parametrize("url", [
    "http://app.example/c.json", "ftp://app.example/c.json", "https://1.2.3.4/c.json", "https://[::1]/c.json",
    "https://app.example:8443/c.json", "https://app.example:22/c.json", "https://user@app.example/c.json",
    "https://user:pw@app.example/c.json", "https://app.example", "https://app.example/", "https://app.example/c.json#f",
    "https://app.example/a/../b", "https://app.example/./b", "https://localhost/c.json", "https://intranet/c.json",
    "https://box.local/c.json", "https://svc.internal/c.json", "https://app.example/ c.json", "https://app.example/c\\x.json",
    "https://" + "a" * 600 + ".example/c.json", "", "https://",
])
def test_client_id_urls_that_could_aim_the_fetch_are_refused(url):
    with pytest.raises(oc.ClientError):
        oc.check_client_id_url(url)


def test_a_good_client_id_url_is_accepted():
    assert oc.check_client_id_url("https://app.example/oauth/client.json") == "app.example"
    assert oc.check_client_id_url("https://app.example:443/oauth/client.json?v=2") == "app.example"


@pytest.mark.parametrize("uri,ok", [
    ("https://app.example/cb", True), ("http://127.0.0.1:8080/cb", True), ("http://localhost/cb", True),
    ("http://[::1]:9/cb", True), ("http://app.example/cb", False), ("myapp://cb", False), ("javascript:alert(1)", False),
    ("https://app.example/cb#x", False), ("https://u:p@app.example/cb", False), ("https:///cb", False),
    ("https://app.example/cb\n", False), ("https://app.example:99999/cb", False), ("data:text/html,x", False),
    ("file:///etc/passwd", False), ("", False), (None, False), (5, False),
])
def test_valid_redirect_uris(uri, ok):
    assert oc.valid_redirect_uri(uri) is ok


def test_clean_names_lose_control_and_bidi_characters():
    assert oc.clean_name("A‮B\x00C\n D") == "A B C D"
    assert oc.clean_name(None) == "" and len(oc.clean_name("x" * 500)) == 80


# -- fetching ----------------------------------------------------------------------------------

def test_a_client_is_fetched_once_and_cached(app, universe, browser):
    url = universe.client_hosts.publish(GOOD_HOST)
    for _ in range(3):
        c, page = start(browser, url)
        assert page.status_code == 200
    assert len(contacted(universe, GOOD_HOST)) == 1
    [row] = clients_of(app)
    assert row.kind == "cimd" and row.name == "Twin Agent" and row.redirect_uris == ["https://app.example/callback"]


def test_the_cache_expires_and_a_changed_document_is_read_again(app, universe, browser):
    url = universe.client_hosts.publish(GOOD_HOST)
    start(browser, url)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    with app.state.db.sessions() as db:
        db.execute(update(OAuthClient).values(fetched_at=long_ago))
        db.commit()
    universe.client_hosts.publish(GOOD_HOST, name="Renamed", redirect_uris=("https://app.example/new",))
    _, old = start(browser, url)  # the old redirect URI is gone with the new document
    assert old.status_code == 400
    c, page = start(browser, url, redirect="https://app.example/new")
    assert page.status_code == 200 and "Renamed" in page.text and len(contacted(universe, GOOD_HOST)) == 2


def test_a_stale_client_whose_document_vanished_is_refused(app, universe, browser):
    url = universe.client_hosts.publish(GOOD_HOST)
    start(browser, url)
    with app.state.db.sessions() as db:
        db.execute(update(OAuthClient).values(fetched_at=datetime.now(timezone.utc) - timedelta(hours=2)))
        db.commit()
    universe.client_hosts.documents.clear()
    assert start(browser, url)[1].status_code == 400  # fail closed


@pytest.mark.parametrize("host", ["internal.example", "metadata.example", "loopback.example", "mixed.example",
                                  "mapped.example", "shared.example"])
def test_names_that_resolve_to_private_addresses_are_never_contacted(app, universe, browser, host):
    url = universe.client_hosts.publish(host)
    _, res = start(browser, url)
    assert res.status_code == 400 and "location" not in res.headers
    assert "public address" in res.text
    assert contacted(universe, host) == [] and clients_of(app) == []


@pytest.mark.parametrize("client_id", [
    "https://1.2.3.4/c.json", "https://app.example:8443/c.json", "https://user@app.example/c.json",
    "https://app.example/", "http://app.example/oauth/client.json", "https://nowhere.invalid/c.json",
    "https://unknown.example/c.json",
])
def test_malformed_and_unresolvable_client_ids_make_no_request(app, universe, browser, client_id):
    universe.client_hosts.publish(GOOD_HOST)
    _, res = start(browser, client_id)
    assert res.status_code == 400 and universe.client_hosts.calls == []


def test_redirects_are_not_followed(app, universe, browser):
    url = universe.client_hosts.serve("redirector.example", "/c.json", lambda req: httpx.Response(
        302, headers={"location": "http://169.254.169.254/latest/meta-data/"}))
    universe.client_hosts.publish("metadata.example")
    _, res = start(browser, url)
    assert res.status_code == 400 and "no redirects" in res.text
    assert len(contacted(universe, "redirector.example")) == 1 and contacted(universe, "metadata.example") == []


def test_a_redirect_to_another_public_host_is_refused_too(app, universe, browser):
    universe.client_hosts.publish("other.example")
    url = universe.client_hosts.serve("redirector.example", "/c.json", lambda req: httpx.Response(
        301, headers={"location": "https://other.example/oauth/client.json"}))
    assert start(browser, url)[1].status_code == 400 and contacted(universe, "other.example") == []


def test_oversized_documents_are_refused(app, universe, browser):
    big = '{"client_id": "x", "pad": "' + "a" * 200_000 + '"}'
    url = universe.client_hosts.serve("big.example", "/c.json", lambda r: httpx.Response(
        200, content=big.encode(), headers={"content-type": "application/json"}))
    _, res = start(browser, url)
    assert res.status_code == 400 and "too large" in res.text and clients_of(app) == []


def test_a_document_that_claims_to_be_small_but_streams_more_is_cut_off(app, universe, browser):
    def chunks():
        for _ in range(100):
            yield b"a" * 1024

    url = universe.client_hosts.serve("big.example", "/c.json", lambda r: httpx.Response(
        200, content=chunks(), headers={"content-type": "application/json"}))
    assert "too large" in start(browser, url)[1].text


def test_slow_documents_are_given_up_on(app, universe, browser):
    app.state.client_fetcher.seconds = 0.1
    url = universe.client_hosts.publish("slow.example")
    universe.client_hosts.latency = 0.4
    _, res = start(browser, url)
    assert res.status_code == 400 and "too long" in res.text


@pytest.mark.parametrize("handler,why", [
    (lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}), "not JSON"),
    (lambda r: httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"}), "valid JSON"),
    (lambda r: httpx.Response(200, content=b"[1]", headers={"content-type": "application/json"}), "not about"),
    (lambda r: httpx.Response(404), "not served directly"),
    (lambda r: httpx.Response(500, content=b"{}", headers={"content-type": "application/json"}), "not served directly"),
])
def test_documents_that_are_not_documents_are_refused(app, universe, browser, handler, why):
    url = universe.client_hosts.serve(GOOD_HOST, "/c.json", handler)
    _, res = start(browser, url)
    assert res.status_code == 400 and why in res.text


def test_a_document_about_another_client_is_refused(app, universe, browser):
    universe.client_hosts.publish("other.example")
    url = universe.client_hosts.publish(GOOD_HOST, client_id="https://other.example/oauth/client.json")
    _, res = start(browser, url)
    assert res.status_code == 400 and "not about this client_id" in res.text and clients_of(app) == []


@pytest.mark.parametrize("fields,why", [
    ({"redirect_uris": []}, "redirect_uris"), ({"redirect_uris": None}, "redirect_uris"),
    ({"redirect_uris": ["http://app.example/cb"]}, "redirect_uris"), ({"redirect_uris": "https://app.example/cb"}, "redirect_uris"),
    ({"redirect_uris": ["myapp://cb"]}, "redirect_uris"), ({"redirect_uris": [f"https://app.example/{i}" for i in range(11)]}, "redirect_uris"),
    ({"name": None}, "client_name"), ({"name": "   "}, "client_name"),
    ({"token_endpoint_auth_method": "private_key_jwt"}, "public clients"),
    ({"token_endpoint_auth_method": "client_secret_basic"}, "public clients"),
])
def test_unacceptable_metadata_is_refused(app, universe, browser, fields, why):
    url = universe.client_hosts.publish(GOOD_HOST, **fields)
    _, res = start(browser, url)
    assert res.status_code == 400 and why in res.text and clients_of(app) == []


def test_errors_never_repeat_what_was_fetched(app, universe, browser):
    url = universe.client_hosts.serve(GOOD_HOST, "/c.json", lambda r: httpx.Response(
        200, content=b'{"secret": "TOP-SECRET-INTERNAL-DATA"', headers={"content-type": "application/json"}))
    assert "TOP-SECRET" not in start(browser, url)[1].text


def test_the_connection_goes_to_the_address_that_was_checked():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, {"client_id": "https://app.example/c.json", "client_name": "X",
                                   "redirect_uris": ["https://app.example/cb"]})

    fetcher = oc.ClientFetcher(httpx.MockTransport(handler), lambda host: [PUBLIC], pin=True)
    assert fetcher.fetch("https://app.example/c.json")["client_name"] == "X"
    [request] = seen
    assert request.url.host == PUBLIC and request.headers["host"] == "app.example"  # DNS can't answer again
    assert request.extensions["sni_hostname"] == "app.example"  # TLS is still checked against the name


def test_a_name_that_resolves_differently_is_judged_on_every_address():
    fetcher = oc.ClientFetcher(httpx.MockTransport(lambda r: pytest.fail("fetched")), lambda host: [PUBLIC, "10.0.0.1"])
    with pytest.raises(oc.ClientError):
        fetcher.fetch("https://app.example/c.json")


def test_proxies_from_the_environment_are_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://169.254.169.254:3128")
    seen = []
    fetcher = oc.ClientFetcher(httpx.MockTransport(lambda r: seen.append(r) or json_response(404, {})), lambda h: [PUBLIC])
    with pytest.raises(oc.ClientError):
        fetcher.fetch("https://app.example/c.json")
    assert seen  # went through the given transport, not the proxy


def test_the_cache_is_capped_by_dropping_the_oldest_unused(app, universe, browser):
    for i in range(5):
        start(browser, universe.client_hosts.publish(GOOD_HOST, f"/c{i}.json"))
    _, res = start(browser, universe.client_hosts.publish(GOOD_HOST, "/c-extra.json"))
    assert res.status_code == 200 and len(clients_of(app)) == 5
    assert "https://app.example/c0.json" not in [c.client_id for c in clients_of(app)]


def test_clients_that_expired_unused_make_room(app, universe, browser):
    for i in range(5):
        start(browser, universe.client_hosts.publish(GOOD_HOST, f"/c{i}.json"))
    with app.state.db.sessions() as db:
        db.execute(update(OAuthClient).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
    assert start(browser, universe.client_hosts.publish(GOOD_HOST, "/new.json"))[1].status_code == 200
    assert len(clients_of(app)) == 1


# -- dynamic client registration ---------------------------------------------------------------

def register(client, **body):
    return client.post("/oauth/register", json={"redirect_uris": ["https://app.example/callback"], **body})


def test_a_registered_client_can_connect_and_is_marked_unverified(app, universe, browser):
    res = register(browser, client_name="My Script")
    assert res.status_code == 201 and res.headers["cache-control"] == "no-store"
    body = res.json()
    assert body["client_id"].startswith("vault_client_") and body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body and body["redirect_uris"] == ["https://app.example/callback"]
    c, page = start(browser, body["client_id"])
    assert page.status_code == 200 and "My Script" in page.text and "Unverified" in page.text
    tokens = c.redeem(c.answer(page).headers["location"].split("code=")[1].split("&")[0]).json()
    assert tokens["access_token"].startswith("vault_oat_")
    [row] = clients_of(app)
    assert row.kind == "dcr" and row.expires_at - datetime.now(timezone.utc) > timedelta(days=30)  # used: kept longer
    [item] = browser.get("/api/v1/me/apps").json()["items"]
    assert item["name"] == "My Script" and item["verified_by_address"] is False and item["domain"] is None


def test_a_registered_loopback_client_may_vary_its_port(app, browser):
    body = register(browser, redirect_uris=["http://127.0.0.1:1234/cb"]).json()
    c, page = start(browser, body["client_id"], redirect="http://127.0.0.1:5555/cb")
    assert page.status_code == 200 and "this computer" in page.text


@pytest.mark.parametrize("body", [
    {"redirect_uris": []}, {"redirect_uris": ["http://evil.example/cb"]}, {"redirect_uris": ["myapp://cb"]},
    {"redirect_uris": ["javascript:alert(1)"]}, {"redirect_uris": "https://app.example/cb"}, {"redirect_uris": [5]},
    {"redirect_uris": [f"https://app.example/{i}" for i in range(11)]},
    {"token_endpoint_auth_method": "client_secret_basic"}, {"token_endpoint_auth_method": "private_key_jwt"},
    {"grant_types": ["password"]}, {"grant_types": ["client_credentials"]}, {"grant_types": []},
    {"response_types": ["token"]},
])
def test_registrations_are_constrained(app, browser, body):
    res = register(browser, **body)
    assert res.status_code == 400 and res.json()["error"] in ("invalid_redirect_uri", "invalid_client_metadata")
    assert clients_of(app) == []


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1]", b'"x"', b"null"])
def test_registration_bodies_must_be_json_objects(app, browser, raw):
    res = browser.post("/oauth/register", content=raw, headers={"content-type": "application/json"})
    assert res.status_code == 400 and res.json()["error"] == "invalid_client_metadata" and clients_of(app) == []


def test_oversized_registrations_are_refused(app, browser):
    res = register(browser, client_name="x" * 20_000)
    assert res.status_code == 413 and clients_of(app) == []


def test_registration_names_are_cleaned_and_capped(app, browser):
    res = register(browser, client_name="<b>‮Admin</b>" + "x" * 300)
    assert len(res.json()["client_name"]) <= 80 and "‮" not in res.json()["client_name"]


def test_the_number_of_registrations_is_capped(app, browser):
    ok = [register(browser).status_code for _ in range(5)]
    assert ok == [201] * 5
    res = register(browser)
    assert res.status_code == 503 and res.json()["error"] == "temporarily_unavailable"


def test_unused_registrations_expire_and_cannot_be_used(app, browser):
    client_id = register(browser).json()["client_id"]
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    with app.state.db.sessions() as db:
        db.execute(update(OAuthClient).values(expires_at=past))
        db.commit()
    assert start(browser, client_id)[1].status_code == 400
    assert register(browser).status_code == 201  # and the expired one made room
    assert len(clients_of(app)) == 1


def test_a_registered_id_is_not_a_metadata_url_and_urls_are_not_registered_ids(app, universe, browser):
    universe.client_hosts.publish(GOOD_HOST)
    client_id = register(browser).json()["client_id"]
    assert start(browser, client_id)[1].status_code == 200
    assert start(browser, client_id.replace("vault_client_", "vault_client_x"))[1].status_code == 400
    assert universe.client_hosts.calls == []  # a registered id never causes a fetch


def test_registration_can_be_called_from_a_browser_app_on_another_site(app, browser):
    res = browser.post("/oauth/register", json={"redirect_uris": ["https://app.example/cb"]},
                       headers={"Origin": "https://some-mcp-client.example"})
    assert res.status_code == 201 and res.headers["access-control-allow-origin"] == "*"
    token = browser.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "x"},
                         headers={"Origin": "https://some-mcp-client.example"})
    assert token.status_code == 400  # not the cross-site 403: the token endpoint takes no cookies


# -- review fixes: names and addresses, caps, budgets, races ------------------------------------

def test_a_named_app_is_shown_with_its_address_next_to_the_name(app, universe, browser):
    url = universe.client_hosts.publish(GOOD_HOST)
    _, page = start(browser, url)
    assert "Connect Twin Agent (app.example) to your Vault?" in page.text
    assert 'name="write"' not in page.text or 'checked' not in page.text.split('name="write"')[1].split(">")[0]


def test_a_look_alike_address_shows_its_technical_form(app, universe, browser):
    host = "xn--pple-43d.example"  # the Cyrillic a followed by "pple"
    url = universe.client_hosts.publish("аpple.example", name="Apple Sync")
    _, page = start(browser, url)
    assert page.status_code == 200
    assert "\u0430pple.example" in page.text and host in page.text and "non-English letters" in page.text
    assert "Connect Apple Sync (\u0430pple.example)" in page.text
    assert oc.display_host("xn--pple-43d.example") == ("\u0430pple.example", "xn--pple-43d.example")
    assert oc.display_host("app.example") == ("app.example", "app.example")
    assert oc.display_host("\u0430pple.example")[1] == "xn--pple-43d.example"


def test_a_self_registered_app_is_titled_unverified_and_write_stays_unticked(app, browser):
    client_id = register(browser, client_name="Totally Official Anthropic").json()["client_id"]
    c, page = start(browser, client_id, scope="read write")
    assert "Connect Unverified app: Totally Official Anthropic to your Vault?" in page.text
    assert "Unverified app." in page.text and "can't confirm who" in page.text
    assert 'name="write"' in page.text and "checked" not in page.text.split('name="write"')[1].split(">")[0]
    sign_in = TestClient(app).get("/oauth/authorize?" + "&".join(f"{k}={v}" for k, v in c.authorize_params().items()
                                                              if k != "redirect_uri") + "&redirect_uri=https%3A%2F%2Fapp.example%2Fcallback")
    assert "Sign in to connect Unverified app: Totally Official Anthropic" in sign_in.text


def test_the_metadata_fetch_does_not_hold_a_database_connection(app, universe, browser):
    held = []
    pool = app.state.db.engine.pool

    def handler(request):
        held.append(pool.checkedout())
        return json_response(200, {"client_id": "https://app.example/oauth/c.json", "client_name": "X",
                                   "redirect_uris": ["https://app.example/callback"]})

    url = universe.client_hosts.serve(GOOD_HOST, "/oauth/c.json", handler)
    assert start(browser, url)[1].status_code == 200
    assert held == [0]  # the request's session had given its connection back while waiting for the stranger's server


def test_two_first_fetches_of_one_url_at_once_do_not_fail(app):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2, timeout=10)
    url = "https://app.example/oauth/client.json"

    def handler(request):
        barrier.wait()  # both are fetching before either has saved
        return json_response(200, {"client_id": url, "client_name": "Racer", "redirect_uris": ["https://app.example/cb"]})

    fetcher = oc.ClientFetcher(httpx.MockTransport(handler), lambda host: [PUBLIC])

    def resolve():
        with app.state.db.sessions() as db:
            return oc.resolve_client(db, fetcher, url).name

    with ThreadPoolExecutor(2) as pool:
        assert [f.result() for f in [pool.submit(resolve), pool.submit(resolve)]] == ["Racer", "Racer"]
    assert len(clients_of(app)) == 1


def test_cached_documents_and_registrations_have_separate_caps(app, universe, browser):
    for _ in range(5):
        assert register(browser).status_code == 201  # the registration cap (5) is full
    for i in range(5):
        assert start(browser, universe.client_hosts.publish(GOOD_HOST, f"/c{i}.json"))[1].status_code == 200
    assert register(browser).status_code == 503  # still full
    # cached documents are at their own cap: the oldest unused are dropped to make room
    assert start(browser, universe.client_hosts.publish(GOOD_HOST, "/new.json"))[1].status_code == 200
    kinds = [c.kind for c in clients_of(app)]
    assert kinds.count("dcr") == 5 and kinds.count("cimd") == 5


def test_documents_in_use_are_not_evicted(app, universe, browser):
    first = universe.client_hosts.publish(GOOD_HOST, "/used.json")
    c, page = start(browser, first)
    assert c.redeem(query_code(c, page)).status_code == 200  # a grant now uses it
    for i in range(6):
        start(browser, universe.client_hosts.publish(GOOD_HOST, f"/c{i}.json"))
    assert first in [c.client_id for c in clients_of(app)]


def query_code(c, page):
    from urllib.parse import parse_qs, urlsplit

    res = c.answer(page)
    return parse_qs(urlsplit(res.headers["location"]).query)["code"][0]


def test_metadata_fetches_are_limited_for_everyone_together(database_url, universe):
    settings = Settings(database_url=database_url, session_secret="test", dev_login=True, base_url=BASE,
                        oauth_rate_limit=1000, oauth_fetch_limit=2)
    app = create_app(settings, serve_static=False, transport=universe.transport, resolver=universe.resolve)
    try:
        urls = [universe.client_hosts.publish(GOOD_HOST, f"/c{i}.json") for i in range(3)]
        statuses = []
        for url in urls:  # a fresh browser (and so no shared state) each time: only the global budget is common
            c = TestClient(app)
            statuses.append(c.get("/oauth/authorize", params=McpClient(lambda: c, url).authorize_params()).status_code)
        assert statuses[:2] == [200, 200] and statuses[2] == 503
        assert TestClient(app).get("/oauth/authorize", params=McpClient(lambda: c, urls[0]).authorize_params()).status_code == 200  # cached: free
    finally:
        app.state.db.engine.dispose()


def test_only_a_few_fetches_run_at_once():
    fetcher = oc.ClientFetcher(httpx.MockTransport(lambda r: pytest.fail("fetched")), lambda host: [PUBLIC])
    held = [fetcher._slots.acquire(blocking=False) for _ in range(oc.MAX_CONCURRENT_FETCHES)]
    assert all(held)
    with pytest.raises(oc.ClientError) as err:
        fetcher.fetch("https://app.example/c.json")
    assert err.value.code == "temporarily_unavailable"
    for _ in held:
        fetcher._slots.release()
