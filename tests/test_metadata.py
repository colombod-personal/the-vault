"""vault_metadata (#127, #119): namespaces that isolate writers, a version that upgrades, a size limit that refuses, tenancy, scopes.
Pure rules first (vault/metadata.py), then the routes and the MCP tools."""

import pytest

from tests.ids import sid
from tests.test_agents import V1, bot, call_tool, make_token, rpc  # noqa: F401 - fixtures
from tests.test_tags_api import CSV, MOUNTAIN, SOL, card_ids, sign_in_as, stocked  # noqa: F401 - fixtures
from vault import metadata as M
from vault.api import mcp
from vault.models import CardAnnotation

CARDS = f"{V1}/collection/cards"
BUCKETS = f"{V1}/collection/buckets"


# -- the rules ------------------------------------------------------------------------------------------------------------

def test_a_person_writes_user_and_an_assistant_writes_its_own_namespace():
    assert M.namespace_for("person", None) == "user"
    assert M.namespace_for("assistant", "claude.ai") == "ai.claude.ai"
    assert M.namespace_for("assistant", "token 'Claude helper'") == "ai.token-claude-helper"
    assert M.namespace_for("assistant", "!!!") == "ai.app"


def test_writing_replaces_only_the_writers_namespace_and_records_who_and_when():
    doc = M.write({"version": 1}, "user", {"note": "keep"}, by="the person")
    doc = M.write(doc, "ai.claude.ai", {"score": 7}, by="claude.ai")
    assert doc["user"] == {"note": "keep"} and doc["ai.claude.ai"] == {"score": 7}
    assert doc["written"]["ai.claude.ai"]["by"] == "claude.ai" and doc["written"]["ai.claude.ai"]["at"]
    again = M.write(doc, "ai.claude.ai", {"score": 9}, by="claude.ai")
    assert again["user"] == {"note": "keep"} and again["ai.claude.ai"] == {"score": 9}  # replaced as a whole, the person's untouched
    cleared = M.write(again, "ai.claude.ai", {}, by="claude.ai")
    assert "ai.claude.ai" not in cleared and "ai.claude.ai" not in cleared["written"] and cleared["user"] == {"note": "keep"}
    assert M.write({"version": 1}, "user", {}, by="x") == {"version": 1}  # nothing to remove, nothing left


def test_the_system_namespace_cannot_be_written_and_the_limits_refuse_instead_of_truncating():
    with pytest.raises(M.MetadataError) as no:
        M.write({"version": 1}, "system", {"a": 1}, by="x")
    assert no.value.status == 403
    deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}  # 7 levels counting the data itself
    with pytest.raises(M.MetadataError) as too_deep:
        M.write({"version": 1}, "user", deep, by="x")
    assert too_deep.value.status == 413
    with pytest.raises(M.MetadataError) as big:
        M.write({"version": 1}, "user", {"text": "x" * M.MAX_BYTES}, by="x")
    assert big.value.status == 413
    one = M.write({"version": 1}, "user", {"text": "x" * 5000}, by="x")
    with pytest.raises(M.MetadataError):  # the limit is for every writer together
        M.write(one, "ai.a", {"text": "y" * 5000}, by="a")
    assert M.size(M.write({"version": 1}, "user", {"text": "é" * 3000}, by="x")) <= M.MAX_BYTES  # bytes, not characters
    with pytest.raises(M.MetadataError):
        M.write({"version": 1}, "user", {"text": "é" * 4100}, by="x")


def test_an_older_version_is_upgraded_on_read_and_a_newer_one_is_refused(monkeypatch):
    monkeypatch.setattr(M, "CURRENT", 3)
    monkeypatch.setitem(M.UPGRADERS, 1, lambda d: {**d, "user": {"upgraded_from": 1, **d.get("user", {})}})
    monkeypatch.setitem(M.UPGRADERS, 2, lambda d: {**d, "system": {"two": True}})
    seen = M.view({"version": 1, "user": {"note": "n"}})
    assert seen["version"] == 3 and seen["namespaces"]["user"] == {"upgraded_from": 1, "note": "n"} and seen["namespaces"]["system"] == {"two": True}
    assert M.write({"version": 1}, "user", {"a": 1}, by="x")["version"] == 3  # a write stores the current shape
    with pytest.raises(M.MetadataError) as newer:
        M.view({"version": 4})
    assert newer.value.status == 409
    monkeypatch.delitem(M.UPGRADERS, 2)
    with pytest.raises(M.MetadataError):  # a gap is a bug, said loudly
        M.view({"version": 2})
    with pytest.raises(M.MetadataError):
        M.view({"version": "1"})


# -- the routes -----------------------------------------------------------------------------------------------------------

def test_a_person_and_an_assistant_each_keep_their_own_namespace_on_a_card(stocked):
    ids = card_ids(stocked)
    url = f"{CARDS}/{ids['Mountain'][0]}/metadata"
    empty = stocked.get(url).json()
    assert empty["version"] == 1 and empty["namespaces"] == {} and empty["you_write"] == "user"
    mine = stocked.put(url, json={"data": {"note": "for the red deck"}}).json()
    assert mine["namespaces"] == {"user": {"note": "for the red deck"}} and mine["written"]["user"]["by"] == "the person"
    rw = stocked.post(f"{V1}/me/tokens", json={"name": "Claude helper", "scopes": ["read", "write"]}).json()["token"]
    stocked.cookies.clear()
    auth = {"Authorization": f"Bearer {rw}"}
    theirs = stocked.put(url, json={"data": {"roles": ["ramp"]}}, headers=auth).json()
    assert theirs["you_write"] == "ai.token-claude-helper"
    assert theirs["namespaces"] == {"user": {"note": "for the red deck"}, "ai.token-claude-helper": {"roles": ["ramp"]}}
    assert theirs["written"]["ai.token-claude-helper"]["by"] == "token 'Claude helper'"
    stocked.put(url, json={"data": {"roles": ["land"]}}, headers=auth)  # replaces its own, leaves the person's
    assert stocked.get(url, headers=auth).json()["namespaces"]["user"] == {"note": "for the red deck"}
    removed = stocked.delete(url, headers=auth).json()
    assert "ai.token-claude-helper" not in removed["namespaces"] and removed["namespaces"]["user"] == {"note": "for the red deck"}


def test_the_metadata_is_on_the_card_not_the_printing_and_an_empty_one_leaves_no_row(app, stocked):
    ids = card_ids(stocked)
    stocked.put(f"{CARDS}/{ids['Sol Ring'][0]}/metadata", json={"data": {"n": 1}})
    assert stocked.get(f"{CARDS}/{ids['Sol Ring'][1]}/metadata").json()["namespaces"] == {"user": {"n": 1}}  # the other printing
    with app.state.db.sessions() as db:
        assert db.query(CardAnnotation).count() == 1
    assert stocked.delete(f"{CARDS}/{ids['Sol Ring'][1]}/metadata").json()["namespaces"] == {}
    with app.state.db.sessions() as db:
        assert db.query(CardAnnotation).count() == 0


def test_over_the_limit_is_413_and_nothing_is_stored(stocked):
    ids = card_ids(stocked)
    url = f"{CARDS}/{ids['Mountain'][0]}/metadata"
    assert stocked.put(url, json={"data": {"text": "x" * 9000}}).status_code == 413
    assert stocked.get(url).json()["namespaces"] == {}
    assert stocked.put(url, json={"data": ["not", "an", "object"]}).status_code == 422
    assert stocked.put(url, json={}).status_code == 422


def test_unknown_unmatched_and_malformed_card_ids(stocked):
    ids = card_ids(stocked)
    assert stocked.get(f"{CARDS}/0123456789abcdef/metadata").status_code == 404
    assert stocked.put(f"{CARDS}/{ids['Unknown Thing'][0]}/metadata", json={"data": {"a": 1}}).status_code == 422  # unmatched: no oracle id
    assert stocked.get(f"{CARDS}/bad%20id/metadata").status_code == 422


def test_buckets_keep_metadata_the_same_way_and_another_persons_are_404(stocked):
    bucket = stocked.post(BUCKETS, json={"name": "Trade box 2"}).json()
    url = f"{BUCKETS}/{bucket['id']}/metadata"
    assert stocked.put(url, json={"data": {"purpose": "trades"}}).json()["namespaces"] == {"user": {"purpose": "trades"}}
    assert stocked.get(f"{BUCKETS}/{bucket['id']}").json()["vault_metadata"]["user"] == {"purpose": "trades"}  # shown on the bucket too
    sign_in_as(stocked, "bob@example.com")
    assert stocked.get(url).status_code == 404 and stocked.put(url, json={"data": {}}).status_code == 404
    assert stocked.delete(url).status_code == 404
    sign_in_as(stocked, "dev@localhost")
    assert stocked.get(url).json()["namespaces"]["user"] == {"purpose": "trades"}


def test_another_person_cannot_read_or_change_a_cards_metadata(stocked):
    ids = card_ids(stocked)
    url = f"{CARDS}/{ids['Mountain'][0]}/metadata"
    stocked.put(url, json={"data": {"secret": 1}})
    sign_in_as(stocked, "bob@example.com")
    assert stocked.get(url).status_code == 404 and stocked.put(url, json={"data": {"x": 1}}).status_code == 404


def test_a_read_only_token_may_read_metadata_but_not_write_it_and_a_retry_writes_once(stocked):
    ids = card_ids(stocked)
    url = f"{CARDS}/{ids['Mountain'][0]}/metadata"
    ro = stocked.post(f"{V1}/me/tokens", json={"name": "ro", "scopes": ["read"]}).json()["token"]
    headers = {"Idempotency-Key": "meta-1"}
    first = stocked.put(url, json={"data": {"n": 1}}, headers=headers)
    again = stocked.put(url, json={"data": {"n": 1}}, headers=headers)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    stocked.cookies.clear()
    only = {"Authorization": f"Bearer {ro}"}
    assert stocked.get(url, headers=only).json()["namespaces"] == {"user": {"n": 1}}
    assert stocked.put(url, json={"data": {"n": 2}}, headers=only).status_code == 403
    assert stocked.delete(url, headers=only).status_code == 403


# -- the MCP tools --------------------------------------------------------------------------------------------------------

def test_the_metadata_tools_are_listed_classified_and_isolated(stocked, bot):
    names = {"get_card_metadata", "set_card_metadata", "get_bucket_metadata", "set_bucket_metadata"}
    assert names <= set(mcp.BY_NAME) and names <= mcp.OWN_DATA_ONLY
    write = make_token(stocked, scopes=["read", "write"], name="Claude helper")
    tools = {t["name"]: t for t in rpc(bot, "tools/list", token=write).json()["result"]["tools"]}
    assert tools["get_card_metadata"]["annotations"]["readOnlyHint"] is True
    assert tools["set_card_metadata"]["annotations"]["readOnlyHint"] is False
    ids = {}
    for c in call_tool(bot, write, "search_cards")["structuredContent"]["items"]:
        ids.setdefault(c["name"], []).append(c["id"])
    stocked.put(f"{CARDS}/{ids['Mountain'][0]}/metadata", json={"data": {"note": "mine"}})
    done = call_tool(bot, write, "set_card_metadata", card_id=ids["Mountain"][0], data={"roles": ["land"]})["structuredContent"]
    assert done["you_write"] == "ai.token-claude-helper" and done["namespaces"]["user"] == {"note": "mine"}  # the person's is untouched
    seen = call_tool(bot, write, "get_card_metadata", card_id=ids["Mountain"][0])["structuredContent"]
    assert seen["namespaces"]["ai.token-claude-helper"] == {"roles": ["land"]} and seen["written"]["ai.token-claude-helper"]["by"]
    assert call_tool(bot, write, "set_card_metadata", card_id=ids["Mountain"][0], data={"text": "x" * 9000}).get("isError")
    bucket = stocked.post(BUCKETS, json={"name": "Deck box 2"}).json()
    set_ = call_tool(bot, write, "set_bucket_metadata", bucket_id=bucket["id"], data={"for": "elves"})["structuredContent"]
    assert set_["namespaces"] == {"ai.token-claude-helper": {"for": "elves"}}
    assert call_tool(bot, write, "get_bucket_metadata", bucket_id=bucket["id"])["structuredContent"]["namespaces"] == set_["namespaces"]
    read = make_token(stocked)
    assert call_tool(bot, read, "set_card_metadata", card_id=ids["Mountain"][0], data={"a": 1}).get("isError")
    assert call_tool(bot, read, "get_card_metadata", card_id=ids["Mountain"][0])["structuredContent"]["version"] == 1
