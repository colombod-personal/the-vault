"""vault-73p: at the cap of five open upload links a sixth request replaces the oldest link that holds no file, instead of
refusing for up to an hour (a new chat has no memory of the link it gave, and only a hash of the ticket is stored).

What must stay true (docs/mcp-oauth-threat-model.md, "The upload link for a read-only connection"): a link that holds a file
the person has not confirmed is never replaced; when all five hold one the answer is plain and lists them; the replaced link
answers exactly as an expired or made-up one (no oracle); a file that arrives while a link is being replaced is either kept
with its link or refused as expired, never reported as received and lost; every per-person and per-link limit still counts."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from test_agents import CSV, V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures
from test_staged_upload import start, ticket_of, upload
from vault import uploads
from vault.models import StagedUpload


def rows(app) -> dict[int, bool]:
    """The staged links in the database: id -> holds a file."""
    with app.state.db.sessions() as db:
        return {r.id: r.content is not None for r in db.query(StagedUpload).all()}


def five(agent) -> list[dict]:
    return [start(agent) for _ in range(uploads.MAX_OPEN)]


def give(app, link: dict, content: bytes = CSV) -> None:
    with TestClient(app) as browser:  # the person's browser: the ticket is the credential
        assert upload(browser, ticket_of(link["url"]), content).status_code == 200


def test_a_sixth_link_replaces_the_oldest_waiting_one_and_the_old_one_answers_as_expired(app, agent):
    links = five(agent)
    sixth = agent.post(f"{V1}/uploads")
    assert sixth.status_code == 201
    new = sixth.json()
    assert new["replaced_upload_id"] == links[0]["id"] and "no longer works" in new["next"]
    assert set(rows(app)) == {l["id"] for l in links[1:]} | {new["id"]}  # five open, never six
    # the replaced link answers like a made-up one: same status and words on the REST route and on the page
    gone, nobody = agent.get(f"{V1}/uploads/{links[0]['id']}"), agent.get(f"{V1}/uploads/9999999")
    assert gone.status_code == nobody.status_code == 404 and gone.json() == nobody.json()
    with TestClient(app) as browser:
        old_page = browser.get(f"/upload?ticket={ticket_of(links[0]['url'])}")
        made_up = browser.get("/upload?ticket=not-a-real-ticket")
        assert old_page.status_code == made_up.status_code == 404 and old_page.text == made_up.text
        old_post = upload(browser, ticket_of(links[0]["url"]))
        made_up_post = upload(browser, "not-a-real-ticket")
        assert old_post.status_code == made_up_post.status_code == 404 and old_post.text == made_up_post.text
        assert upload(browser, ticket_of(new["url"])).status_code == 200  # and the new link takes a file
    assert agent.get(f"{V1}/uploads/{links[1]['id']}").json()["status"] == "waiting"  # the others are untouched


def test_a_link_that_holds_a_file_is_never_replaced(app, agent):
    links = five(agent)
    give(app, links[0])  # the oldest holds the person's file
    give(app, links[1])
    first = agent.post(f"{V1}/uploads").json()
    assert first["replaced_upload_id"] == links[2]["id"]  # the oldest WAITING one, not the oldest
    second = agent.post(f"{V1}/uploads").json()
    assert second["replaced_upload_id"] == links[3]["id"]
    held = rows(app)
    assert held[links[0]["id"]] is True and held[links[1]["id"]] is True  # both still there, both still hold their file
    for link in links[:2]:
        assert agent.get(f"{V1}/uploads/{link['id']}").json()["status"] == "uploaded"


def test_when_all_five_hold_a_file_the_refusal_is_plain_lists_them_and_stores_nothing(app, agent):
    links = five(agent)
    for link in links:
        give(app, link)
    before = rows(app)
    refused = agent.post(f"{V1}/uploads")
    assert refused.status_code == 409
    text = refused.json()["detail"]
    assert "each holds a file the person uploaded and has not confirmed" in text and "get_staged_upload" in text
    assert "confirm_staged_upload" in text and "wait an hour" not in text
    assert all(f"id {l['id']}" in text for l in links)  # a new chat has no ids: they are listed here
    assert "big.csv" in text and "UTC" in text
    assert rows(app) == before  # nothing was replaced, nothing was created
    # confirming one frees a slot, and then no link is replaced
    ready = agent.get(f"{V1}/uploads/{links[0]['id']}").json()
    done = agent.post(f"{V1}/uploads/{links[0]['id']}/apply", params={"content_hash": ready["content_hash"]})
    assert done.status_code == 201
    again = agent.post(f"{V1}/uploads")
    assert again.status_code == 201 and "replaced_upload_id" not in again.json()


def test_a_file_that_was_committed_before_the_replacement_keeps_its_link(app, agent):
    """The race, first order: the file lands, then the sixth link is asked for. The test is made under the row's lock."""
    links = five(agent)
    give(app, links[0])
    new = agent.post(f"{V1}/uploads").json()
    assert new["replaced_upload_id"] == links[1]["id"] and rows(app)[links[0]["id"]] is True


def test_a_file_being_written_while_the_sixth_link_is_made_keeps_its_link(app, agent):
    """The race, second order: the file's write is in flight (its row is locked, not yet committed) when the sixth link is
    asked for. The replacement skips that row and takes the next oldest waiting one; the file's commit then stands."""
    from sqlalchemy import update
    links = five(agent)
    with app.state.db.sessions() as writer:
        writer.execute(update(StagedUpload).where(StagedUpload.id == links[0]["id"]).values(
            content=CSV, filename="big.csv", uploaded_at=datetime.now(timezone.utc)))  # written, not committed: row locked
        new = agent.post(f"{V1}/uploads")  # on its own connection; must not wait for the lock and must not take that row
        assert new.status_code == 201 and new.json()["replaced_upload_id"] == links[1]["id"]
        writer.commit()
    after = rows(app)
    assert after[links[0]["id"]] is True and links[1]["id"] not in after  # the file kept its link


def test_a_file_that_arrives_after_its_link_was_replaced_is_refused_as_expired_and_not_reported_received(app, agent, monkeypatch):
    """The race, third order: the page has already found the link when it is replaced; the file's write then matches no row."""
    links = five(agent)
    real = uploads.per_user
    state = {"replaced": None}

    def replace_then_count(request, bucket, subject, allowed, db=None, **kw):
        if bucket == "upload page" and state["replaced"] is None:  # the page has found the link: now the sixth link is made
            with TestClient(app) as other:
                other.post("/api/auth/dev-login")
                state["replaced"] = other.post(f"{V1}/uploads").json()["replaced_upload_id"]
        return real(request, bucket, subject, allowed, db, **kw)

    monkeypatch.setattr(uploads, "per_user", replace_then_count)
    with TestClient(app) as browser:
        res = upload(browser, ticket_of(links[0]["url"]))
    assert state["replaced"] == links[0]["id"]
    assert res.status_code == 404 and "File received" not in res.text and "expired" in res.text  # not a 500, not "received"
    assert links[0]["id"] not in rows(app)  # and nothing was stored under that id
    assert all(not held for held in rows(app).values())  # no file anywhere


def test_a_read_only_token_and_a_write_token_get_the_same_replacement(app, agent, bot):
    write = make_token(agent, scopes=["read", "write"])
    read = make_token(agent)
    links = [call_tool(bot, write, "start_collection_upload")["structuredContent"] for _ in range(uploads.MAX_OPEN)]
    by_read = call_tool(bot, read, "start_collection_upload")
    by_write = call_tool(bot, write, "start_collection_upload")
    assert not by_read.get("isError") and not by_write.get("isError"), (by_read, by_write)
    a, b = by_read["structuredContent"], by_write["structuredContent"]
    assert a["replaced_upload_id"] == links[0]["id"] and b["replaced_upload_id"] == links[1]["id"]
    assert set(a) == set(b)  # the same answer shape for either scope
    # and a read-only token still cannot read or import the file the replacement protects
    give(app, a)
    staged = call_tool(bot, read, "get_staged_upload", upload_id=a["id"])["structuredContent"]
    refused = call_tool(bot, read, "confirm_staged_upload", upload_id=a["id"], confirm=True, content_hash=staged["content_hash"])
    assert refused["isError"] is True and "read-only" in refused["content"][0]["text"]


def test_replacing_does_not_touch_another_persons_links(app, agent):
    with TestClient(app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        theirs = [bob.post(f"{V1}/uploads").json() for _ in range(uploads.MAX_OPEN)]
        mine = five(agent)
        assert agent.post(f"{V1}/uploads").json()["replaced_upload_id"] == mine[0]["id"]
        assert {t["id"] for t in theirs} <= set(rows(app))  # every one of bob's links is still there
        assert bob.get(f"{V1}/uploads/{theirs[0]['id']}").json()["status"] == "waiting"
        assert agent.get(f"{V1}/uploads/{theirs[0]['id']}").status_code == 404  # and mine cannot see his


def test_the_ten_starts_a_minute_limit_still_counts_when_a_link_is_replaced(app, agent):
    links = five(agent)  # five starts
    codes = [agent.post(f"{V1}/uploads").status_code for _ in range(uploads.STARTS_PER_MINUTE - 5)]  # five replacements
    assert codes == [201] * 5
    before = rows(app)
    over = agent.post(f"{V1}/uploads")  # the eleventh in the minute
    assert over.status_code == 429 and over.headers["retry-after"]
    assert rows(app) == before  # a refused start replaces nothing
    assert not set(before) & {links[0]["id"]}  # (the first link went with the replacements)


def test_the_page_allowance_per_link_is_unchanged(app, agent):
    limit = app.state.settings.import_limit
    link = start(agent)
    with TestClient(app) as browser:
        codes = [upload(browser, ticket_of(link["url"])).status_code for _ in range(limit + 1)]
    assert codes == [200] * limit + [429]
