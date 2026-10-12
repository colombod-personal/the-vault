"""What the sign_out preview says about the app and the other connections (found by the review of PR 471): a client that registered
itself chooses its own name, so the preview calls it "Unverified app: ..." exactly as the consent screen does; and a connection
whose refresh token ran out is dead, so it is not counted among "other connections"."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.test_mcp_oauth import app, client, db_do, make_client, settings, universe  # noqa: F401 - fixtures
from tests.test_sign_out import another_device, grants, result
from twins.mcp_client import McpClient
from vault import oauth_server
from vault.models import OAuthGrant


def test_a_self_registered_client_is_called_unverified_in_the_preview_and_after(app, client):
    registered = client.browser.post("/oauth/register", json={"client_name": "Totally Official Vault App",
                                                              "redirect_uris": ["https://app.example/callback"]})
    assert registered.status_code == 201, registered.text
    dcr = McpClient(lambda: TestClient(app), registered.json()["client_id"])
    token = dcr.connect()["access_token"]
    preview = result(dcr, token, "sign_out")["structuredContent"]
    assert preview["connection"]["app"] == "Unverified app: Totally Official Vault App" and preview["connection"]["verified"] is False
    assert "Unverified app: Totally Official Vault App" in preview["what_happens"]
    done = result(dcr, token, "sign_out", confirm=True)["structuredContent"]
    assert done["signed_out"] is True and done["what_happens"].startswith("Signed out. ") and "Unverified app:" in done["what_happens"]


def test_an_app_identified_by_its_web_address_is_named_with_that_address(app, client):
    token = client.connect()["access_token"]
    body = result(client, token, "sign_out")["structuredContent"]
    assert body["connection"]["app"] == "Twin Agent" and body["connection"]["verified"] is True
    assert body["connection"]["domain"] == "app.example" and "Twin Agent (app.example)" in body["what_happens"]


def test_a_connection_whose_refresh_token_ran_out_is_not_counted_and_the_preview_deletes_nothing(app, client):
    first = client.connect()["access_token"]
    second = another_device(client)  # a second connection that is alive
    third = another_device(client)  # and a third
    assert grants(app) == 3 and result(client, first, "sign_out")["structuredContent"]["connection"]["other_connections"] == 2
    ids = sorted(db_do(app, lambda db: [g.id for g in db.query(OAuthGrant).all()]))

    def expire(db):
        db.execute(update(OAuthGrant).where(OAuthGrant.id == ids[2]).values(refresh_expires=oauth_server._now() - timedelta(seconds=1)))
        db.commit()

    db_do(app, expire)
    body = result(client, first, "sign_out")["structuredContent"]
    assert body["connection"]["other_connections"] == 1  # the dead one is not "another app or device that stays signed in"
    assert grants(app) == 3  # a preview changes nothing, not even the cleanup the connected-apps listing does
    assert result(client, second, "whoami")["isError"] is False
    assert third  # (its refresh token is gone, its access token is not what this test is about)
