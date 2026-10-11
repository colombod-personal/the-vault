"""vault-wxy.6: the one-time upload link works for a read-only connection, the default grant.

A person attached a collection file of about 7,000 cards in ChatGPT; the assistant could not offer the upload link, because
``start_collection_upload`` was a write tool: a read-only connection (the default consent) neither saw it in ``tools/list``
nor could call it (403). The link only stages a file that waits; nothing is imported until ``confirm_staged_upload`` (write
scope) is called with the preview's ``content_hash`` (vault/uploads.py, docs/mcp-oauth-threat-model.md "The upload link for a
read-only connection")."""

import pytest
from fastapi.testclient import TestClient

from tests.test_mcp_oauth import app, settings, universe  # noqa: F401 - fixtures: the app wired to the twin universe
from tests.test_reimport_mcp import catalog  # noqa: F401 - fixture: the catalog the file's cards are found in
from test_reimport_merge import KILLER, base_file, copies_of, file, line
from twins.mcp_client import McpClient
from vault import app as vault_app
from vault import importer, uploads
from vault.api import mcp
from vault.models import Import, StagedUpload


def connect(app, universe, *, write: bool, host: str) -> McpClient:
    """An app connected through OAuth as the same person, with the consent's Write box ticked or not."""
    client = McpClient(lambda: TestClient(app), universe.client_hosts.publish(host=host, name=f"Twin ({'write' if write else 'read'})"))
    client.connect(write=write, scope="read write")  # asks for both, as ChatGPT does; the person's Write box decides
    return client


@pytest.fixture
def read(app, universe, catalog):
    return connect(app, universe, write=False, host="other.example")


@pytest.fixture
def write(app, universe, catalog):
    return connect(app, universe, write=True, host="app.example")


def tool(client: McpClient, name: str, **arguments) -> dict:
    return client.tool(name, **arguments)["result"]


def ok(client: McpClient, name: str, **arguments) -> dict:
    out = tool(client, name, **arguments)
    assert not out.get("isError"), out
    return out["structuredContent"]


def listed(client: McpClient) -> dict[str, dict]:
    return {t["name"]: t for t in client.mcp("tools/list").json()["result"]["tools"]}


def ticket_of(started: dict) -> str:
    return started["url"].split("ticket=")[1]


def person_uploads(app, ticket: str, content: bytes, name: str = "big.csv"):
    with TestClient(app) as browser:  # the person's browser: the ticket is the credential, no session
        return browser.post("/upload", data={"ticket": ticket}, files={"file": (name, content, "text/csv")})


def imports_of(app) -> int:
    with app.state.db.sessions() as db:
        return db.query(Import).count()


# -- the cause, and what a read-only connection sees now --------------------------------------------------------------

def test_a_read_only_connection_is_offered_the_upload_link_and_nothing_that_changes_data(read):
    tools = listed(read)
    assert {"start_collection_upload", "get_staged_upload"} <= set(tools)
    assert not set(tools) & {"confirm_staged_upload", "import_collection_csv", "reset_collection", "update_owned_cards",
                             "move_cards", "save_deck"}
    # the tool is honest about what it is: it creates a link, so it is not marked read-only; it destroys nothing
    notes = tools["start_collection_upload"]["annotations"]
    assert notes["readOnlyHint"] is False and notes["destructiveHint"] is False
    # and it is the only write tool a read-only connection is given: a new one has to be added here on purpose
    assert {t.name for t in mcp.TOOLS if t.write and t.any_scope} == {"start_collection_upload"}
    assert {n for n in tools if mcp.BY_NAME[n].write} == {"start_collection_upload"}


def test_a_read_only_connection_can_start_an_upload_and_read_it_waiting(read):
    started = ok(read, "start_collection_upload")
    assert started["url"].startswith("http://testserver/upload?ticket=") and started["expires_at"] and started["bucket"] is None
    assert ok(read, "get_staged_upload", upload_id=started["id"])["status"] == "waiting"


def test_the_apply_route_is_not_open_to_a_read_only_caller():
    assert "/api/v1/uploads" in vault_app.READ_ONLY_POSTS
    assert not [p for p in vault_app.READ_ONLY_POSTS if p.startswith("/api/v1/uploads/")]  # the apply is not in it


# -- nothing is imported until the person confirms, and confirming needs write ------------------------------------------

def test_a_read_only_connection_stages_and_previews_but_cannot_confirm(app, read, write):
    ok(write, "import_collection_csv", csv=base_file().decode(), confirm=True)
    before, killers = imports_of(app), copies_of(app, "A Killer Among Us")
    started = ok(read, "start_collection_upload")
    page = person_uploads(app, ticket_of(started), file(line(*KILLER, qty=5)))
    assert page.status_code == 200 and "Nothing has been imported" in page.text
    staged = ok(read, "get_staged_upload", upload_id=started["id"])
    assert staged["status"] == "uploaded" and len(staged["content_hash"]) == 64
    preview = ok(read, "confirm_staged_upload", upload_id=started["id"])  # no confirm: the preview, a read
    assert preview["content_hash"] == staged["content_hash"]
    refused = tool(read, "confirm_staged_upload", upload_id=started["id"], confirm=True, content_hash=staged["content_hash"])
    assert refused["isError"] is True and "read-only" in refused["content"][0]["text"]
    assert tool(read, "import_collection_csv", csv=base_file().decode(), confirm=True)["isError"] is True
    assert imports_of(app) == before and copies_of(app, "A Killer Among Us") == killers  # nothing changed
    # the person's write-scope connection can confirm what the read-only one staged and previewed
    done = ok(write, "confirm_staged_upload", upload_id=started["id"], confirm=True, content_hash=staged["content_hash"])
    assert done["import_id"] and imports_of(app) == before + 1 and copies_of(app, "A Killer Among Us") == 5


def test_the_preview_names_the_conflicts_the_same_for_a_read_only_and_a_write_connection(app, read, write):
    ok(write, "import_collection_csv", csv=base_file().decode(), confirm=True)
    edit = [{"action": "remove", "name": "A Killer Among Us", "quantity": 1, "set": "mkm", "number": "167"}]
    ok(write, "confirm_owned_cards_update", lines=edit, confirmation=ok(write, "update_owned_cards", lines=edit)["confirmation"])
    assert copies_of(app, "A Killer Among Us") == 3  # cut in the Vault; their app now says 5: changed on both sides
    started = ok(read, "start_collection_upload")
    assert person_uploads(app, ticket_of(started), file(line(*KILLER, qty=5))).status_code == 200
    seen = ok(read, "get_staged_upload", upload_id=started["id"])
    (killer,) = seen["merge"]["conflicts"]["cards"]
    assert killer["card"] == "A Killer Among Us" and killer["kind"] == "changed_in_both" and killer["keeps"] == "vault"
    assert (killer["last_import_copies"], killer["app_copies"], killer["vault_copies"]) == (4, 5, 3)
    assert seen["merge"]["mode"] == "merge" and "unmatched_rows" in seen and "changes" in seen
    assert ok(write, "get_staged_upload", upload_id=started["id"]) == seen  # the same answer for either grant
    answered = ok(read, "get_staged_upload", upload_id=started["id"], conflicts="app")["merge"]["conflicts"]["cards"]
    assert [c["keeps"] for c in answered] == ["app"] and copies_of(app, "A Killer Among Us") == 3  # and still nothing imported


def test_a_read_only_personal_token_can_start_and_read_but_not_import(app):
    """The REST route is shared with personal access tokens: a read-only one behaves as the read-only connection does."""
    with TestClient(app) as client:
        assert client.post("/api/auth/dev-login").status_code == 200
        created = client.post("/api/v1/me/tokens", json={"name": "read only", "scopes": ["read"]}).json()
    with TestClient(app) as bot:
        headers = {"Authorization": f"Bearer {created['token']}"}
        started = bot.post("/api/v1/uploads", headers=headers)
        assert started.status_code == 201, started.text
        upload_id = started.json()["id"]
        assert bot.get(f"/api/v1/uploads/{upload_id}", headers=headers).json()["status"] == "waiting"
        assert bot.post(f"/api/v1/uploads/{upload_id}/apply", params={"content_hash": "0" * 64}, headers=headers).status_code == 403
        assert bot.post("/api/v1/imports", headers=headers, files={"file": ("a.csv", base_file(), "text/csv")}).status_code == 403


# -- limits hold for a read-only connection -----------------------------------------------------------------------------

def test_a_read_only_connection_holds_at_most_five_open_links_and_only_for_the_persons_own_buckets(app, read):
    links = [ok(read, "start_collection_upload") for _ in range(uploads.MAX_OPEN)]
    over = tool(read, "start_collection_upload")
    assert over["isError"] is True and "5 open upload links" in over["content"][0]["text"]
    with app.state.db.sessions() as db:
        assert db.query(StagedUpload).count() == len(links)  # the refused call stored nothing
    assert tool(read, "start_collection_upload", bucket_id=999_999)["isError"] is True  # nobody's bucket


def test_a_link_started_by_a_read_only_connection_takes_no_more_than_twenty_megabytes(app, read):
    started = ok(read, "start_collection_upload")
    big = person_uploads(app, ticket_of(started), b"x" * (importer.MAX_UPLOAD_BYTES + 1))
    assert big.status_code == 413 and "20 MB" in big.text
    with app.state.db.sessions() as db:
        assert db.get(StagedUpload, started["id"]).content is None  # nothing was kept
    assert ok(read, "get_staged_upload", upload_id=started["id"])["status"] == "waiting"


def test_a_read_only_connection_cannot_reach_another_persons_upload(app, universe, read):
    started = ok(read, "start_collection_upload")
    stranger = McpClient(lambda: TestClient(app), universe.client_hosts.publish(host="evil.example", name="Twin (read, bob)"))
    stranger.connect(write=False, email="bob@example.com")
    assert tool(stranger, "get_staged_upload", upload_id=started["id"])["isError"] is True


def test_the_tool_descriptions_state_the_limits_the_server_enforces():
    text = mcp.BY_NAME["start_collection_upload"].description
    assert f"At most {uploads.MAX_OPEN} links" in text and f"{importer.MAX_UPLOAD_BYTES // (1024 * 1024)} MB" in text
    assert "read-only" in text and "confirm_staged_upload" in text and "write permission" in text
