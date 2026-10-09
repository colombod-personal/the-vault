"""Big collection files through an assistant (vault/uploads.py, #101 G1): a one-time link, the file staged and
previewed, imported only on confirm, and the link useless to anyone else or after it is used or expired."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from test_agents import CSV, V1, agent, bot, call_tool, make_token  # noqa: F401 - fixtures


def ticket_of(url: str) -> str:
    return url.split("ticket=")[1]


def upload(client, ticket, content=CSV):
    return client.post("/upload", data={"ticket": ticket}, files={"file": ("big.csv", content, "text/csv")})


def test_link_upload_preview_confirm(agent, bot):
    write = make_token(agent, scopes=["read", "write"])
    started = call_tool(bot, write, "start_collection_upload")["structuredContent"]
    assert call_tool(bot, write, "get_staged_upload", upload_id=started["id"])["structuredContent"]["status"] == "waiting"
    with TestClient(agent.app) as browser:  # the person's browser: no session needed, the ticket is the credential
        assert browser.get(f"/upload?ticket={ticket_of(started['url'])}").status_code == 200
        res = upload(browser, ticket_of(started["url"]))
        assert res.status_code == 200 and "Nothing has been imported" in res.text
    before = agent.get(f"{V1}/imports").json()["total"]
    staged = call_tool(bot, write, "get_staged_upload", upload_id=started["id"])["structuredContent"]
    assert staged["status"] == "uploaded" and "unmatched_rows" in staged and "changes" in staged
    preview = call_tool(bot, write, "confirm_staged_upload", upload_id=started["id"])["structuredContent"]
    assert preview["status"] == "uploaded" and agent.get(f"{V1}/imports").json()["total"] == before
    assert preview["content_hash"] == staged["content_hash"] and len(preview["content_hash"]) == 64
    done = call_tool(bot, write, "confirm_staged_upload", upload_id=started["id"], confirm=True,
                     content_hash=preview["content_hash"])
    assert not done.get("isError"), done
    assert agent.get(f"{V1}/imports").json()["total"] == before + 1
    gone = call_tool(bot, write, "get_staged_upload", upload_id=started["id"])
    assert gone.get("isError")  # the staged file is deleted once imported


def test_a_read_only_connection_cannot_start_or_confirm(agent, bot):
    read = make_token(agent)
    assert call_tool(bot, read, "start_collection_upload").get("isError")


def test_a_ticket_works_only_for_its_owner_and_only_until_it_expires(app, agent, bot):
    from vault.models import StagedUpload
    write = make_token(agent, scopes=["read", "write"])
    started = call_tool(bot, write, "start_collection_upload")["structuredContent"]
    with TestClient(agent.app) as bob:
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        bob_token = make_token(bob, scopes=["read", "write"])
        assert call_tool(bot, bob_token, "get_staged_upload", upload_id=started["id"]).get("isError")
    with app.state.db.sessions() as db:
        row = db.get(StagedUpload, started["id"])
        assert ticket_of(started["url"]) not in row.ticket_hash  # only the hash is stored
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    with TestClient(agent.app) as browser:
        assert upload(browser, ticket_of(started["url"])).status_code == 404
        assert browser.get(f"/upload?ticket={ticket_of(started['url'])}").status_code == 404
    assert call_tool(bot, write, "get_staged_upload", upload_id=started["id"]).get("isError")


def test_a_made_up_ticket_gets_nothing(client):
    assert client.get("/upload?ticket=not-a-real-ticket").status_code == 404
    assert upload(client, "not-a-real-ticket").status_code == 404


def test_the_upload_page_reads_as_text_and_warns_before_a_big_removal(agent, bot):
    write = make_token(agent, scopes=["read", "write"])
    started = call_tool(bot, write, "start_collection_upload")["structuredContent"]
    smaller = b"\n".join(CSV.splitlines()[:3]) + b"\n"  # one card left: the rest would be removed
    with TestClient(agent.app) as browser:
        res = upload(browser, ticket_of(started["url"]), smaller)
    assert "{" not in res.text.split("<body>")[1]  # no raw data on the page (found in a real run)
    assert "would remove" in res.text and "Removed:" in res.text and "1 row," in res.text


# -- #352: what is applied is what was previewed; limits; retention --------------------------------------------------

def start(client):
    res = client.post(f"{V1}/uploads")
    assert res.status_code == 201, res.text
    return res.json()


def test_apply_imports_only_the_file_that_was_previewed(agent):
    started = start(agent)
    ticket = ticket_of(started["url"])
    with TestClient(agent.app) as browser:
        assert upload(browser, ticket).status_code == 200
        shown = agent.get(f"{V1}/uploads/{started['id']}").json()
        other = CSV.replace(b"Sol Ring", b"Sol Ring Two", 1) if b"Sol Ring" in CSV else CSV + b"\n"
        assert upload(browser, ticket, other).status_code == 200  # the link is used again after the preview
    before = agent.get(f"{V1}/imports").json()["total"]
    changed = agent.post(f"{V1}/uploads/{started['id']}/apply", params={"content_hash": shown["content_hash"]})
    assert changed.status_code == 409 and "uploaded again after the preview" in changed.json()["detail"]
    assert agent.post(f"{V1}/uploads/{started['id']}/apply").status_code == 422  # a hash is needed
    assert agent.post(f"{V1}/uploads/{started['id']}/apply", params={"content_hash": "x" * 64}).status_code == 422
    assert agent.get(f"{V1}/imports").json()["total"] == before  # nothing was imported
    again = agent.get(f"{V1}/uploads/{started['id']}").json()
    assert again["content_hash"] != shown["content_hash"]
    ok = agent.post(f"{V1}/uploads/{started['id']}/apply", params={"content_hash": again["content_hash"]})
    assert ok.status_code == 201 and agent.get(f"{V1}/imports").json()["total"] == before + 1


def test_a_person_holds_at_most_five_open_links_and_starts_at_most_ten_a_minute(app, agent):
    from vault.models import StagedUpload
    first = [start(agent) for _ in range(5)]
    over = agent.post(f"{V1}/uploads")
    assert over.status_code == 409 and "5 open upload links" in over.json()["detail"]
    with app.state.db.sessions() as db:  # one expires: room again
        row = db.get(StagedUpload, first[0]["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert agent.post(f"{V1}/uploads").status_code == 201
    with TestClient(agent.app) as bob:  # another person has their own allowance
        bob.post("/api/auth/dev-login", params={"email": "bob@example.com"})
        assert bob.post(f"{V1}/uploads").status_code == 201


def test_starting_links_is_rate_limited_per_person(app, agent):
    from vault.models import StagedUpload
    codes = []
    for _ in range(12):
        res = agent.post(f"{V1}/uploads")
        codes.append(res.status_code)
        with app.state.db.sessions() as db:  # keep the open count low so only the rate limit can answer 429
            db.query(StagedUpload).delete()
            db.commit()
    assert codes[:10] == [201] * 10 and 429 in codes[10:]


def test_the_daily_job_deletes_expired_uploads_and_keeps_live_ones(app, agent):
    from vault import retention
    from vault.models import StagedUpload
    old, live = start(agent), start(agent)
    with app.state.db.sessions() as db:
        db.get(StagedUpload, old["id"]).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    with app.state.db.sessions() as db:
        report = retention.apply(db)
        assert report["uploads_deleted"] == 1
        assert db.get(StagedUpload, old["id"]) is None and db.get(StagedUpload, live["id"]) is not None
