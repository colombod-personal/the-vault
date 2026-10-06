"""Small edits to the cards a person owns, through their assistant (vault/owned_changes.py, #83,
docs/owned-cards-updates.md): previews change nothing, a confirmation applies exactly what was shown and nothing
else, stale or tampered confirmations are refused, the printing is the person's choice, caps hold, every change set
is in the history with its app and can be undone, and nobody else's collection is touched."""

import pytest
from fastapi.testclient import TestClient

from test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from vault import catalog_sync as cs
from vault import owned_changes
from vault.models import Card, Entry, Import, User

SOL_RING = "33333333-3333-3333-3333-333333333333"
KILLER = "44444444-4444-4444-4444-444444444444"


def oracle(oracle_id, name):
    return {"object": "card", "id": "p-" + oracle_id[:4], "oracle_id": oracle_id, "name": name, "layout": "normal",
            "mana_cost": "{1}", "cmc": 1.0, "type_line": "Artifact", "oracle_text": "", "colors": [],
            "color_identity": [], "keywords": [], "legalities": {"commander": "legal"}, "digital": False}


@pytest.fixture
def cards(app):
    """Sol Ring (owned: C21 263 foil) with a second printing (CMR 472), and A Killer Among Us (owned: MKM 167)."""
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, [oracle(SOL_RING, "Sol Ring"), oracle(KILLER, "A Killer Among Us")])
        db.add_all([
            Card(scryfall_id="sol-c21", oracle_id=SOL_RING, name="Sol Ring", set_code="c21", set_name="Commander 2021",
                 collector_number="263"),
            Card(scryfall_id="sol-cmr", oracle_id=SOL_RING, name="Sol Ring", set_code="cmr", set_name="Commander Legends",
                 collector_number="472"),
            Card(scryfall_id="killer-mkm", oracle_id=KILLER, name="A Killer Among Us", set_code="mkm",
                 set_name="Murders at Karlov Manor", collector_number="167"),
        ])
        db.commit()


@pytest.fixture
def write(agent, cards):
    return make_token(agent, scopes=["read", "write"])


def copies(app, name, set_code=None):
    with app.state.db.sessions() as db:
        rows = db.query(Entry).filter(Entry.name == name).all()
        return sum(e.quantity for e in rows if set_code is None or str(e.set_code).lower() == set_code)


def total(app):
    with app.state.db.sessions() as db:
        return sum(e.quantity for e in db.query(Entry).all())


def preview(bot, token, *lines):
    out = call_tool(bot, token, "update_owned_cards", lines=list(lines))
    assert not out.get("isError"), out
    return out["structuredContent"]


def confirm(bot, token, lines, confirmation):
    return call_tool(bot, token, "confirm_owned_cards_update", lines=lines, confirmation=confirmation)


ADD_CMR = {"action": "add", "name": "Sol Ring", "quantity": 2, "set": "CMR", "number": "472"}


def test_preview_changes_nothing_and_confirm_applies_exactly_it(app, agent, bot, write):
    before, imports = total(app), agent.get(f"{V1}/imports").json()["total"]
    seen = preview(bot, write, ADD_CMR)
    line = seen["lines"][0]
    assert seen["ready"] and line["status"] == "ready" and line["copies_before"] == 0 and line["copies_after"] == 2
    assert line["printing"] == {"set": "cmr", "number": "472", "finish": "nonfoil"}
    assert "_resolved" not in seen and seen["copies_added"] == 2
    assert total(app) == before and agent.get(f"{V1}/imports").json()["total"] == imports  # nothing changed yet
    done = confirm(bot, write, [ADD_CMR], seen["confirmation"])
    assert not done.get("isError"), done
    assert total(app) == before + 2 and copies(app, "Sol Ring", "cmr") == 2 and copies(app, "Sol Ring", "c21") == 1
    history = agent.get(f"{V1}/imports").json()["items"][0]
    assert history["kind"] == "assistant" and history["app"] == "token 'my agent'"
    assert history["changes"]["added"] == 1 and history["changes"]["copies_in"] == 2  # an import's summary shape
    assert history["lines"][0]["before"] == 0 and history["lines"][0]["after"] == 2
    assert history["lines"][0]["folders"] == [{"folder": None, "copies": 2}]


def test_a_confirmation_works_only_for_the_lines_it_was_given_for(app, bot, write):
    seen = preview(bot, write, ADD_CMR)
    more = {**ADD_CMR, "quantity": 3}
    assert confirm(bot, write, [more], seen["confirmation"]).get("isError")
    assert confirm(bot, write, [ADD_CMR], seen["confirmation"][:-4] + "AAAA").get("isError")
    assert copies(app, "Sol Ring", "cmr") == 0


def test_a_confirmation_is_refused_once_the_collection_changed(app, agent, bot, write):
    seen = preview(bot, write, ADD_CMR)
    first = preview(bot, write, {"action": "remove", "name": "A Killer Among Us", "quantity": 1})
    assert not confirm(bot, write, [{"action": "remove", "name": "A Killer Among Us", "quantity": 1}],
                       first["confirmation"]).get("isError")
    stale = confirm(bot, write, [ADD_CMR], seen["confirmation"])
    assert stale.get("isError") and copies(app, "Sol Ring", "cmr") == 0
    # and used once only: the same confirmation again is stale too
    assert confirm(bot, write, [{"action": "remove", "name": "A Killer Among Us", "quantity": 1}],
                   first["confirmation"]).get("isError")


def test_an_expired_confirmation_is_refused(app, cards, agent):
    with app.state.db.sessions() as db:
        user = db.query(User).first()
        lines = [{"action": "add", "name": "Sol Ring", "quantity": 1, "set": "cmr", "number": "472"}]
        seen = owned_changes.preview(db, user, lines, "secret", lambda s, n: None, now=1_000)
        assert seen["ready"]
        with pytest.raises(owned_changes.ChangeError, match="expired"):
            owned_changes.apply(db, user, lines, seen["confirmation"], "secret", lambda s, n: None, "test",
                                now=1_000 + owned_changes.TOKEN_SECONDS + 1)
        with pytest.raises(owned_changes.ChangeError):  # signed with another secret
            owned_changes.apply(db, user, lines, seen["confirmation"], "other", lambda s, n: None, "test", now=1_001)


def test_the_printing_is_the_persons_choice(app, bot, write):
    # they own one Sol Ring printing: removing "a Sol Ring" means that one
    one = preview(bot, write, {"action": "remove", "name": "Sol Ring", "quantity": 1})
    assert one["lines"][0]["status"] == "ready" and one["lines"][0]["printing"]["set"].lower() == "c21"
    # adding without a printing: ask, offering theirs first and then the others known
    ask = preview(bot, write, {"action": "add", "name": "Sol Ring", "quantity": 1})
    assert not ask["ready"] and "confirmation" not in ask
    line = ask["lines"][0]
    assert line["status"] == "choose_printing"
    assert [c["set"].lower() for c in line["choose_from"]] == ["c21", "cmr"] and line["choose_from"][0]["owned"] == 1
    # they do not know: a name-only add, kept as such
    unknown = preview(bot, write, {"action": "add", "name": "Sol Ring", "quantity": 1, "printing_unknown": True})
    assert unknown["ready"] and unknown["lines"][0]["printing"] == "not specified"
    # removing needs the printing when it is not obvious
    assert not preview(bot, write, {"action": "remove", "name": "Sol Ring", "quantity": 1, "printing_unknown": True})["ready"]


def test_unknown_cards_and_wrong_printings_are_refused_with_suggestions(app, bot, write):
    typo = preview(bot, write, {"action": "add", "name": "Sol Rings", "quantity": 1, "printing_unknown": True})
    assert not typo["ready"] and typo["lines"][0]["status"] == "refused" and "Sol Ring" in typo["lines"][0]["did_you_mean"]
    wrong = preview(bot, write, {"action": "add", "name": "Sol Ring", "quantity": 1, "set": "mkm", "number": "167"})
    assert not wrong["ready"] and wrong["lines"][0]["status"] == "refused"
    too_many = preview(bot, write, {"action": "remove", "name": "Sol Ring", "quantity": 5})
    assert not too_many["ready"] and "own 1" in too_many["lines"][0]["reason"]


REMOVE_ALL_KILLERS = {"action": "set", "name": "A Killer Among Us", "quantity": 0, "set": "mkm", "number": "167"}


def test_a_small_collection_can_still_remove_a_few_cards(app, bot, write):
    # 4 of the fixture's 7 copies: over 10%, but within the floor, so a new person can record selling cards
    assert preview(bot, write, REMOVE_ALL_KILLERS)["ready"]


def test_big_removals_and_long_change_sets_are_refused(app, bot, write, monkeypatch):
    monkeypatch.setattr(owned_changes, "REMOVAL_FLOOR", 1)  # the 10% share cap, on the small fixture collection
    share = preview(bot, write, REMOVE_ALL_KILLERS)
    assert not share["ready"] and "limit" in share["refused"] and "confirmation" not in share
    monkeypatch.setattr(owned_changes, "REMOVAL_FLOOR", 5)
    monkeypatch.setattr(owned_changes, "MAX_REMOVED_COPIES", 3)  # the copies cap
    assert not preview(bot, write, REMOVE_ALL_KILLERS)["ready"]
    long = call_tool(bot, write, "update_owned_cards", lines=[ADD_CMR] * 51)
    assert long.get("isError")


def test_a_read_only_connection_cannot_edit(agent, cards, bot):
    read = make_token(agent)
    assert call_tool(bot, read, "update_owned_cards", lines=[ADD_CMR]).get("isError")
    assert call_tool(bot, read, "confirm_owned_cards_update", lines=[ADD_CMR], confirmation="x" * 20).get("isError")
    assert call_tool(bot, read, "undo_owned_cards_update").get("isError")


def test_undo_previews_then_restores_and_only_the_last_change(app, agent, bot, write):
    before = total(app)
    lines = [ADD_CMR, {"action": "add", "name": "Sol Ring", "quantity": 1, "printing_unknown": True}]
    seen = preview(bot, write, *lines)
    assert not confirm(bot, write, lines, seen["confirmation"]).get("isError")
    assert total(app) == before + 3
    shown = call_tool(bot, write, "undo_owned_cards_update")["structuredContent"]
    assert shown["ready"] and shown["copies_removed"] == 3 and total(app) == before + 3  # a preview changes nothing
    done = call_tool(bot, write, "undo_owned_cards_update", confirmation=shown["confirmation"])
    assert not done.get("isError"), done
    assert total(app) == before and copies(app, "Sol Ring", "cmr") == 0
    assert agent.get(f"{V1}/imports").json()["items"][0]["kind"] == "undo"
    assert call_tool(bot, write, "undo_owned_cards_update").get("isError")  # nothing left to undo


def test_undo_is_refused_after_an_import(app, agent, bot, write):
    from test_agents import CSV
    seen = preview(bot, write, ADD_CMR)
    assert not confirm(bot, write, [ADD_CMR], seen["confirmation"]).get("isError")
    agent.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")})
    assert call_tool(bot, write, "undo_owned_cards_update").get("isError")


def test_edits_touch_only_the_persons_own_collection(app, agent, bot, write):
    with TestClient(agent.app) as bob:
        from test_agents import CSV
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        bob.post(f"{V1}/imports", files={"file": ("e.csv", CSV, "text/csv")})
        bob_write = make_token(bob, scopes=["read", "write"])
    seen = preview(bot, write, ADD_CMR)
    assert confirm(bot, bob_write, [ADD_CMR], seen["confirmation"]).get("isError")  # not Bob's confirmation
    assert not confirm(bot, write, [ADD_CMR], seen["confirmation"]).get("isError")
    with app.state.db.sessions() as db:
        bob_id = db.query(User).filter(User.email == "bob@example.com").one().id
        assert not db.query(Entry).filter(Entry.user_id == bob_id, Entry.set_code == "cmr").count()
        assert db.query(Import).filter(Import.user_id == bob_id, Import.kind != "import").count() == 0
    # and Bob cannot undo Alice's change
    assert call_tool(bot, bob_write, "undo_owned_cards_update").get("isError")
