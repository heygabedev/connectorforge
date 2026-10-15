from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from connectorforge.api import create_app
from connectorforge.config import Settings
from connectorforge.security import issue_login_code


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(Settings(tmp_path)), base_url="http://127.0.0.1:8000") as browser:
        yield browser


@pytest.fixture
def admin(client: TestClient):
    code = issue_login_code(client.app.state.db, "admin")
    response = client.post("/api/v1/auth/login", json={"code": code})
    client.headers["x-csrf-token"] = response.json()["csrf"]
    return client
