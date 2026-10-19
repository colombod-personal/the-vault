"""Recoverable deletes under pressure (vault-wxy.18, PR 1; the independent review of PR 485 and Copilot's findings): the copy and the
delete are one transaction, one removal/restore/creation at a time per person, a restore never destroys the original, a hostile app
cannot make the person's own delete discard the person's own items, and a damaged copy is a plain 409.

Each test names the protection it needs: take that line out (``recovery.lock_account``, the ``FOR UPDATE`` read in ``delete_deck``, the
commit order, the ``v`` check, the cap on decompression) and the test fails.
"""

import json
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from test_agents import V1, agent, auth, bot, call_tool, make_token  # noqa: F401 - fixtures
from test_recovery import RECOVERY, delete_as_app, items, make_deck
from vault import recovery, reviewer
from vault.models import Deck, RecoveryItem


def gate(monkeypatch, parties: int) -> None:
    """Hold the first ``parties`` callers of ``recovery.lock_account`` until all have arrived, then let them take the lock: two requests
    that reach it at the same moment, as two browser tabs or two apps would."""
    barrier = threading.Barrier(parties, timeout=15)
    real = recovery.lock_account
    seen = {"n": 0}
    guard = threading.Lock()

    def wrapped(db, user):
        with guard:
            seen["n"] += 1
            waits = seen["n"] <= parties
        if waits:
            barrier.wait()
        return real(db, user)

    monkeypatch.setattr(recovery, "lock_account", wrapped)


def together(*calls):
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result() for f in [pool.submit(c) for c in calls]]


def payload_of(app, item_id: int) -> dict:
    with app.state.db.sessions() as db:
        return json.loads(zlib.decompress(db.get(RecoveryItem, item_id).payload))


def set_payload(app, item_id: int, payload) -> None:
    raw = payload if isinstance(payload, bytes) else zlib.compress(json.dumps(payload).encode())
    with app.state.db.sessions() as db:
        db.get(RecoveryItem, item_id).payload = raw
        db.commit()


# -- one at a time per person -----------------------------------------------------------------------------------------------------

def test_two_deletes_of_the_same_deck_at_once_leave_one_copy_and_the_second_finds_nothing(app, agent, bot, monkeypatch):
    deck = make_deck(agent)
    write = make_token(agent, scopes=["read", "write"])
    gate(monkeypatch, 2)
    codes = sorted(r.status_code for r in together(lambda: delete_as_app(bot, write, deck["id"]),
                                                   lambda: agent.delete(f"{V1}/decks/{deck['id']}")))
    assert codes == [200, 404]
    assert len(items(app)) == 1  # not two copies that would restore two decks


def test_an_edit_committed_just_before_the_delete_is_in_the_copy(app, agent, monkeypatch):
    deck = make_deck(agent, text="1 Sol Ring")
    real = recovery.lock_account

    def edit_first(db, user):  # another tab saves the deck after the delete started, before it holds the lock
        assert agent.put(f"{V1}/decks/{deck['id']}", json={"name": "Ramp, edited", "text": "1 Sol Ring\n1 Arcane Signet"}).status_code == 200
        monkeypatch.setattr(recovery, "lock_account", real)
        real(db, user)

    monkeypatch.setattr(recovery, "lock_account", edit_first)
    assert agent.delete(f"{V1}/decks/{deck['id']}").status_code == 200
    item = items(app)[0]
    assert item.label == "Ramp, edited" and "Arcane Signet" in payload_of(app, item.id)["deck"]["text"]


def test_an_edit_that_arrives_while_the_delete_holds_the_deck_waits_and_then_finds_it_gone(app, agent, monkeypatch):
    deck = make_deck(agent, text="1 Sol Ring")
    real = recovery.keep_deck
    seen = {}

    def while_holding(db, user, d, **kwargs):
        edit = threading.Thread(target=lambda: seen.setdefault("put", agent.put(
            f"{V1}/decks/{deck['id']}", json={"name": "Late edit", "text": "1 Sol Ring\n1 Arcane Signet"}).status_code))
        edit.start()
        edit.join(1.5)  # the deck row is locked by this transaction: the edit cannot finish until the delete has
        seen["finished_early"] = not edit.is_alive()
        seen["thread"] = edit
        return real(db, user, d, **kwargs)

    monkeypatch.setattr(recovery, "keep_deck", while_holding)
    assert agent.delete(f"{V1}/decks/{deck['id']}").status_code == 200
    seen["thread"].join(15)
    assert seen["finished_early"] is False and seen["put"] == 404  # not an edit that is saved and then deleted unseen by the copy
    assert items(app)[0].label == "Ramp"


def test_two_apps_deleting_at_once_with_room_for_one_get_one_copy_and_one_refusal(app, agent, bot, monkeypatch):
    monkeypatch.setattr(recovery, "MAX_ITEMS", 1)
    write = make_token(agent, scopes=["read", "write"])
    a, b = make_deck(agent, name="A")["id"], make_deck(agent, name="B")["id"]
    gate(monkeypatch, 2)
    codes = sorted(r.status_code for r in together(lambda: delete_as_app(bot, write, a), lambda: delete_as_app(bot, write, b)))
    assert codes == [200, 409]
    assert len(items(app)) == 1 and agent.get(f"{V1}/decks").json()["total"] == 1  # the refused one still exists


def test_a_restore_and_a_new_deck_at_once_with_one_slot_left_cannot_both_get_it(app, agent, monkeypatch):
    object.__setattr__(app.state.settings, "max_decks", 1)
    gone = make_deck(agent, name="Gone")
    agent.delete(f"{V1}/decks/{gone['id']}")
    item = items(app)[0].id
    gate(monkeypatch, 2)
    codes = together(lambda: agent.post(f"{RECOVERY}/{item}/restore").status_code,
                     lambda: agent.post(f"{V1}/decks", json={"name": "New", "text": "1 Sol Ring"}).status_code)
    assert sorted(codes) in ([200, 409], [201, 409]) and sorted(codes) != [200, 201]
    with app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Deck)) == 1


def test_two_restores_of_the_same_item_at_once_restore_it_once(app, agent, monkeypatch):
    deck = make_deck(agent)
    agent.delete(f"{V1}/decks/{deck['id']}")
    item = items(app)[0].id
    gate(monkeypatch, 2)
    codes = sorted(together(lambda: agent.post(f"{RECOVERY}/{item}/restore").status_code,
                            lambda: agent.post(f"{RECOVERY}/{item}/restore").status_code))
    assert codes == [200, 409]
    assert agent.get(f"{V1}/decks").json()["total"] == 1


def test_a_restore_that_meets_a_taken_id_or_a_conflict_answers_a_plain_409(app, agent, monkeypatch):
    deck = make_deck(agent)
    agent.delete(f"{V1}/decks/{deck['id']}")
    item = items(app)[0].id

    def clash(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(recovery, "_restore_deck", clash)
    res = agent.post(f"{RECOVERY}/{item}/restore")
    assert res.status_code == 409 and "changed at the same moment" in res.json()["detail"]
    monkeypatch.undo()
    assert agent.post(f"{RECOVERY}/{item}/restore").status_code == 200  # nothing was half done


# -- the copy and the delete are one transaction ----------------------------------------------------------------------------------

def test_a_failure_between_the_copy_and_the_delete_leaves_the_deck_and_no_copy(app, agent, monkeypatch):
    deck = make_deck(agent)
    real = Session.delete

    def failing(self, obj, *args, **kwargs):
        if isinstance(obj, Deck):
            raise RuntimeError("the delete failed")
        return real(self, obj, *args, **kwargs)

    monkeypatch.setattr(Session, "delete", failing)
    with pytest.raises(RuntimeError):
        agent.delete(f"{V1}/decks/{deck['id']}")
    monkeypatch.undo()
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 200 and items(app) == []  # no copy of a deck that was not deleted


def test_a_failure_at_the_commit_leaves_the_deck_and_no_copy(app, agent, monkeypatch):
    deck = make_deck(agent)
    real = Session.commit

    def failing(self):
        if any(isinstance(o, Deck) for o in self.deleted):
            raise RuntimeError("the commit failed")
        return real(self)

    monkeypatch.setattr(Session, "commit", failing)
    with pytest.raises(RuntimeError):
        agent.delete(f"{V1}/decks/{deck['id']}")
    monkeypatch.undo()
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 200 and items(app) == []


# -- the original cannot be destroyed by restoring, editing and deleting again --------------------------------------------------

def test_restore_then_twenty_edits_then_delete_still_leaves_the_original_to_restore_again(app, agent, bot):
    original = "1 Sol Ring\n1 Arcane Signet\n1 Command Tower"
    deck = make_deck(agent, name="Original", text=original)
    write = make_token(agent, scopes=["read", "write"])
    assert delete_as_app(bot, write, deck["id"]).status_code == 200
    first = agent.get(RECOVERY).json()["items"][0]["id"]
    assert call_tool(bot, write, "restore_deleted", item_id=first)["structuredContent"]["restored"] is True
    for n in range(22):  # more than the 20 saved versions a deck keeps: the original leaves the deck's own history
        edited = bot.put(f"{V1}/decks/{deck['id']}", json={"name": "Original", "text": f"1 Sol Ring\n1 Card Number {n}"}, headers=auth(write))
        assert edited.status_code == 200
    assert delete_as_app(bot, write, deck["id"]).status_code == 200
    listed = agent.get(RECOVERY).json()["items"]
    assert len(listed) == 2  # the second delete is an item of its own: it did not replace or lose the first
    old = next(i for i in listed if i["id"] == first)
    assert old["restored"]["deck_exists"] is False
    back = agent.post(f"{RECOVERY}/{first}/restore")
    assert back.status_code == 200
    assert agent.get(f"{V1}/decks/{back.json()['deck']['id']}").json()["text"] == original  # the original, not the last edit
    assert agent.post(f"{RECOVERY}/{first}/restore").status_code == 409  # and not twice


# -- the space: the person's own items are never dropped by a hostile app's junk --------------------------------------------------

def test_a_hostile_fill_then_the_persons_delete_drops_the_apps_junk_not_the_persons_older_deck(app, agent, bot, monkeypatch):
    monkeypatch.setattr(recovery, "MAX_ITEMS", 4)
    write = make_token(agent, scopes=["read", "write"])
    agent.delete(f"{V1}/decks/{make_deck(agent, name='Legit old deck')['id']}")
    junk = [make_deck(agent, name=f"junk{n}")["id"] for n in range(5)]
    assert [delete_as_app(bot, write, i).status_code for i in junk] == [200, 200, 200, 409, 409]
    mine = agent.delete(f"{V1}/decks/{make_deck(agent, name='Mine')['id']}")
    assert mine.status_code == 200 and [d["name"] for d in mine.json()["recovery_dropped"]] == ["junk0"]  # said, and only the app's
    names = [i["name"] for i in agent.get(RECOVERY).json()["items"]]
    assert "Legit old deck" in names and "Mine" in names and "junk0" not in names


def test_when_the_space_is_full_of_the_persons_own_decks_the_persons_delete_is_refused_with_a_sentence_and_drops_nothing(app, agent, monkeypatch):
    monkeypatch.setattr(recovery, "MAX_ITEMS", 2)
    ids = [make_deck(agent, name=f"Mine {n}")["id"] for n in range(3)]
    assert [agent.delete(f"{V1}/decks/{i}").status_code for i in ids[:2]] == [200, 200]
    refused = agent.delete(f"{V1}/decks/{ids[2]}")
    assert refused.status_code == 409 and "full of decks you deleted yourself" in refused.json()["detail"]
    assert agent.get(f"{V1}/decks/{ids[2]}").status_code == 200  # still there
    assert [i["name"] for i in agent.get(RECOVERY).json()["items"]] == ["Mine 1", "Mine 0"]  # nothing dropped


def test_an_apps_burst_of_deletes_does_not_use_up_the_persons_own_limit(agent, bot):
    write = make_token(agent, scopes=["read", "write"])
    ids = [make_deck(agent, name=f"D{n}")["id"] for n in range(32)]
    assert [delete_as_app(bot, write, i).status_code for i in ids[:30]] == [200] * 30
    assert delete_as_app(bot, write, ids[30]).status_code == 429
    assert agent.delete(f"{V1}/decks/{ids[31]}").status_code == 200  # the person is not blocked by the app's 30


# -- a damaged copy is a plain answer, never a 500 ---------------------------------------------------------------------------------

def test_a_copy_of_another_format_or_with_missing_or_wrong_fields_is_unreadable_409_and_stays_removable(app, agent):
    deck = make_deck(agent, text="1 Sol Ring")
    agent.delete(f"{V1}/decks/{deck['id']}")
    item = items(app)[0].id
    good = payload_of(app, item)
    for broken in ({**good, "v": 2},  # a format this code does not know (the check of ``v``)
                   {"v": 1}, {"v": 1, "deck": {"id": 1}, "versions": []},
                   {**good, "versions": "not a list"}, {**good, "deck": "no"}):
        set_payload(app, item, broken)
        res = agent.post(f"{RECOVERY}/{item}/restore")
        assert res.status_code == 409 and "cannot be read" in res.json()["detail"], broken
    assert agent.get(f"{V1}/decks").json()["total"] == 0  # nothing half restored
    assert agent.delete(f"{RECOVERY}/{item}").json() == {"deleted": True}


def test_a_long_name_in_a_copy_is_cut_to_the_columns_length_not_a_500(app, agent):
    deck = make_deck(agent)
    agent.delete(f"{V1}/decks/{deck['id']}")
    item = items(app)[0].id
    good = payload_of(app, item)
    good["deck"]["name"] = "x" * 500
    set_payload(app, item, good)
    done = agent.post(f"{RECOVERY}/{item}/restore")
    assert done.status_code == 200 and len(done.json()["deck"]["name"]) == 200


def test_a_copy_that_would_expand_past_the_cap_is_refused_not_expanded(app, agent, monkeypatch):
    deck = make_deck(agent, text="\n".join(f"1 Card {n}" for n in range(400)))
    agent.delete(f"{V1}/decks/{deck['id']}")
    item = items(app)[0].id
    monkeypatch.setattr(recovery, "MAX_UNPACKED_BYTES", 1000)
    res = agent.post(f"{RECOVERY}/{item}/restore")
    assert res.status_code == 409 and "cannot be read" in res.json()["detail"]
    with pytest.raises(recovery.RecoveryError):
        with app.state.db.sessions() as db:
            recovery._unpack(db.get(RecoveryItem, item))
    monkeypatch.undo()
    assert agent.post(f"{RECOVERY}/{item}/restore").status_code == 200  # within the cap it is fine


# -- the demo account, the typed answers ------------------------------------------------------------------------------------------

def test_resetting_the_demo_account_clears_the_last_reviewers_deleted_decks(app):
    with app.state.db.sessions() as db:
        user = reviewer.seed(db)
        deck = db.scalar(select(Deck).where(Deck.user_id == user.id))
        item, _ = recovery.keep_deck(db, user, deck, assistant=None)
        db.delete(deck)
        db.commit()
        assert db.scalar(select(func.count()).select_from(RecoveryItem)) == 1
        reviewer.seed(db, reset=True)
        assert db.scalar(select(func.count()).select_from(RecoveryItem)) == 0


def test_the_recovery_routes_are_described_by_typed_models_in_the_openapi_document(agent):
    spec = agent.get("/api/openapi.json").json()
    schemas = spec["components"]["schemas"]
    page = spec["paths"]["/api/v1/recovery"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert page["$ref"].endswith("/RecoveryPage") and {"items", "days", "total"} <= set(schemas["RecoveryPage"]["properties"])
    restore = spec["paths"]["/api/v1/recovery/{item_id}/restore"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert restore["$ref"].endswith("/RestoreResult") and {"restored", "deck", "versions_restored"} <= set(schemas["RestoreResult"]["properties"])
    assert {"id", "name", "deleted_by", "restorable_until", "restored"} <= set(schemas["RecoveryItem"]["properties"])
    assert "recovery_id" in schemas["DeckDeleted"]["properties"]
