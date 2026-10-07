"""The re-import as a person's assistant drives it, end to end through the MCP tools, with ChatGPT's twin
(twins/mcp_client.py) on the twin universe (#194, docs/owned-cards-updates.md): import, edit with confirmation, preview
the next import (what the app changed, which edits are kept, the conflicts), answer, apply, upload a big file through
the staged flow, replace everything, and see it all in the import history."""

import pytest
from fastapi.testclient import TestClient

from tests.test_mcp_oauth import app, settings, universe  # noqa: F401 - fixtures: the app wired to the twin universe
from test_reimport_merge import BELFRY, KILLER, SOL, base_file, copies_of, file, line
from twins.mcp_client import ChatGptClient
from vault import catalog_sync as cs
from vault.models import Card

SOL_RING = "33333333-3333-3333-3333-333333333333"
KILLER_ID = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def catalog(app):
    with app.state.db.sessions() as db:
        cs.sync_oracle_cards(db, [
            {"object": "card", "id": "p-" + oid[:4], "oracle_id": oid, "name": name, "layout": "normal", "mana_cost": "{1}",
             "cmc": 1.0, "type_line": "Artifact", "oracle_text": "", "colors": [], "color_identity": [], "keywords": [],
             "legalities": {"commander": "legal"}, "digital": False}
            for oid, name in ((SOL_RING, "Sol Ring"), (KILLER_ID, "A Killer Among Us"))])
        db.add_all([
            Card(scryfall_id="sol-c21", oracle_id=SOL_RING, name="Sol Ring", set_code="c21", set_name="Commander 2021",
                 collector_number="263", finishes=["nonfoil", "foil"]),
            Card(scryfall_id="sol-cmr", oracle_id=SOL_RING, name="Sol Ring", set_code="cmr", set_name="Commander Legends",
                 collector_number="472"),
            Card(scryfall_id="killer-mkm", oracle_id=KILLER_ID, name="A Killer Among Us", set_code="mkm",
                 set_name="Murders at Karlov Manor", collector_number="167")])
        db.commit()


@pytest.fixture
def chatgpt(app, universe, catalog):
    return ChatGptClient(lambda: TestClient(app), universe.client_hosts)


def test_the_assistant_flow_end_to_end_through_the_mcp_tools(app, chatgpt):
    tools = {t["name"]: t for t in chatgpt.add_app(write=True)}
    for name in ("import_collection_csv", "confirm_staged_upload", "get_staged_upload"):
        assert {"conflicts", "use_app_value", "replace_everything"} <= set(tools[name]["inputSchema"]["properties"]), name
    assert chatgpt.flagged() == {}, chatgpt.flagged()  # the new descriptions do not steer approval

    def call(name, **args):
        return chatgpt.tool(name, **args)["result"]

    def ok(name, **args):
        out = call(name, **args)
        assert not out.get("isError"), out
        return out["structuredContent"]

    assert ok("import_collection_csv", csv=base_file().decode(), confirm=True)["merge"]["mode"] == "first"

    # the person tells the assistant what they sold and found; each edit is previewed, then confirmed
    sell = [{"action": "remove", "name": "Sol Ring", "quantity": 1, "set": "c21", "number": "263", "finish": "foil"},
            {"action": "remove", "name": "A Killer Among Us", "quantity": 1, "set": "mkm", "number": "167"}]
    seen = ok("update_owned_cards", lines=sell)
    assert "keeps these edits" in seen["note"] and "replaces the whole collection" not in seen["note"]
    ok("confirm_owned_cards_update", lines=sell, confirmation=seen["confirmation"])
    add = [{"action": "add", "name": "Sol Ring", "quantity": 2, "set": "cmr", "number": "472"}]
    ok("confirm_owned_cards_update", lines=add, confirmation=ok("update_owned_cards", lines=add)["confirmation"])
    assert (copies_of(app, "Sol Ring", "c21"), copies_of(app, "A Killer Among Us"), copies_of(app, "Sol Ring", "cmr")) == (0, 3, 2)

    # their app's next export: the foil Sol Ring bought again (changed on both sides), Belfry up, Marauder sold
    newer = file(line(*KILLER, qty=4), line(*SOL, qty=2, printing="Foil"), line(*BELFRY, qty=2)).decode()
    shown = ok("import_collection_csv", csv=newer)  # no confirm: a preview
    merged = shown["merge"]
    assert merged["mode"] == "merge" and merged["from_your_app"]["increased"] == 2 and merged["from_your_app"]["removed"] == 1
    assert merged["kept_vault_edits"]["count"] == 2  # the Killer cut to 3, the Sol Ring (cmr) added
    conflicts = merged["conflicts"]["cards"]
    assert [c["card"] for c in conflicts] == ["Sol Ring"] and conflicts[0]["keeps"] == "vault"
    assert ok("list_imports")["total"] == 3 and copies_of(app, "Belfry Spirit") == 1  # a preview changes nothing

    # the person answers: take the app's value for that card; the rest keeps their edits
    done = ok("import_collection_csv", csv=newer, confirm=True, use_app_value=[conflicts[0]["id"]])
    assert done["merge"]["conflicts"]["cards"][0]["keeps"] == "app"
    history = ok("list_imports")["items"][0]
    assert history["kind"] == "import" and history["merge"]["kept_vault_edits"]["count"] == 2
    assert ok("get_import", import_id=history["id"])["merge"] == history["merge"]
    assert copies_of(app, "A Killer Among Us") == 3 and copies_of(app, "Sol Ring", "c21") == 2
    assert copies_of(app, "Sol Ring", "cmr") == 2 and copies_of(app, "Belfry Spirit") == 2
    assert copies_of(app, "Accursed Marauder") == 0
    assert call("undo_owned_cards_update").get("isError")  # an import came after the edits: they stay, undo is refused

    # a big file through the staged flow: the preview shows the merge and the answers, the apply keeps the edits
    started = ok("start_collection_upload")
    with TestClient(app) as browser:  # the app now has 5 Killers: that and the Vault's 3 are a conflict
        page = browser.post("/upload", data={"ticket": started["url"].split("ticket=")[1]},
                            files={"file": ("big.csv", file(line(*KILLER, qty=5), line(*BELFRY, qty=2)), "text/csv")})
        assert page.status_code == 200 and "kept" in page.text and "ask you which to keep" in page.text
    staged = ok("get_staged_upload", upload_id=started["id"])
    assert staged["merge"]["mode"] == "merge" and staged["merge"]["kept_vault_edits"]["count"] == 1  # the Sol Ring (cmr)
    (killer,) = staged["merge"]["conflicts"]["cards"]
    assert killer["card"] == "A Killer Among Us" and killer["kind"] == "changed_in_both" and killer["keeps"] == "vault"
    assert (killer["last_import_copies"], killer["app_copies"], killer["vault_copies"]) == (4, 5, 3)
    answered = ok("get_staged_upload", upload_id=started["id"], conflicts="app")["merge"]["conflicts"]["cards"]
    assert [c["keeps"] for c in answered] == ["app"]
    shown = ok("confirm_staged_upload", upload_id=started["id"], use_app_value=[killer["id"]])  # no confirm: the preview
    assert shown["merge"]["conflicts"]["cards"][0]["keeps"] == "app" and copies_of(app, "A Killer Among Us") == 3
    done = ok("confirm_staged_upload", upload_id=started["id"], confirm=True, use_app_value=[killer["id"]])
    assert done["merge"]["conflicts"]["cards"][0]["keeps"] == "app"
    assert copies_of(app, "A Killer Among Us") == 5 and copies_of(app, "Sol Ring", "cmr") == 2
    assert copies_of(app, "Sol Ring", "c21") == 0  # the app sold the one it had bought again: untouched here, so it goes

    # and replace everything makes the file the whole collection
    wiped = ok("import_collection_csv", csv=newer, confirm=True, replace_everything=True)
    assert wiped["merge"]["mode"] == "replace"
    assert (copies_of(app, "A Killer Among Us"), copies_of(app, "Sol Ring", "cmr"), copies_of(app, "Sol Ring", "c21")) == (4, 0, 2)
