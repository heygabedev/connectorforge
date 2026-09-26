from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from connectorforge.api import create_app
from connectorforge.cli import app
from connectorforge.config import Settings, require_safe_sqlite, sqlite_is_patched


@pytest.mark.parametrize(
    ("version", "expected"),
    [("3.50.4", False), ("3.51.2", False), ("3.51.3", True), ("3.50.7", True), ("3.44.6", True)],
)
def test_sqlite_patch_detection(version: str, expected: bool) -> None:
    assert sqlite_is_patched(version) is expected


def test_unpatched_runtime_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("connectorforge.config.sqlite3.sqlite_version", "3.50.4")
    with pytest.raises(RuntimeError, match="WAL-reset"):
        require_safe_sqlite()


def test_settings_reject_invalid_port(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Port"):
        Settings(tmp_path, port=80)


def test_health(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(tmp_path))) as client:
        assert client.get("/healthz").json()["status"] == "ok"


def test_doctor_reports_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONNECTORFORGE_HOME", str(tmp_path))
    result = CliRunner().invoke(app, ["doctor"])
    assert '"sqlite_patched"' in result.stdout
    assert "state_directory" in result.stdout
