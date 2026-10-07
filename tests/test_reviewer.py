"""The stores' reviewer account (#239): absent unless REVIEWER_PASSPHRASE is set, one fixed demo account, only its own data."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from twins import Universe
from twins.mcp_client import ChatGptClient, McpClient
from vault import reviewer
from vault.api import mcp
from vault.app import create_app
from vault.config import Settings
from vault.models import Deck, Entry, User

V1 = "/api/v1"
SECRET = "correct horse battery staple"


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, session_secret="test", dev_login=True, base_url="http://testserver",
                    reviewer_passphrase=SECRET, auth_verify_rate_limit=10, oauth_rate_limit=1000, oauth_register_rate_limit=1000,
                    oauth_fetch_limit=100_000, oauth_fetch_ip_limit=100_000)


@pytest.fixture
def universe(settings):
    u = Universe()
    u.register_vault(settings)
    yield u
    assert not u.escapes


@pytest.fixture
def app(settings, universe):
    app = create_app(settings, serve_static=False, transport=universe.transport, resolver=universe.resolve)
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def browser(app):
    with TestClient(app) as c:
        yield c


def count(db, model, **where):
    return db.scalar(select(func.count()).select_from(model).filter_by(**where))


def test_the_way_in_does_not_exist_unless_the_passphrase_is_set(database_url, universe):
    off = Settings(database_url=database_url, session_secret="test", base_url="http://testserver")
    with TestClient(create_app(off, serve_static=False, transport=universe.transport, resolver=universe.resolve)) as c:
        assert c.post("/api/auth/reviewer-login", json={"passphrase": SECRET}).status_code == 404
        assert c.get("/reviewers").status_code == 404


def test_a_wrong_passphrase_is_refused_and_attempts_are_rate_limited(browser):
    bad = browser.post("/api/auth/reviewer-login", json={"passphrase": "nope"})
    assert bad.status_code == 401 and SECRET not in bad.text
    assert browser.get(f"{V1}/me").status_code == 401  # no session came of it
    codes = {browser.post("/api/auth/reviewer-login", json={"passphrase": f"try {i}"}).status_code for i in range(12)}
    assert 429 in codes
    assert browser.post("/api/auth/reviewer-login", json={"passphrase": SECRET}).status_code == 429  # even the right one, once limited


def test_the_right_passphrase_signs_in_the_one_demo_account_with_its_synthetic_data(browser, app):
    ok = browser.post("/api/auth/reviewer-login", json={"passphrase": SECRET})
    assert ok.status_code == 200 and ok.json()["account"] == "reviewer@demo.invalid"
    assert browser.get(f"{V1}/me").json()["email"] == reviewer.EMAIL
    decks = browser.get(f"{V1}/decks").json()["items"]
    assert {d["name"] for d in decks} == {"Sliver Swarm (demo)", "Pauper Burn (demo)"}
    with app.state.db.sessions() as db:
        assert sum(e.quantity for e in db.scalars(select(Entry))) == sum(row[1] for row in reviewer.OWNED) == 110
    commander, _, rest = reviewer.SLIVER_SWARM.partition("\n\nDeck\n")
    assert commander == "Commander\n1 Sliver Overlord"  # a five-colour Commander deck led by Sliver Overlord
    assert 1 + sum(int(m.group(1)) for m in re.finditer(r"^(\d+) ", rest, re.M)) == 100
    assert sum(int(m.group(1)) for m in re.finditer(r"^(\d+) ", reviewer.PAUPER_BURN, re.M)) == 60


def test_seeding_is_idempotent_and_reset_puts_the_demo_back_as_it_was(app):
    with app.state.db.sessions() as db:
        user = reviewer.seed(db)
        again = reviewer.seed(db)
        assert user.id == again.id
        assert count(db, Deck, user_id=user.id) == 2 and count(db, Entry, user_id=user.id) == len(reviewer.OWNED)
        db.query(Deck).filter_by(user_id=user.id, name="Pauper Burn (demo)").delete()
        db.commit()
        reviewer.seed(db)  # the missing deck comes back
        assert count(db, Deck, user_id=user.id) == 2
        reviewer.seed(db, reset=True)
        assert count(db, Deck, user_id=user.id) == 2 and count(db, Entry, user_id=user.id) == len(reviewer.OWNED)
        assert count(db, User, email=reviewer.EMAIL) == 1


def test_the_demo_session_cannot_see_or_touch_anyone_elses_data(app):
    mine = TestClient(app)
    mine.post("/api/auth/dev-login?email=someone@example.com")
    saved = mine.post(f"{V1}/decks", json={"name": "Private deck", "text": "1 Sol Ring"}).json()
    demo = TestClient(app)
    assert demo.post("/api/auth/reviewer-login", json={"passphrase": SECRET}).status_code == 200
    assert demo.get(f"{V1}/decks/{saved['id']}").status_code == 404
    assert demo.delete(f"{V1}/decks/{saved['id']}").status_code == 404
    assert "Private deck" not in demo.get(f"{V1}/decks").text
    assert mine.get(f"{V1}/decks/{saved['id']}").status_code == 200  # still there for its owner


def connect_and_list_decks(client: McpClient) -> str:
    assert client.sign_in_as_reviewer(SECRET).status_code == 200
    res = client.redeem(client.approve(write=True)["code"])
    assert res.status_code == 200, res.text
    client.tokens = res.json()
    return str(client.tool("list_decks"))


def test_claude_and_chatgpt_connect_through_oauth_with_no_second_factor_and_see_only_the_demo_decks(app, universe):
    other = TestClient(app)
    other.post("/api/auth/dev-login?email=someone@example.com")
    other.post(f"{V1}/decks", json={"name": "Private deck", "text": "1 Sol Ring"})
    claude = McpClient(lambda: TestClient(app), universe.client_hosts.publish())
    seen = connect_and_list_decks(claude)
    assert "Sliver Swarm (demo)" in seen and "Pauper Burn (demo)" in seen and "Private deck" not in seen
    chatgpt = ChatGptClient(lambda: TestClient(app), universe.client_hosts)
    seen = connect_and_list_decks(chatgpt)
    assert "Sliver Swarm (demo)" in seen and "Private deck" not in seen


def test_the_sign_in_page_offers_the_passphrase_box_while_enabled_and_the_guide_lists_the_cases(app, universe):
    client = McpClient(lambda: TestClient(app), universe.client_hosts.publish())
    page = client.authorize()  # signed out: the sign-in page
    assert page.status_code == 200 and 'id="rp"' in page.text and "Reviewer sign-in" in page.text
    guide = client.browser.get("/reviewers")
    assert guide.status_code == 200 and "Sliver Swarm (demo)" in guide.text and SECRET not in guide.text
    for case in reviewer.POSITIVE + reviewer.NEGATIVE:
        assert case["name"].replace("'", "&#x27;") in guide.text or case["name"] in guide.text
    assert len(reviewer.POSITIVE) == 5 and len(reviewer.NEGATIVE) == 3
    names = {t for case in reviewer.POSITIVE + reviewer.NEGATIVE for t in case["tools"]}
    assert names <= set(mcp.BY_NAME), f"unknown tools in the test cases: {names - set(mcp.BY_NAME)}"


def test_the_sign_in_page_has_no_passphrase_box_when_it_is_off(database_url, universe):
    off = Settings(database_url=database_url, session_secret="test", base_url="http://testserver", oauth_rate_limit=1000,
                   oauth_register_rate_limit=1000, oauth_fetch_limit=100_000, oauth_fetch_ip_limit=100_000)
    app = create_app(off, serve_static=False, transport=universe.transport, resolver=universe.resolve)
    client = McpClient(lambda: TestClient(app), universe.client_hosts.publish())
    page = client.authorize()
    assert page.status_code == 200 and "Reviewer sign-in" not in page.text and 'id="rp"' not in page.text


def test_the_passphrase_check_is_exact_and_an_empty_one_never_matches():
    assert reviewer.passphrase_ok(SECRET, SECRET) and not reviewer.passphrase_ok(SECRET + " ", SECRET)
    assert not reviewer.passphrase_ok("", "") and not reviewer.passphrase_ok("x", "") and not reviewer.passphrase_ok(None, SECRET)


def test_a_short_passphrase_stops_the_server_from_starting(database_url):
    with pytest.raises(RuntimeError, match="at least 16"):
        Settings(database_url=database_url, session_secret="test", reviewer_passphrase="short").check()


def test_the_guide_states_the_real_number_of_copies_in_the_demo_collection(app):
    """The live page said 'about 150 copies' while the demo holds 110: the number is computed from the data, not typed."""
    guide = TestClient(app).get("/reviewers").text
    assert f"{sum(row[1] for row in reviewer.OWNED)} copies of well-known cards" in guide and "about 150" not in guide
