"""Recoverable deletes (vault-wxy.18, PR 1: decks): a deck deleted by anyone, an assistant included, is kept 30 days and can be put back
from the person's own screen route and by a tool; the way back cannot be destroyed by a token; the 30 days end and the copy is purged;
account erasure takes it; a full recovery space refuses an app's delete instead of discarding earlier items.

The hostile sequence is the reviewer's (review of PR 484, finding M1): the app sends delete_deck with confirm true, which the server
cannot tell from the person's yes. The server's answer to that is not a question: it is that nothing is lost.
"""

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from test_agents import V1, agent, auth, bot, call_tool, make_token  # noqa: F401 - fixtures
from test_mcp_profile import BUDGET_BYTES, C, L, W, hello, listed, run, send  # noqa: F401
from test_tags_api import sign_in_as
from vault import recovery, retention
from vault.api import mcp, mcp_profile
from vault.models import Deck, DeckVersion, RecoveryItem, Share, User
from vault.privacy import export_archive, purge_user

RECOVERY = f"{V1}/recovery"
LIST = "1 Sol Ring\n1 Arcane Signet\n1 Command Tower"


def make_deck(client, name="Ramp", text="1 Sol Ring\n1 Arcane Signet", **extra):
    res = client.post(f"{V1}/decks", json={"name": name, "text": text, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def me(db, email="dev@localhost"):
    return db.scalar(select(User).where(User.email == email))


def items(app, email="dev@localhost"):
    with app.state.db.sessions() as db:
        return list(db.scalars(select(RecoveryItem).where(RecoveryItem.user_id == me(db, email).id).order_by(RecoveryItem.id)))


def delete_as_app(bot, token, deck_id):
    return bot.delete(f"{V1}/decks/{deck_id}", headers=auth(token))


# -- 1. the hostile sequence: delete by an app, then recover by the screen route and by tool ----------------------------------------

def test_an_app_deletes_a_deck_with_confirm_true_and_it_comes_back_by_the_screen_route(agent, bot):
    deck = make_deck(agent, text=LIST)
    write = make_token(agent, scopes=["read", "write"])
    res = call_tool(bot, write, "delete_deck", deck_id=deck["id"], confirm=True)  # what a hostile or injected assistant sends
    gone = res["structuredContent"]
    assert gone["deleted"] is True and gone["recovery_id"] and "restore_deleted" in gone["restore"]
    until = datetime.fromisoformat(gone["recoverable_until"])
    assert timedelta(days=29, hours=23) < until - datetime.now(timezone.utc) <= timedelta(days=30)
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 404
    assert agent.get(f"{V1}/decks").json()["total"] == 0  # the list and its count no longer show it

    shown = agent.get(RECOVERY).json()  # the person's own screen asks this
    assert shown["total"] == 1 and shown["days"] == 30
    row = shown["items"][0]
    assert row["kind"] == "deck" and row["name"] == "Ramp" and row["id"] == gone["recovery_id"]
    assert row["deleted_by"]["who"] == "assistant" and "my agent" in row["deleted_by"]["app"]  # the screen says who did it
    back = agent.post(f"{RECOVERY}/{row['id']}/restore")
    assert back.status_code == 200 and back.json()["restored"] is True and back.json()["deck"]["id"] == deck["id"]
    again = agent.get(f"{V1}/decks/{deck['id']}").json()
    assert again["name"] == "Ramp" and "Command Tower" in again["text"]  # the same deck, the same id
    kept = agent.get(RECOVERY).json()["items"]  # the copy stays until its 30 days are over, marked as restored
    assert len(kept) == 1 and kept[0]["restored"]["deck_exists"] is True and kept[0]["restored"]["deck_id"] == deck["id"]
    assert agent.post(f"{RECOVERY}/{row['id']}/restore").status_code == 409  # not twice: no duplicate deck
    assert agent.get(f"{V1}/decks").json()["total"] == 1


def test_the_same_deletion_is_restored_by_a_tool(agent, bot):
    deck = make_deck(agent, text=LIST)
    write = make_token(agent, scopes=["read", "write"])
    assert call_tool(bot, write, "delete_deck", deck_id=deck["id"], confirm=True)["structuredContent"]["deleted"]
    listing = call_tool(bot, write, "list_recently_deleted")["structuredContent"]
    assert [i["name"] for i in listing["items"]] == ["Ramp"]
    done = call_tool(bot, write, "restore_deleted", item_id=listing["items"][0]["id"])["structuredContent"]
    assert done["restored"] is True and done["deck"]["name"] == "Ramp"
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 200
    assert call_tool(bot, write, "restore_deleted", item_id=listing["items"][0]["id"]).get("isError")  # used up: not twice


def test_the_preview_of_a_delete_keeps_nothing_and_deletes_nothing(agent, bot):
    deck = make_deck(agent)
    write = make_token(agent, scopes=["read", "write"])
    assert call_tool(bot, write, "delete_deck", deck_id=deck["id"])["structuredContent"]["name"] == "Ramp"
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 200 and agent.get(RECOVERY).json()["total"] == 0


def test_a_whole_hostile_run_of_deletes_is_cut_at_30_a_minute_and_every_deck_is_recoverable(agent, bot):
    write = make_token(agent, scopes=["read", "write"])
    ids = [make_deck(agent, name=f"Deck {n}")["id"] for n in range(31)]
    codes = [delete_as_app(bot, write, i).status_code for i in ids]
    assert codes == [200] * 30 + [429]  # one loop cannot empty the account in seconds
    assert agent.get(f"{V1}/decks/{ids[30]}").status_code == 200
    listing = agent.get(RECOVERY, params={"limit": 100}).json()
    assert listing["total"] == 30
    for row in listing["items"]:
        assert agent.post(f"{RECOVERY}/{row['id']}/restore").status_code == 200
    assert agent.get(f"{V1}/decks").json()["total"] == 31


def test_a_person_deleting_from_the_web_app_is_kept_too_and_named_as_the_person(agent):
    deck = make_deck(agent)
    assert agent.delete(f"{V1}/decks/{deck['id']}").json()["deleted"] is True
    row = agent.get(RECOVERY).json()["items"][0]
    assert row["deleted_by"] == {"who": "person", "app": None}


def test_a_restored_deck_keeps_its_id_its_saved_versions_and_what_was_last_seen(app, agent):
    deck = make_deck(agent, text="1 Sol Ring", source_url="https://archidekt.com/decks/1", source_author="Ann", format="commander")
    assert agent.put(f"{V1}/decks/{deck['id']}", json={"name": "Ramp 2", "text": LIST}).status_code == 200
    with app.state.db.sessions() as db:
        versions = [v.id for v in db.scalars(select(DeckVersion).where(DeckVersion.deck_id == deck["id"]).order_by(DeckVersion.id))]
        row = db.get(Deck, deck["id"])
        row.viewed_version_id = versions[0]
        db.commit()
    assert len(versions) == 2
    agent.delete(f"{V1}/decks/{deck['id']}")
    with app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DeckVersion)) == 0  # the live rows really went
    item = agent.get(RECOVERY).json()["items"][0]
    assert item["summary"]["saved_versions"] == 2
    assert agent.post(f"{RECOVERY}/{item['id']}/restore").json()["versions_restored"] == 2
    with app.state.db.sessions() as db:
        back = db.get(Deck, deck["id"])
        assert (back.name, back.source_url, back.source_author, back.format) == ("Ramp 2", "https://archidekt.com/decks/1", "Ann", "commander")
        assert [v.id for v in db.scalars(select(DeckVersion).where(DeckVersion.deck_id == back.id).order_by(DeckVersion.id))] == versions
        assert back.viewed_version_id == versions[0]


def test_a_restore_does_not_reopen_the_shares_and_no_invite_or_grantee_is_kept(app, agent):
    deck = make_deck(agent)
    invite = agent.post(f"{V1}/shares", json={"kind": "deck", "deck_id": deck["id"]}).json()
    sign_in_as(agent, "bob@example.com")
    assert agent.post(f"{V1}/shares/accept", json={"token": invite["url"].split("invite=")[1]}).status_code == 200
    sign_in_as(agent, "dev@localhost")
    agent.delete(f"{V1}/decks/{deck['id']}")
    item = agent.get(RECOVERY).json()["items"][0]
    assert item["summary"]["shares_ended"] == {"active": 1, "pending": 0} and item["summary"]["shares_note"]
    with app.state.db.sessions() as db:
        raw = json.dumps(recovery._unpack(db.get(RecoveryItem, item["id"])))
        assert "token_hash" not in raw and "grantee" not in raw and "bob" not in raw
    done = agent.post(f"{RECOVERY}/{item['id']}/restore").json()
    assert done["shares"] and "not reopened" in done["shares"]
    with app.state.db.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Share)) == 0
    sign_in_as(agent, "bob@example.com")
    assert agent.get(f"{V1}/shared").json()["total"] == 0  # bob still has no access
    sign_in_as(agent, "dev@localhost")


def test_a_restore_is_refused_when_the_account_already_has_the_most_decks_and_works_once_there_is_room(app, agent):
    object.__setattr__(app.state.settings, "max_decks", 1)
    first = make_deck(agent, name="First")
    agent.delete(f"{V1}/decks/{first['id']}")
    second = make_deck(agent, name="Second")
    item = agent.get(RECOVERY).json()["items"][0]
    refused = agent.post(f"{RECOVERY}/{item['id']}/restore")
    assert refused.status_code == 409 and "most the Vault keeps" in refused.json()["detail"]
    agent.delete(f"{V1}/decks/{second['id']}")
    assert agent.post(f"{RECOVERY}/{item['id']}/restore").status_code == 200


# -- 2. the 30 days ---------------------------------------------------------------------------------------------------------------

def age(app, item_id, expires_in: timedelta):
    with app.state.db.sessions() as db:
        db.get(RecoveryItem, item_id).expires_at = datetime.now(timezone.utc) + expires_in
        db.commit()


def test_it_is_restorable_until_day_30_then_refused_hidden_and_purged_by_the_daily_job(app, agent):
    keep = make_deck(agent, name="Keep")
    old = make_deck(agent, name="Old")
    agent.delete(f"{V1}/decks/{keep['id']}")
    agent.delete(f"{V1}/decks/{old['id']}")
    a, b = (i.id for i in items(app))
    with app.state.db.sessions() as db:
        first = db.get(RecoveryItem, a)
        span = first.expires_at.astimezone(timezone.utc) - first.created_at.astimezone(timezone.utc)  # as instants: a local zone's clock change is not a day
        assert span == timedelta(days=30)
    age(app, a, timedelta(minutes=5))  # day 29 and 23 hours: still there
    age(app, b, -timedelta(seconds=1))  # day 30 passed
    gone = agent.post(f"{RECOVERY}/{b}/restore")
    assert gone.status_code == 404 and "30 days are over" in gone.json()["detail"]  # refused although the row still exists
    with app.state.db.sessions() as db:
        assert db.get(RecoveryItem, b) is not None
        report = retention.apply(db)
        assert report["recovery_items_deleted"] == 1  # the daily job deletes what expired and only that
        assert db.get(RecoveryItem, b) is None and db.get(RecoveryItem, a) is not None
    assert agent.post(f"{RECOVERY}/{a}/restore").status_code == 200
    assert agent.post(f"{RECOVERY}/{b}/restore").status_code == 404


def test_listing_hides_and_deletes_the_expired_even_if_the_daily_job_never_ran(app, agent):
    deck = make_deck(agent)
    agent.delete(f"{V1}/decks/{deck['id']}")
    age(app, items(app)[0].id, -timedelta(days=1))
    assert agent.get(RECOVERY).json()["total"] == 0
    assert items(app) == []


def test_erasing_the_account_removes_the_recovery_items_and_the_export_holds_them_while_they_exist(app, agent):
    deck = make_deck(agent, name="Secret brew", text=LIST)
    agent.delete(f"{V1}/decks/{deck['id']}")
    with app.state.db.sessions() as db:
        user = me(db)
        archive = zipfile.ZipFile(io.BytesIO(export_archive(db, user)))
        held = json.loads(archive.read("recently_deleted.json"))
        assert held[0]["name"] == "Secret brew" and "Command Tower" in held[0]["data"]["deck"]["text"]
        assert "recently_deleted.json" in archive.read("README.txt").decode()
        assert purge_user(db, user.id)["recovery_items"] == 1
        assert db.scalar(select(func.count()).select_from(RecoveryItem)) == 0


# -- 3. the way back cannot be destroyed by a token -------------------------------------------------------------------------------

def test_removing_an_item_for_good_is_the_persons_alone_a_token_is_refused(app, agent, bot):
    deck = make_deck(agent)
    write = make_token(agent, scopes=["read", "write"])
    delete_as_app(bot, write, deck["id"])
    item = agent.get(RECOVERY).json()["items"][0]["id"]
    refused = bot.delete(f"{RECOVERY}/{item}", headers=auth(write))
    assert refused.status_code == 403 and "can't manage the account" in refused.json()["detail"]
    assert len(items(app)) == 1
    assert not any(t.name in ("purge_deleted", "remove_deleted", "empty_recently_deleted") for t in mcp.TOOLS)  # no tool does it either
    assert agent.delete(f"{RECOVERY}/{item}").json() == {"deleted": True}  # the person, in the web app
    assert items(app) == [] and agent.post(f"{RECOVERY}/{item}/restore").status_code == 404


def test_a_read_only_token_lists_but_cannot_restore_and_the_restore_tool_is_not_even_listed(agent, bot):
    deck = make_deck(agent)
    write = make_token(agent, scopes=["read", "write"])
    delete_as_app(bot, write, deck["id"])
    read = make_token(agent)
    names = {t["name"] for t in bot.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=auth(read)).json()["result"]["tools"]}
    assert "list_recently_deleted" in names and "restore_deleted" not in names
    assert len(call_tool(bot, read, "list_recently_deleted")["structuredContent"]["items"]) == 1
    item = agent.get(RECOVERY).json()["items"][0]["id"]
    assert call_tool(bot, read, "restore_deleted", item_id=item).get("isError")
    assert bot.post(f"{RECOVERY}/{item}/restore", headers=auth(read)).status_code == 403


def test_nobody_sees_or_restores_or_removes_another_persons_deleted_deck(app, agent):
    deck = make_deck(agent)
    agent.delete(f"{V1}/decks/{deck['id']}")
    item = agent.get(RECOVERY).json()["items"][0]["id"]
    sign_in_as(agent, "bob@example.com")
    assert agent.get(RECOVERY).json()["total"] == 0
    assert agent.post(f"{RECOVERY}/{item}/restore").status_code == 404
    assert agent.delete(f"{RECOVERY}/{item}").status_code == 404
    sign_in_as(agent, "dev@localhost")
    assert len(items(app)) == 1


# -- 4. the space: an app is refused, the person makes room -----------------------------------------------------------------------

def test_when_the_space_is_full_an_apps_delete_is_refused_and_nothing_is_lost_but_the_person_can_still_delete(app, agent, bot, monkeypatch):
    monkeypatch.setattr(recovery, "MAX_ITEMS", 2)
    write = make_token(agent, scopes=["read", "write"])
    ids = [make_deck(agent, name=f"D{n}")["id"] for n in range(4)]
    assert [delete_as_app(bot, write, i).status_code for i in ids[:2]] == [200, 200]
    refused = delete_as_app(bot, write, ids[2])
    assert refused.status_code == 409 and "Nothing was deleted" in refused.json()["detail"]
    assert agent.get(f"{V1}/decks/{ids[2]}").status_code == 200  # still there
    assert [i["name"] for i in agent.get(RECOVERY).json()["items"]] == ["D1", "D0"]  # the earlier two were not pushed out
    made_room = agent.delete(f"{V1}/decks/{ids[2]}")  # the person is not blocked: the oldest app item makes room, and it is said
    assert made_room.status_code == 200 and [d["name"] for d in made_room.json()["recovery_dropped"]] == ["D0"]
    assert [i["name"] for i in agent.get(RECOVERY).json()["items"]] == ["D2", "D1"]


def test_the_size_cap_works_the_same_way(app, agent, bot, monkeypatch):
    write = make_token(agent, scopes=["read", "write"])
    first, second = make_deck(agent, name="One", text=LIST)["id"], make_deck(agent, name="Two", text=LIST)["id"]
    assert delete_as_app(bot, write, first).status_code == 200
    only = items(app)[0].stored_bytes
    monkeypatch.setattr(recovery, "MAX_BYTES", only + 10)  # room for one
    assert delete_as_app(bot, write, second).status_code == 409
    assert agent.get(f"{V1}/decks/{second}").status_code == 200


# -- 5. a compact (Codex family) connection reaches it through the dispatchers, at no cost to the tool list ------------------------

def test_a_compact_client_lists_and_restores_through_vault_call_and_vault_write_and_the_list_is_the_same_size(agent, bot):
    deck = make_deck(agent, text=LIST)
    write = make_token(agent, scopes=["read", "write"])
    session = hello(bot, write, "codex-mcp-client")
    tools = listed(bot, write, session)
    assert [t["name"] for t in tools] == [*mcp_profile.CORE, L, C, W]  # not one tool more: the two new ones are not core
    assert "restore_deleted" not in mcp_profile.CORE and "list_recently_deleted" not in mcp_profile.CORE
    assert len(json.dumps(tools)) < BUDGET_BYTES
    # delete is not reachable through the dispatchers (a presentation policy), restore is
    refusal = run(bot, write, session, W, "delete_deck", {"deck_id": deck["id"], "confirm": True})
    assert "web app" in json.dumps(refusal)
    assert delete_as_app(bot, write, deck["id"]).status_code == 200
    found = send(bot, write, session, "tools/call", {"name": L, "arguments": {"query": "deleted"}})["result"]["structuredContent"]
    rows = {r["name"]: r for r in found["tools"]}
    assert rows["restore_deleted"]["run_with"] == W and rows["list_recently_deleted"]["run_with"] == C
    shown = run(bot, write, session, C, "list_recently_deleted")["result"]["structuredContent"]
    assert [i["name"] for i in shown["items"]] == ["Ramp"]
    refused = run(bot, write, session, C, "restore_deleted", {"item_id": shown["items"][0]["id"]})
    assert "run it with vault_write" in json.dumps(refused)  # it changes something: the write dispatcher
    done = run(bot, write, session, W, "restore_deleted", {"item_id": shown["items"][0]["id"]})["result"]["structuredContent"]
    assert done["restored"] is True
    assert agent.get(f"{V1}/decks/{deck['id']}").status_code == 200


def test_the_new_tools_are_classified_and_the_delete_tool_says_where_the_way_back_is():
    tools = mcp.BY_NAME
    assert tools["list_recently_deleted"].write is False and tools["restore_deleted"].write is True
    assert tools["restore_deleted"].destructive is False  # a restore deletes nothing: the compact write dispatcher may run it
    assert {"list_recently_deleted", "restore_deleted"} <= mcp.OWN_DATA_ONLY
    assert "restore_deleted" in tools["delete_deck"].description and "30 days" in tools["delete_deck"].description


# -- 6. what cannot be put back is said, not guessed ------------------------------------------------------------------------------

def test_an_unreadable_copy_is_reported_never_a_500_and_can_still_be_removed_and_exported(app, agent):
    deck = make_deck(agent)
    agent.delete(f"{V1}/decks/{deck['id']}")
    item = items(app)[0]
    with app.state.db.sessions() as db:
        db.get(RecoveryItem, item.id).payload = b"not zlib"
        db.commit()
    res = agent.post(f"{RECOVERY}/{item.id}/restore")
    assert res.status_code == 409 and "cannot be read" in res.json()["detail"]
    with app.state.db.sessions() as db:
        archive = zipfile.ZipFile(io.BytesIO(export_archive(db, me(db))))
        assert json.loads(archive.read("recently_deleted.json"))[0]["readable"] is False
    assert agent.delete(f"{RECOVERY}/{item.id}").json() == {"deleted": True}
