"""A re-import is a three-way update (vault/merge.py, #194, docs/owned-cards-updates.md "The rules of a re-import"):
the last imported file (base), the new file (theirs) and the collection now with the edits made through an assistant
(ours). Only what changed in the person's app is applied; the Vault's edits stay; a card changed on both sides is a
conflict the preview lists and that keeps the Vault's edit unless the person answers otherwise.

First the rule table, card by card, on the pure logic; then the whole thing through the API and the MCP tools, the
way a person's assistant drives it (twins/mcp_client.py): preview, answers, apply, history, undo, tenancy, speed."""

import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mtg_toolkits import delta
from mtg_toolkits.models import CollectionEntry, Finish
from sqlalchemy import select

from test_agents import V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from test_owned_changes import ADD_CMR, cards, confirm, preview, write  # noqa: F401 - fixtures and helpers
from vault import catalog_sync as cs
from vault import merge
from vault.models import Card, CollectionBaseline, Entry, Import, User

CSV = (Path(__file__).parent / "fixtures" / "collection.csv").read_bytes()
HEADER = ['"sep=,"', "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,Printing,Language,"
                     "Price Bought,Date Bought,LOW,MID,MARKET"]


# -- the rule table, card by card ----------------------------------------------------------------------------------

def x(quantity, folder=None, number="1", name="X", price=None, prices=None):
    return CollectionEntry(name=name, quantity=quantity, set_code="abc", collector_number=number, folder=folder,
                           purchase_price=price, source_prices=prices or {})


def plan_for(base, theirs, ours, **kw):
    snap = lambda entries: merge.snapshot(entries)  # noqa: E731
    return merge.make_plan(None if base is None else snap(base), snap(theirs), snap(ours), entries_exist=True, **kw)


def only(plan, group):
    assert len(group) == 1, group
    return group[0]


def test_rule_unchanged_in_the_app_and_untouched_in_the_vault_takes_the_file():
    plan = plan_for([x(2)], [x(2)], [x(2)])
    assert plan.mode == "merge" and not plan.keep and plan.take and not plan.kept_edits and not plan.conflicts


def test_rule_unchanged_in_the_app_and_edited_in_the_vault_keeps_the_edit():
    plan = plan_for([x(2)], [x(2)], [x(1)])
    assert plan.keep and not plan.take and not plan.conflicts
    edit = only(plan, plan.kept_edits)
    assert (edit["last_import_copies"], edit["vault_copies"], edit["card"]) == (2, 1, "X")


def test_rule_changed_in_the_app_and_untouched_in_the_vault_takes_the_app_change():
    plan = plan_for([x(2)], [x(3)], [x(2)])
    assert plan.take and not plan.keep and not plan.kept_edits and not plan.conflicts
    assert plan.app_changes["increased"] == 1 and plan.app_changes["copies_in"] == 1
    assert plan_for([x(2)], [], [x(2)]).app_changes["removed"] == 1  # sold in the app: gone here too


def test_rule_changed_in_the_app_to_what_the_vault_already_says_is_no_conflict():
    plan = plan_for([x(2)], [x(1)], [x(1)])
    assert plan.take and not plan.conflicts and plan.same_change == 1


def test_rule_changed_in_both_differently_is_a_conflict_that_keeps_the_vault_edit_by_default():
    plan = plan_for([x(2)], [x(3)], [x(1)])
    conflict = only(plan, plan.conflicts)
    assert conflict["kind"] == "changed_in_both" and conflict["keeps"] == "vault" and conflict["default"] == "vault"
    assert (conflict["last_import_copies"], conflict["app_copies"], conflict["vault_copies"]) == (2, 3, 1)
    assert plan.keep and not plan.take
    # the person's answers: for one card, or for all
    assert plan_for([x(2)], [x(3)], [x(1)], use_app=frozenset({conflict["id"]})).take
    assert plan_for([x(2)], [x(3)], [x(1)], default="app").take
    assert plan_for([x(2)], [x(3)], [x(1)], default="vault").keep


def test_rule_removed_in_the_app_but_edited_in_the_vault_says_so():
    plan = plan_for([x(2)], [], [x(1)])
    conflict = only(plan, plan.conflicts)
    assert conflict["kind"] == "removed_in_app" and conflict["keeps"] == "vault" and plan.keep
    assert "no longer has this card" in merge.describe(plan, None)["conflicts"]["cards"][0]["question"]
    assert plan_for([x(2)], [], [x(1)], default="app").take  # the person may take the app's removal instead


def test_rule_added_in_both_with_different_copies_and_removed_in_the_vault_and_changed_in_the_app():
    assert only(plan_for([], [x(2)], [x(1)]), plan_for([], [x(2)], [x(1)]).conflicts)["kind"] == "added_in_both"
    assert only(plan_for([x(2)], [x(3)], []), plan_for([x(2)], [x(3)], []).conflicts)["kind"] == "removed_in_vault"


def test_rule_a_card_added_only_in_the_vault_stays_while_the_file_omits_it():
    plan = plan_for([x(2)], [x(2)], [x(2), x(1, number="2")])  # the second card is in no file
    assert plan.keep == {merge.key_string(x(1, number="2"))} and only(plan, plan.kept_edits)["vault_copies"] == 1


def test_a_change_in_the_apps_folder_or_price_paid_is_the_apps_change():
    assert plan_for([x(1, folder="A")], [x(1, folder="B")], [x(1, folder="A")]).app_changes["changed"] == 1
    assert plan_for([x(1, price=1.0)], [x(1, price=2.0)], [x(1, price=1.0)]).take
    # and Dragon Shield's prices, which differ in every export, are not a change
    plan = plan_for([x(1, prices={"market": 1.0})], [x(1, prices={"market": 9.0})], [x(1, prices={"market": 1.0})])
    assert plan.app_changes["changed"] == 0 and not plan.app_changes["increased"]


def test_without_a_base_the_file_replaces_the_collection_as_it_always_did():
    plan = plan_for(None, [x(3)], [x(1)])
    assert plan.mode == "no_baseline" and plan.take and not plan.keep and not plan.conflicts
    assert "no record of an earlier file" in merge.describe(plan, None)["how"]


def test_an_emptied_collection_takes_the_file_whatever_the_base_says():
    plan = merge.make_plan(merge.snapshot([x(2)]), merge.snapshot([x(2)]), {}, entries_exist=False)
    assert plan.mode == "first" and plan.take and not plan.keep and not plan.kept_edits


def test_replace_everything_discards_vault_edits_and_the_preview_counts_them():
    plan = plan_for([x(2), x(2, number="2")], [x(2), x(5, number="2")], [x(1), x(2, number="2")], replace_everything=True)
    assert plan.mode == "replace" and not plan.keep and plan.discards == 1
    assert plan_for([x(2)], [x(2)], [x(1)]).discards == 1  # the merge says what replacing would lose, too


def test_the_history_summary_is_what_delta_computes(app):
    import random
    rnd = random.Random(7)
    def collection():
        return [x(rnd.randint(0, 4), number=str(n)) for n in range(40) if rnd.random() < 0.8]
    for _ in range(20):
        old, new = collection(), collection()
        ours = {k: s["q"] for k, s in merge.snapshot(old).items()}
        result = {k: s["q"] for k, s in merge.snapshot(new).items()}
        assert merge.summary(ours, result) == delta.diff(old, new).summary()


# -- through the API ------------------------------------------------------------------------------------------------

def line(name, set_code, set_name, number, qty=1, printing="Normal", market="0.10", folder="my cards", price="0.10"):
    return (f"{folder},{qty},0,{name},{set_code},{set_name},{number},Mint,{printing},English,{price},2024-01-01,"
            f"0.01,0.20,{market}")


KILLER = ("A Killer Among Us", "MKM", "Murders at Karlov Manor", "167")
SOL = ("Sol Ring", "C21", "Commander 2021", "263")
BELFRY = ("Belfry Spirit", "GK2", "Guild Kit: Orzhov", "29")
MARAUDER = ("Accursed Marauder", "MH3", "Modern Horizons 3", "512")
CAVERN = ("Cavern of Souls", "LCI", "The Lost Caverns of Ixalan", "269")


def file(*rows) -> bytes:
    return ("\r\n".join([*HEADER, *rows]) + "\r\n").encode()


def base_file(market="0.10") -> bytes:
    return file(line(*KILLER, qty=4, market=market), line(*SOL, qty=1, printing="Foil", market=market),
                line(*BELFRY, qty=1, market=market), line(*MARAUDER, qty=1, printing="", market=market))


def upload(client, content, **params):
    return client.post(f"{V1}/imports", files={"file": ("export.csv", content, "text/csv")}, params=params)


def preview_file(client, content, **params):
    res = client.post(f"{V1}/imports/preview", files={"file": ("export.csv", content, "text/csv")}, params=params)
    assert res.status_code == 200, res.text
    return res.json()


def rows_of(app, email="dev@localhost"):
    """The collection as stored, in order: what an export would write."""
    with app.state.db.sessions() as db:
        user = db.scalar(select(User).where(User.email == email))
        return [(r.name, (r.set_code or "").lower(), r.collector_number, r.finish, r.quantity, r.folder, r.condition)
                for r in db.scalars(select(Entry).where(Entry.user_id == user.id).order_by(Entry.position, Entry.id))]


def totals(app, email="dev@localhost"):
    """Copies per printing (undo restores these exactly; it may put them back in a plain row)."""
    out = {}
    for name, set_code, number, finish, quantity, *_ in rows_of(app, email):
        out[(name, set_code, number, finish)] = out.get((name, set_code, number, finish), 0) + quantity
    return out


def copies_of(app, name, set_code=None):
    return sum(r[4] for r in rows_of(app) if r[0] == name and (set_code is None or r[1] == set_code))


def sell(bot, token, name, set_code, number, quantity=1, finish=None):
    """An assistant edit the way the person asked for it: preview, then confirm."""
    lines = [{"action": "remove", "name": name, "quantity": quantity, "set": set_code, "number": number,
              **({"finish": finish} if finish else {})}]
    seen = preview(bot, token, *lines)
    assert seen["ready"], seen
    done = confirm(bot, token, lines, seen["confirmation"])
    assert not done.get("isError"), done


def buy(bot, token, name, set_code, number, quantity=1):
    lines = [{"action": "add", "name": name, "quantity": quantity, "set": set_code, "number": number}]
    seen = preview(bot, token, *lines)
    assert seen["ready"], seen
    done = confirm(bot, token, lines, seen["confirmation"])
    assert not done.get("isError"), done


@pytest.fixture
def person(signed_in, cards):  # noqa: F811
    """A signed-in person (dev@localhost) with the catalog the assistant resolves cards in, and no collection yet."""
    return signed_in


@pytest.fixture
def assistant(person, bot):
    return bot, make_token(person, scopes=["read", "write"])


def test_with_no_edits_a_re_import_is_what_it_was_before(app, person):
    first = upload(person, base_file())
    assert first.status_code == 201 and first.json()["merge"]["mode"] == "first"
    changed = file(line(*KILLER, qty=4), line(*SOL, qty=2, printing="Foil"), line(*CAVERN, qty=1))
    res = upload(person, changed).json()
    assert res["merge"]["mode"] == "merge" and res["merge"]["kept_vault_edits"]["count"] == 0
    assert res["merge"]["conflicts"]["count"] == 0
    from vault import importer  # the summary is what delta computes between the two files, as it always was
    before = importer.read_file(base_file())[1]
    after = importer.read_file(changed)[1]
    assert res["changes"] == delta.diff(before, after).summary()
    assert person.get(f"{V1}/collection/export.csv").content == changed  # byte for byte, as imports always did


def test_vault_edits_stay_and_only_the_apps_changes_are_applied(app, person, assistant):
    bot, token = assistant
    upload(person, base_file())
    buy(bot, token, "Sol Ring", "CMR", "472", 2)  # in no file
    sell(bot, token, "A Killer Among Us", "MKM", "167", 1)  # 4 -> 3, the app still says 4
    new_file = file(line(*KILLER, qty=4), line(*SOL, qty=1, printing="Foil"), line(*BELFRY, qty=3), line(*CAVERN, qty=1))
    seen = preview_file(person, new_file)
    assert seen["merge"]["mode"] == "merge"
    assert seen["merge"]["from_your_app"] == {"added": 1, "removed": 1, "increased": 1, "decreased": 0, "changed": 0,
                                              "copies_in": 3, "copies_out": 1}
    kept = {c["card"] + c["set"].lower(): c for c in seen["merge"]["kept_vault_edits"]["cards"]}
    assert seen["merge"]["kept_vault_edits"]["count"] == 2
    assert (kept["Sol Ringcmr"]["last_import_copies"], kept["Sol Ringcmr"]["vault_copies"]) == (0, 2)
    assert (kept["A Killer Among Usmkm"]["last_import_copies"], kept["A Killer Among Usmkm"]["vault_copies"]) == (4, 3)
    assert seen["merge"]["conflicts"]["count"] == 0
    assert copies_of(app, "Belfry Spirit") == 1  # a preview changes nothing
    done = upload(person, new_file)
    assert done.status_code == 201 and done.json()["kind"] == "import"
    assert done.json()["changes"] == seen["changes"]  # what the preview said is what happened
    assert copies_of(app, "A Killer Among Us") == 3 and copies_of(app, "Sol Ring", "cmr") == 2  # the edits stay
    assert copies_of(app, "Belfry Spirit") == 3 and copies_of(app, "Cavern of Souls") == 1  # the app's changes come in
    assert copies_of(app, "Accursed Marauder") == 0  # sold in the app
    assert copies_of(app, "Sol Ring", "c21") == 1


def test_the_import_history_says_what_was_kept_and_what_was_asked(app, person, assistant):
    bot, token = assistant
    upload(person, base_file())
    buy(bot, token, "Sol Ring", "CMR", "472", 2)
    sell(bot, token, "A Killer Among Us", "MKM", "167", 1)
    upload(person, file(line(*SOL, qty=1, printing="Foil"), line(*BELFRY, qty=1), line(*MARAUDER, qty=1, printing="")))
    latest = person.get(f"{V1}/imports").json()["items"][0]
    assert latest["kind"] == "import" and latest["filename"] == "export.csv"
    merged = latest["merge"]
    assert merged["mode"] == "merge" and merged["kept_vault_edits"]["count"] == 1  # the Sol Ring; the Killer is a conflict
    assert merged["conflicts"]["count"] == 1 and merged["conflicts"]["cards"][0]["kind"] == "removed_in_app"
    assert merged["conflicts"]["cards"][0]["keeps"] == "vault" and merged["baseline"]["import_id"]
    assert person.get(latest["_links"]["self"]["href"]).json()["merge"] == merged
    assert [i["kind"] for i in person.get(f"{V1}/imports").json()["items"]] == ["import", "assistant", "assistant", "import"]


def two_sided(person, bot, token):
    """Sol Ring (foil) sold through the assistant while the app now says 2; the Killer cut to 3 here while the app dropped it."""
    upload(person, base_file())
    sell(bot, token, "Sol Ring", "C21", "263", 1, finish="foil")
    sell(bot, token, "A Killer Among Us", "MKM", "167", 1)
    return file(line(*SOL, qty=2, printing="Foil"), line(*BELFRY, qty=1), line(*MARAUDER, qty=1, printing=""))


def test_a_card_changed_on_both_sides_is_listed_and_keeps_the_vault_edit_unless_answered(app, person, assistant):
    bot, token = assistant
    changed = two_sided(person, bot, token)
    seen = preview_file(person, changed)
    conflicts = {c["card"]: c for c in seen["merge"]["conflicts"]["cards"]}
    assert seen["merge"]["conflicts"]["count"] == 2 and seen["merge"]["conflicts"]["default"] == "vault"
    assert conflicts["Sol Ring"]["kind"] == "removed_in_vault" and conflicts["Sol Ring"]["keeps"] == "vault"
    assert (conflicts["Sol Ring"]["last_import_copies"], conflicts["Sol Ring"]["app_copies"],
            conflicts["Sol Ring"]["vault_copies"]) == (1, 2, 0)
    assert conflicts["A Killer Among Us"]["kind"] == "removed_in_app"  # the app dropped it, the assistant edited it: said so
    assert (conflicts["A Killer Among Us"]["app_copies"], conflicts["A Killer Among Us"]["vault_copies"]) == (0, 3)
    assert all(c["question"] for c in conflicts.values())
    # the default answer: the Vault's edits stay
    upload(person, changed)
    assert copies_of(app, "Sol Ring", "c21") == 0 and copies_of(app, "A Killer Among Us") == 3


def test_the_person_can_answer_one_conflict_or_all_of_them_and_replace_everything_is_still_there(app, person, assistant):
    bot, token = assistant
    changed = two_sided(person, bot, token)
    ids = {c["card"]: c["id"] for c in preview_file(person, changed)["merge"]["conflicts"]["cards"]}
    # answers show in the preview before they are applied
    one = preview_file(person, changed, use_app_value=[ids["Sol Ring"]])["merge"]["conflicts"]["cards"]
    assert {c["card"]: c["keeps"] for c in one} == {"Sol Ring": "app", "A Killer Among Us": "vault"}
    every = preview_file(person, changed, conflicts="app")["merge"]["conflicts"]["cards"]
    assert {c["keeps"] for c in every} == {"app"}
    assert person.post(f"{V1}/imports/preview", files={"file": ("e.csv", changed, "text/csv")},
                       params={"conflicts": "nope"}).status_code == 422
    # one answer applied: the app's value for the Sol Ring, the Vault's for the Killer
    assert upload(person, changed, use_app_value=[ids["Sol Ring"]]).status_code == 201
    assert copies_of(app, "Sol Ring", "c21") == 2 and copies_of(app, "A Killer Among Us") == 3


def test_answering_app_for_every_conflict_takes_the_apps_value_for_all(app, person, assistant):
    bot, token = assistant
    changed = two_sided(person, bot, token)
    assert upload(person, changed, conflicts="app").status_code == 201
    assert copies_of(app, "Sol Ring", "c21") == 2 and copies_of(app, "A Killer Among Us") == 0  # the app dropped it
    assert copies_of(app, "Belfry Spirit") == 1


def test_replace_everything_replaces_everything_and_says_what_it_discards(app, person, assistant):
    bot, token = assistant
    changed = two_sided(person, bot, token)
    buy(bot, token, "Sol Ring", "CMR", "472", 2)
    seen = preview_file(person, changed, replace_everything=True)
    assert seen["merge"]["mode"] == "replace" and seen["merge"]["replace_everything_discards_vault_edits"] == 3
    assert not seen["merge"]["kept_vault_edits"]["count"] and not seen["merge"]["conflicts"]["count"]
    assert "discarded" in seen["merge"]["how"]
    assert preview_file(person, changed)["merge"]["replace_everything_discards_vault_edits"] == 3  # the merge says it too
    assert upload(person, changed, replace_everything=True).status_code == 201
    assert person.get(f"{V1}/collection/export.csv").content == changed  # exactly the file
    assert copies_of(app, "Sol Ring", "cmr") == 0


def test_a_card_added_only_in_the_vault_survives_a_changed_file_that_still_omits_it_and_the_same_file_again(app, person, assistant):
    """The required test of docs/collections.md (decision 6): import A, add X in the Vault, import a changed file that
    still omits X (X kept), import the same file again (X still kept)."""
    bot, token = assistant
    upload(person, base_file())
    buy(bot, token, "Sol Ring", "CMR", "472", 1)  # X
    b = file(line(*KILLER, qty=4), line(*SOL, qty=1, printing="Foil"), line(*BELFRY, qty=2), line(*MARAUDER, qty=1, printing=""))
    assert upload(person, b).status_code == 201 and copies_of(app, "Sol Ring", "cmr") == 1
    assert copies_of(app, "Belfry Spirit") == 2
    again = upload(person, b)
    assert again.status_code == 201 and copies_of(app, "Sol Ring", "cmr") == 1 and copies_of(app, "Belfry Spirit") == 2


def test_importing_the_same_file_twice_changes_nothing(app, person, assistant):
    bot, token = assistant
    upload(person, base_file())
    buy(bot, token, "Sol Ring", "CMR", "472", 2)
    sell(bot, token, "A Killer Among Us", "MKM", "167", 1)
    f1 = file(line(*KILLER, qty=4), line(*SOL, qty=1, printing="Foil"), line(*BELFRY, qty=3), line(*CAVERN, qty=1))
    upload(person, f1)
    once = rows_of(app)
    twice = upload(person, f1).json()
    assert rows_of(app) == once
    assert {k: v for k, v in twice["changes"].items() if k != "unchanged"} == {
        "added": 0, "removed": 0, "increased": 0, "decreased": 0, "copies_in": 0, "copies_out": 0}
    assert twice["merge"]["from_your_app"] == {"added": 0, "removed": 0, "increased": 0, "decreased": 0, "changed": 0,
                                              "copies_in": 0, "copies_out": 0}
    assert twice["merge"]["kept_vault_edits"]["count"] == 2 and twice["merge"]["conflicts"]["count"] == 0
    # and with no edits at all, the same file twice leaves the same rows, in the same order
    person.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"})
    person.post("/api/auth/dev-login", params={"email": "plain@example.com"})
    upload(person, base_file())
    plain = rows_of(app, "plain@example.com")
    upload(person, base_file())
    assert rows_of(app, "plain@example.com") == plain
    assert person.get(f"{V1}/collection/export.csv").content == base_file()


def test_a_dragon_shield_re_export_with_new_prices_is_not_a_change(app, person, assistant):
    """Every export carries that day's LOW/MID/MARKET: only a card's copies, folder, condition and what was paid count."""
    bot, token = assistant
    upload(person, base_file(market="0.10"))
    sell(bot, token, "A Killer Among Us", "MKM", "167", 1)
    seen = preview_file(person, base_file(market="9.99"))["merge"]
    assert seen["from_your_app"]["changed"] == 0 and seen["conflicts"]["count"] == 0
    assert seen["kept_vault_edits"]["count"] == 1
    upload(person, base_file(market="9.99"))
    assert copies_of(app, "A Killer Among Us") == 3
    with app.state.db.sessions() as db:  # the untouched rows took the new prices from the file
        sol = db.scalar(select(Entry).where(Entry.name == "Sol Ring"))
        assert sol.source_prices["market"] == 9.99


def test_a_collection_imported_before_this_has_no_base_so_the_file_replaces_it_and_says_so(app, person):
    upload(person, base_file())
    with app.state.db.sessions() as db:
        db.query(CollectionBaseline).delete()
        db.commit()
    seen = preview_file(person, file(line(*SOL, qty=5, printing="Foil")))["merge"]
    assert seen["mode"] == "no_baseline" and "no record of an earlier file" in seen["how"]
    upload(person, file(line(*SOL, qty=5, printing="Foil")))
    assert [(r[0], r[4]) for r in rows_of(app)] == [("Sol Ring", 5)]
    assert preview_file(person, file(line(*SOL, qty=5, printing="Foil")))["merge"]["mode"] == "merge"  # the record started


# -- undo and history -----------------------------------------------------------------------------------------------

def test_an_edit_cannot_be_undone_after_an_import_and_stays_while_the_import_keeps_it(app, person, assistant):
    bot, token = assistant
    upload(person, base_file())
    buy(bot, token, "Sol Ring", "CMR", "472", 2)
    upload(person, file(line(*KILLER, qty=4), line(*SOL, qty=1, printing="Foil"), line(*BELFRY, qty=2),
                        line(*MARAUDER, qty=1, printing="")))
    undone = call_tool(bot, token, "undo_owned_cards_update")
    assert undone.get("isError") or "Nothing to undo" in json.dumps(undone)  # the collection changed since: refused
    assert copies_of(app, "Sol Ring", "cmr") == 2


def test_an_edit_made_after_a_merged_import_is_undone_exactly_and_the_next_import_still_keeps_the_first(app, person, assistant):
    bot, token = assistant
    upload(person, base_file())
    buy(bot, token, "Sol Ring", "CMR", "472", 2)
    new_file = file(line(*KILLER, qty=4), line(*SOL, qty=1, printing="Foil"), line(*BELFRY, qty=2), line(*MARAUDER, qty=1, printing=""))
    upload(person, new_file)
    before = totals(app)
    sell(bot, token, "A Killer Among Us", "MKM", "167", 2)
    assert copies_of(app, "A Killer Among Us") == 2
    shown = call_tool(bot, token, "undo_owned_cards_update")["structuredContent"]
    assert shown["ready"] and shown["undoes"]
    done = call_tool(bot, token, "undo_owned_cards_update", confirmation=shown["confirmation"])
    assert not done.get("isError"), done
    assert totals(app) == before  # exactly the copies it had after the merged import
    assert person.get(f"{V1}/imports").json()["items"][0]["kind"] == "undo"
    # the first edit is still there after another import of the same file, and the undone one did not leave a trace
    upload(person, new_file)
    assert totals(app) == before and copies_of(app, "Sol Ring", "cmr") == 2


# -- tenancy ---------------------------------------------------------------------------------------------------------

def test_one_persons_edits_and_base_never_reach_anothers_import(app, person, assistant):
    bot, token = assistant
    upload(person, base_file())
    buy(bot, token, "Sol Ring", "CMR", "472", 2)
    with TestClient(app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        res = upload(bob, base_file())
        assert res.json()["merge"]["mode"] == "first" and res.json()["merge"]["kept_vault_edits"]["count"] == 0
        assert [r[0] for r in rows_of(app, "bob@example.com")] == ["A Killer Among Us", "Sol Ring", "Belfry Spirit", "Accursed Marauder"]
        # Bob's preview is about Bob's collection only
        seen = preview_file(bob, file(line(*SOL, qty=1, printing="Foil")))["merge"]
        assert seen["kept_vault_edits"]["count"] == 0 and seen["from_your_app"]["removed"] == 3
        # and Alice's import page entries are not reachable by id
        alice_import = person.get(f"{V1}/imports").json()["items"][0]["id"]
        assert bob.get(f"{V1}/imports/{alice_import}").status_code == 404
    assert copies_of(app, "Sol Ring", "cmr") == 2  # Bob's imports did not touch Alice's collection
    with app.state.db.sessions() as db:
        assert {b.user_id for b in db.scalars(select(CollectionBaseline))} == {u.id for u in db.scalars(select(User))}
        assert db.query(CollectionBaseline).count() == 2


def test_the_base_is_exported_and_erased_with_the_account(app, person):
    upload(person, base_file())
    z = zipfile.ZipFile(io.BytesIO(person.get(f"{V1}/me/export").content))
    last = json.loads(z.read("last_import_cards.json"))
    assert {c["name"] for c in last["cards"]} == {"A Killer Among Us", "Sol Ring", "Belfry Spirit", "Accursed Marauder"}
    assert json.loads(z.read("imports.json"))[0]["merge"]["mode"] == "first"
    res = person.request("DELETE", f"{V1}/me", json={"confirm": "DELETE"}).json()
    assert res["removed"]["collection_baselines"] == 1
    with app.state.db.sessions() as db:
        assert db.query(CollectionBaseline).count() == 0


def test_the_baseline_is_the_files_cards_and_vault_edits_never_change_it(app, person, assistant):
    bot, token = assistant
    upload(person, base_file())
    with app.state.db.sessions() as db:
        before = json.dumps(db.query(CollectionBaseline).one().cards, sort_keys=True)
    buy(bot, token, "Sol Ring", "CMR", "472", 2)
    sell(bot, token, "A Killer Among Us", "MKM", "167", 1)
    with app.state.db.sessions() as db:
        assert json.dumps(db.query(CollectionBaseline).one().cards, sort_keys=True) == before


# -- speed ----------------------------------------------------------------------------------------------------------

def big_file(n=10_000, bump=()):
    rows = [line(f"Card {i}", f"S{i % 300}", f"Set {i % 300}", str(i), qty=3 if i in bump else 1, market="0.5") for i in range(n)]
    return file(*rows)


def test_a_re_import_of_ten_thousand_rows_with_edits_stays_fast(app, person):
    t0 = time.perf_counter()
    assert upload(person, big_file()).status_code == 201
    first = time.perf_counter() - t0
    with app.state.db.sessions() as db:  # 400 cards edited in the Vault since (as many change sets would)
        user = db.scalar(select(User))
        for row in db.scalars(select(Entry).where(Entry.user_id == user.id).limit(400)):
            row.quantity += 2
        user.collection_version += 1
        db.commit()
    newer = big_file(bump=set(range(5000, 5500)))  # 500 cards changed in the app
    t0 = time.perf_counter()
    seen = preview_file(person, newer)
    previewed = time.perf_counter() - t0
    t0 = time.perf_counter()
    res = upload(person, newer)
    merged = time.perf_counter() - t0
    assert res.status_code == 201 and res.json()["merge"]["kept_vault_edits"]["count"] == 400
    assert seen["merge"]["from_your_app"]["increased"] == 500
    assert copies_of(app, "Card 0") == 3 and copies_of(app, "Card 5000") == 3 and copies_of(app, "Card 9000") == 1
    assert previewed < 12 and merged < 20 and first < 20, (first, previewed, merged)
    t0 = time.perf_counter()
    upload(person, newer)  # the same file again
    again = time.perf_counter() - t0
    # Relative to this run (#288): a slow runner passes, a repeat that is slower than the merge that applied 900 changes fails. The same
    # file again has nothing to apply, so it must not cost more than that merge plus noise; the absolute ceilings above still catch a
    # slowdown of everything together.
    assert again < max(merged * 1.5, 3) and again < 20, (again, merged)


# -- the migration of collections imported before this ---------------------------------------------------------------

def test_the_migration_rebuilds_a_base_so_the_first_re_import_after_the_upgrade_keeps_assistant_edits(database_url):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    import vault.db
    from vault.db import Database
    from vault.db import normalise_url

    config = Config()
    config.set_main_option("script_location", str(Path(vault.db.__file__).parent / "migrations"))
    engine = create_engine(normalise_url(database_url))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    with engine.begin() as conn:  # the schema as it was before the base existed
        config.attributes["connection"] = conn
        command.upgrade(config, "0111")
    changes = {"lines": [{"card": "Sol Ring", "set": "cmr", "number": "472", "finish": "nonfoil", "before": 0, "after": 2},
                         {"card": "A Killer Among Us", "set": "mkm", "number": "167", "finish": "nonfoil", "before": 4, "after": 3}]}
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, name, email, created_at, collection_version) VALUES "
                          "(1, 'A', 'a@example.com', CURRENT_TIMESTAMP, 3), (2, 'B', 'b@example.com', CURRENT_TIMESTAMP, 1)"))
        conn.execute(text("INSERT INTO imports (id, user_id, filename, source, rows, copies, summary, created_at, kind) VALUES "
                          "(1, 1, 'f.csv', 'dragonshield', 2, 5, '{}', CURRENT_TIMESTAMP, 'import'), "
                          "(2, 1, 'Changes', 'assistant', 2, 5, '{}', CURRENT_TIMESTAMP, 'assistant'), "
                          "(3, 2, 'g.csv', 'dragonshield', 1, 1, '{}', CURRENT_TIMESTAMP, 'import')"))
        conn.execute(text("UPDATE imports SET changes = CAST(:c AS json), app = 'x' WHERE id = 2"), {"c": json.dumps(changes)})
        for uid, imp, name, qty, set_code, number, finish in (
                (1, 1, "A Killer Among Us", 3, "MKM", "167", "nonfoil"), (1, 2, "Sol Ring", 2, "cmr", "472", "nonfoil"),
                (1, 1, "Belfry Spirit", 1, "GK2", "29", "nonfoil"), (2, 3, "Sol Ring", 1, "c21", "263", "foil")):
            conn.execute(text("INSERT INTO entries (user_id, import_id, position, name, set_code, collector_number, finish, condition, "
                              "language, quantity, trade_quantity, source_prices, extra) VALUES (:u, :i, 0, :n, :s, :c, :f, "
                              "'near_mint', 'en', :q, 0, '{}', '{}')"), {"u": uid, "i": imp, "n": name, "s": set_code, "c": number,
                                                                          "f": finish, "q": qty})
    engine.dispose()
    db = Database(database_url)
    db.migrate()
    with db.sessions() as session:
        a, b = (session.get(CollectionBaseline, 1), session.get(CollectionBaseline, 2))
        assert a.import_id == 1 and b.import_id == 3
        cards = {c["n"]: c for c in a.cards}
        assert cards["Belfry Spirit"]["q"] == 1 and cards["Belfry Spirit"]["r"]  # untouched since the file: exact
        assert cards["A Killer Among Us"]["q"] == 4 and cards["A Killer Among Us"]["r"] is None  # touched: copies before
        assert cards["Sol Ring"]["q"] == 0 and cards["Sol Ring"]["r"] is None
        assert {c["n"]: c["q"] for c in b.cards} == {"Sol Ring": 1}
        # so the first re-import now keeps the assistant's edits and applies the app's change
        user = session.get(User, 1)
        from vault import importer
        seen = importer.preview_import(session, user, file(line(*KILLER, qty=4), line(*BELFRY, qty=2)))["merge"]
        assert seen["mode"] == "merge" and seen["kept_vault_edits"]["count"] == 2 and seen["from_your_app"]["increased"] == 1
    db.engine.dispose()
