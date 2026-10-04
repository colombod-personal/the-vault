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
    done = call_tool(bot, write, "confirm_staged_upload", upload_id=started["id"], confirm=True)
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
