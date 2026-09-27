import pytest
from fastapi.testclient import TestClient

from vault.app import create_app
from vault.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path}/test.db", session_secret="test", dev_login=True,
                    base_url="http://testserver", facebook_client_secret="fb-secret")


@pytest.fixture
def app(settings):
    return create_app(settings, serve_static=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def signed_in(client):
    assert client.post("/api/auth/dev-login").status_code == 200
    return client
