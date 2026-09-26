import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from connectorforge.models import AccessToken, LoginCode
from connectorforge.problems import Problem
from connectorforge.security import authorize, issue_login_code
from connectorforge.telemetry import JSONFormatter, redact


def test_login_is_single_use_and_csrf_required(client: TestClient) -> None:
    code = issue_login_code(client.app.state.db, "admin")
    response = client.post("/api/v1/auth/login", json={"code": code})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.post("/api/v1/auth/login", json={"code": code}).status_code == 401
    assert client.get("/api/v1/auth/me").json()["role"] == "admin"
    assert client.post("/api/v1/auth/logout").status_code == 403
    client.headers["x-csrf-token"] = response.json()["csrf"]
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_roles_cannot_grant_credentials(client: TestClient, role: str) -> None:
    code = issue_login_code(client.app.state.db, role)
    response = client.post("/api/v1/auth/login", json={"code": code})
    client.headers["x-csrf-token"] = response.json()["csrf"]
    assert client.post("/api/v1/keys", json={"name": "forbidden"}).status_code == 403


def test_key_scopes_expiry_and_revocation(admin: TestClient) -> None:
    response = admin.post(
        "/api/v1/keys",
        json={
            "name": "integration",
            "connectors": ["tickets"],
            "operations": ["listTickets"],
        },
    )
    assert response.status_code == 201
    token, key_id = response.json()["token"], response.json()["id"]
    db = admin.app.state.db
    assert authorize(db, token, "invoke", "tickets", "listTickets").role == "operator"
    for args in [
        ("manage", None, None),
        ("invoke", "billing", None),
        ("invoke", "tickets", "delete"),
    ]:
        with pytest.raises(Problem) as error:
            authorize(db, token, *args)
        assert error.value.status == 403
    with db.transaction() as session:
        row = session.get(AccessToken, key_id)
        assert token not in row.digest
        row.expires_at = time.time() - 1
    with pytest.raises(Problem, match="current"):
        authorize(db, token, "read")
    assert admin.delete(f"/api/v1/keys/{key_id}").status_code == 204
    with db.transaction() as session:
        assert session.get(AccessToken, key_id).revoked
    assert admin.delete("/api/v1/keys/missing").status_code == 404


def test_scope_escalation_is_rejected(admin: TestClient) -> None:
    assert (
        admin.post(
            "/api/v1/keys",
            json={
                "name": "bad",
                "role": "viewer",
                "scopes": ["manage"],
            },
        ).status_code
        == 422
    )


def test_origin_host_and_body_limits(client: TestClient) -> None:
    assert client.get("/healthz", headers={"host": "attacker.test"}).status_code == 400
    assert client.get("/healthz", headers={"origin": "https://attacker.test"}).status_code == 403
    assert (
        client.post("/api/v1/auth/login", content=b"a" * (2 * 1024 * 1024 + 1)).status_code == 413
    )
    assert "frame-ancestors 'none'" in client.get("/healthz").headers["content-security-policy"]


def test_expired_code_and_throttle(client: TestClient) -> None:
    code = issue_login_code(client.app.state.db, "viewer")
    with client.app.state.db.transaction() as session:
        session.scalar(select(LoginCode)).expires_at = 0
    assert client.post("/api/v1/auth/login", json={"code": code}).status_code == 401
    for _ in range(9):
        assert client.post("/api/v1/auth/login", json={"code": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"code": "wrong"}).status_code == 429


def test_bearer_auth_is_independent_of_browser_csrf(admin: TestClient) -> None:
    token = admin.post("/api/v1/keys", json={"name": "CLI"}).json()["token"]
    admin.cookies.clear()
    admin.headers.pop("x-csrf-token")
    admin.headers["authorization"] = "Bearer " + token
    assert admin.get("/api/v1/auth/me").status_code == 200
    admin.headers["authorization"] = "Basic test"
    assert admin.get("/api/v1/auth/me").status_code == 401


def test_validation_does_not_echo_credentials(client: TestClient) -> None:
    secret = "cf_login_" + "s" * 130
    response = client.post("/api/v1/auth/login", json={"code": secret})
    assert response.status_code == 422
    assert secret not in response.text


def test_revoked_key_cannot_authenticate(admin: TestClient) -> None:
    key = admin.post("/api/v1/keys", json={"name": "Revoked"}).json()
    assert admin.delete("/api/v1/keys/" + key["id"]).status_code == 204
    assert (
        admin.get(
            "/api/v1/auth/me", headers={"authorization": "Bearer " + key["token"]}
        ).status_code
        == 401
    )


def test_old_login_code_is_replaced(client: TestClient) -> None:
    first = issue_login_code(client.app.state.db, "viewer")
    second = issue_login_code(client.app.state.db, "viewer")
    assert client.post("/api/v1/auth/login", json={"code": first}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"code": second}).status_code == 200


def test_nested_redaction_and_structured_logging() -> None:
    data = redact({"nested": [{"refresh_token": "private", "message": "cf_key_sensitive"}]})
    assert "private" not in json.dumps(data)
    assert "cf_key_sensitive" not in json.dumps(data)
    record = logging.LogRecord("test", logging.INFO, "", 0, "Bearer abc", (), None)
    assert json.loads(JSONFormatter().format(record))["message"] == "[redacted]"
