"""Recoverable deletes through a connected app (OAuth), the way claude.ai or ChatGPT reach the Vault (vault-wxy.18, PR 1: decks).

The sequence the security review of PR 484 described: the person presses Allow on a consent page that has Write ticked, the app sends
``delete_deck`` with ``confirm: true`` (nothing on the server proves the person said yes), and then the person, or the app itself, brings
the deck back. An OAuth token has no ``account`` scope and works only on ``/api/mcp``, so it cannot remove the way back either.
"""

from test_mcp_oauth import V1, app, client, make_client, settings, universe  # noqa: F401 - fixtures

RECOVERY = f"{V1}/recovery"


def structured(res: dict) -> dict:
    assert not res["result"]["isError"], res
    return res["result"]["structuredContent"]


def test_a_connected_app_that_deletes_with_confirm_true_leaves_the_deck_restorable_by_the_person_and_by_the_app(app, client):
    client.sign_in()
    client.connect(write=True, scope="read write")  # a plain Allow on the page as shown, Write ticked
    saved = structured(client.tool("save_deck", name="Ramp", text="1 Sol Ring\n1 Arcane Signet"))
    deck_id = saved["id"]

    gone = structured(client.tool("delete_deck", deck_id=deck_id, confirm=True))
    assert gone["deleted"] is True
    assert client.browser.get(f"{V1}/decks/{deck_id}").status_code == 404

    row = client.browser.get(RECOVERY).json()["items"][0]  # the person's own screen
    assert row["name"] == "Ramp" and row["deleted_by"]["who"] == "assistant" and row["deleted_by"]["app"]
    back = client.browser.post(f"{RECOVERY}/{row['id']}/restore")
    assert back.status_code == 200 and client.browser.get(f"{V1}/decks/{deck_id}").json()["name"] == "Ramp"

    again = structured(client.tool("delete_deck", deck_id=deck_id, confirm=True))  # and by tool, the app undoing its own deletion
    listing = structured(client.tool("list_recently_deleted"))
    assert [i["id"] for i in listing["items"]] == [again["recovery_id"]]
    assert structured(client.tool("restore_deleted", item_id=again["recovery_id"]))["restored"] is True
    assert client.browser.get(f"{V1}/decks/{deck_id}").status_code == 200


def test_a_connected_apps_token_cannot_remove_the_way_back_and_a_read_only_app_cannot_restore(app, make_client):
    first = make_client()
    first.sign_in()
    first.connect(write=True, scope="read write")
    deck_id = structured(first.tool("save_deck", name="Brew", text="1 Sol Ring"))["id"]
    item = structured(first.tool("delete_deck", deck_id=deck_id, confirm=True))["recovery_id"]

    bearer = {"Authorization": f"Bearer {first.tokens['access_token']}"}
    assert first.api.delete(f"{RECOVERY}/{item}", headers=bearer).status_code == 401  # an OAuth token works on /api/mcp only
    assert first.api.post(f"{RECOVERY}/{item}/restore", headers=bearer).status_code == 401
    assert first.browser.get(RECOVERY).json()["total"] == 1

    other = make_client()  # a different app the person connected read-only
    other.sign_in()
    other.connect(write=False, scope="read")
    assert len(structured(other.tool("list_recently_deleted"))["items"]) == 1
    refused = other.tool("restore_deleted", item_id=item)
    assert "error" in refused or refused["result"]["isError"]
    assert first.browser.get(RECOVERY).json()["total"] == 1

    assert first.browser.delete(f"{RECOVERY}/{item}").status_code == 200  # the person, in the web app, can
    assert first.browser.get(RECOVERY).json()["total"] == 0
