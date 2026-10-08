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
from vault.models import Deck, Entry, Import, User

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


def _reviewer(browser):
    assert browser.post("/api/auth/reviewer-login", json={"passphrase": SECRET}).status_code == 200


def test_a_reviewer_session_can_do_what_the_review_cases_need_but_no_account_level_action(browser):  # #345
    _reviewer(browser)
    assert browser.get(f"{V1}/me").status_code == 200 and browser.get(f"{V1}/collection").status_code == 200
    assert browser.get(f"{V1}/decks").status_code == 200
    assert browser.post(f"{V1}/decks/parse", json={"text": "1 Sol Ring"}).status_code == 200
    saved = browser.post(f"{V1}/decks", json={"name": "A reviewer's deck", "text": "1 Sol Ring"})
    assert saved.status_code == 201  # writes the cases need still work
    for method, path, body in [("POST", f"{V1}/me/tokens", {"name": "x", "scopes": ["read", "write"], "expires_in_days": 365}),
                               ("GET", f"{V1}/me/export", None), ("DELETE", f"{V1}/me", {"confirm": "DELETE"}),
                               ("POST", "/api/auth/passkey/register/options", {})]:
        res = browser.request(method, path, json=body)
        assert res.status_code in (403, 404), (path, res.status_code)  # 404: passkeys are off on this http test site
        assert res.status_code != 200, path
    assert browser.get(f"{V1}/me").status_code == 200  # nothing above deleted the demo account


def test_the_guide_and_the_docs_say_what_the_reviewer_account_cannot_do():  # #345
    from pathlib import Path

    guide = " ".join(__import__("vault.reviewer_routes", fromlist=["page"]).page("https://x.example").split())
    assert "cannot create access tokens, passkeys or linked sign-ins, export or delete itself" in guide
    doc = " ".join((Path(__file__).parent.parent / "docs" / "mcp-oauth-threat-model.md").read_text(encoding="utf-8").split())
    assert "no account-level powers" in doc and "unsetting or rotating the variable ends every reviewer session at once" in doc


def test_a_reviewer_session_never_takes_a_sign_in_method_of_its_own(app, browser):  # #345
    from types import SimpleNamespace

    from vault import auth as auth_module
    from vault.models import Identity

    _reviewer(browser)
    with app.state.db.sessions() as db:
        demo = db.scalar(select(User).join(Identity, Identity.user_id == User.id).where(Identity.provider == reviewer.PROVIDER))
        before = count(db, Identity, user_id=demo.id)
        request = SimpleNamespace(session={"uid": demo.id, "sk": demo.session_key, "rv": "any", "app_flow": None})
        other = auth_module.sign_in(db, request, auth_module.Profile("google", "someone", "someone@example.com", "Someone"))
        assert other.id != demo.id  # a provider sign-in on a reviewer session switches accounts, it does not link
        assert count(db, Identity, user_id=demo.id) == before


def test_unsetting_or_changing_the_passphrase_ends_the_reviewer_sessions_at_once(app, browser, settings, universe):  # #345
    from dataclasses import replace

    _reviewer(browser)
    assert browser.get(f"{V1}/me").status_code == 200
    for changed in (replace(settings, reviewer_passphrase=None), replace(settings, reviewer_passphrase="another long passphrase 2")):
        other = create_app(changed, serve_static=False, transport=universe.transport, resolver=universe.resolve)
        try:
            with TestClient(other) as later:
                later.cookies.update(browser.cookies)  # the same cookie, the app now configured differently
                assert later.get(f"{V1}/me").status_code == 401, changed.reviewer_passphrase
        finally:
            other.state.db.engine.dispose()
    assert browser.get(f"{V1}/me").status_code == 200  # still valid where the passphrase is unchanged


def test_the_cross_site_guard_exempts_the_exact_sign_in_callbacks_and_no_path_below_them():  # #348
    from vault.app import CROSS_SITE_ALLOWED

    for provider in ("google", "microsoft", "apple", "facebook"):
        assert f"/api/auth/callback/{provider}" in CROSS_SITE_ALLOWED
    assert not any(p.endswith("/") for p in CROSS_SITE_ALLOWED)  # no prefix: a path under one is not exempt


def test_a_cross_site_post_below_a_callback_is_refused_and_the_callback_itself_is_not(app, browser):  # #348
    foreign = {"origin": "https://evil.example"}
    for path in ("/api/auth/callback/google/../me", "/api/auth/callback/%2e%2e/me", "/api/auth/callback/x", "/api/auth/callback/google/x"):
        res = browser.post(path, headers=foreign)
        assert res.status_code == 403 and "Cross-site" in res.text, path
    assert browser.post("/api/auth/callback/google", headers=foreign).status_code != 403  # the real callback may be posted cross-site


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


def test_a_private_copy_can_load_its_own_collection_and_sign_in_keeps_it(app):
    own = ("Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,Price Bought,"
           "Date Bought,LOW,MID,MARKET\nMy binder,3,0,Lightning Bolt,m11,Magic 2011,149,NearMint,Normal,English,1.5,2020-01-01,1,2,2\n")
    with app.state.db.sessions() as db:
        user = reviewer.seed(db, reset=True, collection=own.encode("utf-8"))
        names = {e.name: e.quantity for e in db.scalars(select(Entry).where(Entry.user_id == user.id))}
        assert names == {"Lightning Bolt": 3}
        reviewer.seed(db)  # what a reviewer sign-in does: the private data is not put back to the made-up one
        assert {e.name for e in db.scalars(select(Entry).where(Entry.user_id == user.id))} == {"Lightning Bolt"}


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


def test_every_demo_card_names_a_real_printing_and_old_demo_data_is_replaced(app):
    """The first real Claude run (2026-10-07) found every demo price at $0: the rows had no set or collector number, so Scryfall
    could not match them. Now each row carries a printing, and a demo account holding an older version is put back at sign-in."""
    assert all(row[2] and row[3] and row[4] for row in reviewer.OWNED), "a demo row without a printing cannot be priced"
    with app.state.db.sessions() as db:
        user = reviewer.seed(db)
        db.query(Import).filter_by(user_id=user.id).update({"filename": "demo-collection.csv"})  # the old version's name
        db.query(Entry).filter_by(user_id=user.id).update({"set_code": None, "collector_number": None})
        db.commit()
        assert db.query(Entry).filter_by(user_id=user.id, set_code=None).count() == len(reviewer.OWNED)
        reviewer.seed(db)  # signing in again
        assert db.query(Entry).filter_by(user_id=user.id, set_code=None).count() == 0
        assert db.query(Import).filter_by(user_id=user.id, filename=reviewer.COLLECTION_FILE).count() == 1


def test_the_first_reviewer_prompt_gets_five_priced_cards_once_the_demo_is_matched_to_the_catalog(browser, app):
    """Acceptance for 'What are my five most valuable cards?' on the demo account (#239): the demo rows, matched to a Scryfall-shaped
    catalog the way the daily sync does it, give five priced cards, not the $0 list the first real Claude run saw."""
    from datetime import date

    from mtg_toolkits.scryfall import Card as SfCard

    from tests.ids import sid
    from vault.sync import sync

    assert browser.post("/api/auth/reviewer-login", json={"passphrase": SECRET}).status_code == 200
    bulk = [SfCard.from_json({"id": sid(f"demo-{i}"), "name": name, "set": code, "collector_number": number, "finishes": ["nonfoil"],
                              "prices": {"usd": f"{max(paid, 0.1) * 3:.2f}"}, "type_line": "Card", "color_identity": [], "cmc": 1,
                              "rarity": "rare", "artist": "A", "image_uris": {"small": "https://img.test/s.jpg", "normal": "https://img.test/n.jpg"}})
            for i, (name, _q, code, _sn, number, _f, paid) in enumerate(reviewer.OWNED)]
    with app.state.db.sessions() as db:
        report = sync(db, bulk, day=date(2026, 10, 7))
        unmatched = db.query(Entry).filter(Entry.scryfall_id.is_(None)).count()
    assert unmatched == 0, f"{unmatched} demo rows have no matching printing: {report}"
    top = browser.get(f"{V1}/collection/stats").json()["most_valuable"]
    assert len(top) >= 5 and all((card.get("market_value") or card.get("value") or 0) > 0 for card in top[:5]) and top[0]["name"] == "Smothering Tithe"
