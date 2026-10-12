"""vault-wxy.6: the upload link works for a read-only connection, the default grant.

A person attached a collection file of about 7,000 cards in ChatGPT; the assistant could not offer the upload link, because
``start_collection_upload`` was a write tool: a read-only connection (the default consent) neither saw it in ``tools/list``
nor could call it (403). The link only stages a file that waits; nothing is imported until ``confirm_staged_upload`` (write
scope) is called with the preview's ``content_hash`` (vault/uploads.py, docs/mcp-oauth-threat-model.md "The upload link for a
read-only connection")."""

import html
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.test_mcp_oauth import app, settings, universe  # noqa: F401 - fixtures: the app wired to the twin universe
from tests.test_reimport_mcp import catalog  # noqa: F401 - fixture: the catalog the file's cards are found in
from test_reimport_merge import KILLER, base_file, copies_of, file, line
from twins.mcp_client import McpClient
from vault import app as vault_app
from vault import importer, uploads
from vault.api import mcp
from vault.api.mcp import _marked_as_mcp
from vault.models import Import, StagedUpload


BUCKETS = "/api/v1/collection/buckets"


def connect(app, universe, *, write: bool, host: str, email: str = "dev@localhost") -> McpClient:
    """An app connected through OAuth as the same person, with the consent's Write box ticked or not."""
    client = McpClient(lambda: TestClient(app), universe.client_hosts.publish(host=host, name=f"Twin ({'write' if write else 'read'})"))
    client.connect(write=write, email=email, scope="read write")  # asks for both, as ChatGPT does; the Write box decides
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
    # and these are the only write tools a read-only connection is given (the link, and ending its own sign-in: vault-wxy.3.1):
    # a new one has to be added here on purpose
    assert {t.name for t in mcp.TOOLS if t.write and t.any_scope} == {"start_collection_upload", "sign_out"}
    assert {n for n in tools if mcp.BY_NAME[n].write} == {"start_collection_upload", "sign_out"}


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




# -- limits hold for a read-only connection, and the denial is plain -----------------------------------------------------

def test_a_read_only_app_can_rotate_the_persons_waiting_links_but_not_read_or_import_what_they_hold(app, read, write):
    links = [ok(read, "start_collection_upload") for _ in range(uploads.MAX_OPEN)]
    sixth = ok(read, "start_collection_upload")  # vault-73p: replaced, not refused
    assert sixth["replaced_upload_id"] == links[0]["id"]
    assert ok(write, "start_collection_upload")["replaced_upload_id"] == links[1]["id"]  # the person's write app too
    with app.state.db.sessions() as db:
        assert db.query(StagedUpload).count() == uploads.MAX_OPEN  # five open, never six
    # a link that holds a file: the read-only app sees only what the person's own read grant shows, and cannot import it
    assert person_uploads(app, ticket_of(sixth), file(line(*KILLER, qty=1))).status_code == 200
    staged = ok(read, "get_staged_upload", upload_id=sixth["id"])
    refused = tool(read, "confirm_staged_upload", upload_id=sixth["id"], confirm=True, content_hash=staged["content_hash"])
    assert refused["isError"] is True and "read-only" in refused["content"][0]["text"]
    with app.state.db.sessions() as db:  # make every other link hold a file too: now the refusal is plain and lists them
        for row in db.query(StagedUpload).filter(StagedUpload.content.is_(None)):
            row.content, row.filename, row.uploaded_at = b"x", "a.csv", row.expires_at
        db.commit()
    for denied in (tool(read, "start_collection_upload"), tool(write, "start_collection_upload")):  # either scope
        text = denied["content"][0]["text"]
        assert denied["isError"] is True and "a file has been uploaded through each one" in text
        assert "confirm_staged_upload" in text and f"id {sixth['id']}" in text


def test_a_read_only_app_can_use_up_the_persons_start_budget_and_the_denial_is_plain(app, read, write):
    """The residual risk, written down as a test: the ten starts a minute are per person, whichever app uses them."""
    answers = []
    for _ in range(uploads.STARTS_PER_MINUTE + 1):
        answers.append(tool(read, "start_collection_upload"))
        with app.state.db.sessions() as db:  # keep the open count low so only the rate limit can answer
            db.query(StagedUpload).delete()
            db.commit()
    assert [a["isError"] for a in answers] == [False] * uploads.STARTS_PER_MINUTE + [True]
    denied = tool(write, "start_collection_upload")  # the person's own write app now waits for the same budget
    assert denied["isError"] is True and "Too many upload start requests" in denied["content"][0]["text"]


def test_a_read_only_connection_names_only_the_persons_own_buckets(app, read):
    bob = TestClient(app)
    assert bob.post("/api/auth/dev-login", params={"email": "bob@example.com"}).status_code == 200
    theirs = bob.post(BUCKETS, json={"name": "Bob's box"}).json()
    mine = read.browser.post(BUCKETS, json={"name": "Dev's box"}).json()  # the person's own session makes it
    for bucket_id in (theirs["id"], 999_999):  # a real person's bucket and nobody's bucket: the same answer
        refused = tool(read, "start_collection_upload", bucket_id=bucket_id)
        assert refused["isError"] is True and "Bucket not found" in refused["content"][0]["text"]
    own = ok(read, "start_collection_upload", bucket_id=mine["id"])
    assert own["bucket"] == {"id": mine["id"], "name": "Dev's box"}
    with app.state.db.sessions() as db:
        assert [r.bucket_id for r in db.query(StagedUpload)] == [mine["id"]]  # only the person's own link was stored


def test_a_link_started_by_a_read_only_connection_takes_no_more_than_twenty_megabytes(app, read):
    started = ok(read, "start_collection_upload")
    big = person_uploads(app, ticket_of(started), b"x" * (importer.MAX_UPLOAD_BYTES + 1))
    assert big.status_code == 413 and "20 MB" in big.text and "about 4 MB" in big.text
    with app.state.db.sessions() as db:
        assert db.get(StagedUpload, started["id"]).content is None  # nothing was kept
    assert ok(read, "get_staged_upload", upload_id=started["id"])["status"] == "waiting"


def test_the_upload_page_has_its_own_allowance_per_link(app, read):
    """The page parses and matches up to 20 MB for whoever holds the ticket: IMPORT_LIMIT a minute for each link."""
    limit = app.state.settings.import_limit
    first, second = ok(read, "start_collection_upload"), ok(read, "start_collection_upload")
    answers = [person_uploads(app, ticket_of(first), file(line(*KILLER, qty=1))) for _ in range(limit + 1)]
    assert [a.status_code for a in answers] == [200] * limit + [429]
    assert "Too many uploads" in answers[-1].text and "new link" in answers[-1].text
    assert int(answers[-1].headers["retry-after"]) >= 1
    assert person_uploads(app, ticket_of(second), file(line(*KILLER, qty=1))).status_code == 200  # another link has its own
    assert ok(read, "get_staged_upload", upload_id=first["id"])["status"] == "uploaded"  # the last good file stays


def test_a_loop_on_the_upload_page_does_not_touch_the_persons_imports(app, read):
    """An app the person disconnected can still hold its ticket and loop on the page. Refused tries count, so the page's counter
    stays above its limit for as long as it loops: that must be the link's counter, not the one the person's /imports uses."""
    limit = app.state.settings.import_limit
    started = ok(read, "start_collection_upload")
    codes = [person_uploads(app, ticket_of(started), file(line(*KILLER, qty=1))).status_code for _ in range(limit * 3)]
    assert codes[:limit] == [200] * limit and set(codes[limit:]) == {429}  # the loop is refused and keeps counting
    files = {"file": ("e.csv", base_file(), "text/csv")}
    assert read.browser.post("/api/v1/imports/preview", files=files).status_code == 200  # the person's own, untouched
    assert read.browser.post("/api/v1/imports", files=files).status_code == 201
    assert ok(read, "start_collection_upload")["id"] != started["id"]  # and a new link is theirs to ask for


def test_a_made_up_ticket_leaves_every_counter_untouched(app, read):
    from sqlalchemy import func
    from vault.models import RateHit
    limit = app.state.settings.import_limit
    started = ok(read, "start_collection_upload")  # its own start was counted: take the count after it

    def counted() -> int:
        with app.state.db.sessions() as db:
            return db.query(func.coalesce(func.sum(RateHit.hits), 0)).scalar()

    before = counted()
    for _ in range(limit * 3):
        assert person_uploads(app, "not-a-real-ticket", file(line(*KILLER, qty=1))).status_code == 404
    assert counted() == before  # nothing was counted: not for the person, not for any link
    answers = [person_uploads(app, ticket_of(started), file(line(*KILLER, qty=1))) for _ in range(limit)]
    assert [a.status_code for a in answers] == [200] * limit  # the real link still has its whole allowance


def test_another_person_cannot_read_or_apply_an_upload_a_read_only_app_started(app, universe, read):
    started = ok(read, "start_collection_upload")
    assert person_uploads(app, ticket_of(started), file(line(*KILLER, qty=5))).status_code == 200
    staged = ok(read, "get_staged_upload", upload_id=started["id"])
    for write_scope in (False, True):  # a stranger with a read grant, and one with a write grant
        host = "evil.example" if write_scope else "plain.example"
        bob = connect(app, universe, write=write_scope, host=host, email="bob@example.com")
        got = tool(bob, "get_staged_upload", upload_id=started["id"])
        assert got["isError"] is True and "No such upload" in got["content"][0]["text"]
        applied = tool(bob, "confirm_staged_upload", upload_id=started["id"], confirm=True, content_hash=staged["content_hash"])
        assert applied["isError"] is True
        assert ("No such upload" if write_scope else "read-only") in applied["content"][0]["text"]
    assert ok(read, "get_staged_upload", upload_id=started["id"])["status"] == "uploaded"  # still there, still the person's


def test_the_tool_descriptions_state_the_limits_the_server_enforces():
    text = mcp.BY_NAME["start_collection_upload"].description
    assert f"at most {uploads.MAX_OPEN} open links" in text and f"{importer.MAX_UPLOAD_BYTES // (1024 * 1024)} MB" in text
    assert "read-only" in text and "confirm_staged_upload" in text and "write permission" in text
    assert "not single-use" in text and "There is no list or cancel yet" in text  # said where the link is described
    assert "oldest link that holds no file stop working" in text and "is never replaced" in text  # the cap, as it behaves
    assert "changes nothing in the person's collection or decks" in text
    # the server's own limit is 20 MB; on the hosted site the platform's request cap (docs/developing.md) is what a person meets
    assert "mtgvault.cards" in text and "about 4 MB" in text and "before the Vault sees it" in text
    assert "a file may be 20 MB" not in text  # the old sentence: true for the server, false for the hosted site


def test_llms_txt_does_not_call_the_upload_link_one_time():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "public" / "llms.txt").read_text(encoding="utf-8")
    assert "one-time link" not in text and "link, good for one hour" in text  # it is reusable until it expires


def test_the_consent_screen_tells_a_person_that_read_includes_the_upload_link(app, universe):
    client = McpClient(lambda: TestClient(app), universe.client_hosts.publish())
    client.sign_in()
    text = html.unescape(client.authorize(scope="read write").text)
    assert "give you a one-hour link to upload a collection file" in text
    assert "read what importing that file would change. Importing it needs Write." in text


# -- the allow-list, swept (review of 2026-10-11) -----------------------------------------------------------------------

UNSAFE = {"post", "put", "patch", "delete"}
# What a read-only OAuth token could already do before this change, by method and path template: the allow-list's POSTs...
BEFORE_THIS_CHANGE = {("POST", p) for p in (
    "/api/v1/decks/parse", "/api/v1/imports/preview", "/api/v1/decks/simulate", "/api/v1/decks/coverage", "/api/v1/auth/revoke",
    "/api/v1/cards/lookup", "/api/v1/decks/stats", "/api/v1/decks/legality", "/api/v1/decks/upgrades",
    "/api/v1/decks/validate-changes", "/api/v1/decks/shopping-list", "/api/v1/decks/combos", "/api/v1/decks/roles")}
# ...and the routes that take no access token's authority at all (sign-in, and two public catalog computations).
TAKE_NO_ACCESS_TOKEN = {("POST", "/api/v1/auth/native/{provider}"), ("POST", "/api/v1/auth/token"),
                        ("POST", "/api/v1/catalog/walkthrough"), ("POST", "/api/v1/catalog/verify-citation")}
THE_ONE_EXEMPTION = {("POST", "/api/v1/uploads")}
# ...and the one that ends the caller's own app connection (vault-wxy.3.1): any connection may end itself, and nothing else.
ENDS_ITS_OWN_CONNECTION = {("POST", "/api/v1/agent/sign-out")}
END_THE_TOKEN = ("/api/v1/auth/revoke", "/api/v1/agent/sign-out")


def in_process(app) -> TestClient:
    """The app as an MCP tool call reaches it: marked, so an OAuth access token is accepted on it (vault.api.mcp)."""
    return TestClient(_marked_as_mcp(app))


def unsafe_routes(app):
    """Every documented unsafe-method route under /api/v1 (all of them are in the OpenAPI document)."""
    for path, methods in app.openapi()["paths"].items():
        if path.startswith("/api/v1/"):
            for method in sorted(set(methods) & UNSAFE):
                yield method.upper(), path


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_a_read_only_oauth_token_is_refused_on_every_unsafe_route_but_the_upload_link(app, read):
    token = read.tokens["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    routes = sorted(unsafe_routes(app), key=lambda r: r[1] in END_THE_TOKEN)  # these two end the token: last
    assert len(routes) > 40  # the sweep really saw the API
    not_refused = set()
    with in_process(app) as c:
        for method, path in routes:
            res = c.request(method, concrete(path), headers=headers, json={})
            if res.status_code == 403:
                assert "insufficient_scope" in res.headers.get("www-authenticate", ""), (method, path)
            else:
                not_refused.add((method, path))
    # nothing new is open to a read-only token, and the one thing this change opens is exactly the upload link
    assert not_refused == BEFORE_THIS_CHANGE | TAKE_NO_ACCESS_TOKEN | THE_ONE_EXEMPTION | ENDS_ITS_OWN_CONNECTION, (
        not_refused ^ (BEFORE_THIS_CHANGE | TAKE_NO_ACCESS_TOKEN | THE_ONE_EXEMPTION | ENDS_ITS_OWN_CONNECTION))


def test_an_oauth_token_is_not_accepted_on_the_rest_route_directly(app, read):
    """The link is reached only through the MCP server: an OAuth access token on /api/v1/uploads itself is no credential."""
    with TestClient(app) as direct:
        res = direct.post("/api/v1/uploads", headers={"Authorization": f"Bearer {read.tokens['access_token']}"})
    assert res.status_code == 401


def test_variants_of_the_allowed_path_are_not_exempted(app, read):
    headers = {"Authorization": f"Bearer {read.tokens['access_token']}"}
    apply = "/api/v1/uploads/1/apply?content_hash=" + "0" * 64
    with in_process(app) as c:
        for method, url in (("POST", "/api/v1/imports?next=/api/v1/uploads"), ("POST", apply + "&x=/api/v1/uploads"),
                            ("POST", "/api/v1/uploads%2F1%2Fapply?content_hash=" + "0" * 64), ("POST", "/api/v1//uploads"),
                            ("POST", "/api/v1/uploads/1"), ("POST", "/api/v1/Uploads"),
                            ("PUT", "/api/v1/uploads"), ("PATCH", "/api/v1/uploads"), ("DELETE", "/api/v1/uploads")):
            res = c.request(method, url, headers=headers, json={}, follow_redirects=False)
            assert res.status_code not in (200, 201, 202, 204), (method, url, res.status_code)
        assert c.post("/api/v1/uploads/", headers=headers, follow_redirects=False).status_code != 201  # a redirect, or nothing
        assert c.post("/api/v1/uploads", headers=headers, follow_redirects=False).status_code == 201  # only the exact path


def test_the_allow_list_exempts_only_a_post_at_exactly_that_path():
    for path in vault_app.READ_ONLY_POSTS:
        assert not vault_app.is_write("POST", path), path
        assert all(vault_app.is_write(method, path) for method in ("PUT", "PATCH", "DELETE")), path
        assert not vault_app.is_write("GET", path)  # a read stays a read
        assert vault_app.is_write("POST", path + "/") and vault_app.is_write("POST", path + "/1")
    assert vault_app.is_write("POST", "/api/v1/uploads/1/apply")
