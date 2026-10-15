import asyncio
from pathlib import Path

import pytest

from connectorforge.config import Settings
from connectorforge.security import SecurityBoundary


async def exercise(
    tmp_path: Path,
    messages: list[dict],
    *,
    development: bool = False,
    origin: bytes = b"http://127.0.0.1:8000",
):
    captured, delivered = [], []

    async def application(scope, receive, send):
        delivered.append(await receive())

    async def receive():
        return messages.pop(0)

    async def send(message):
        captured.append(message)

    boundary = SecurityBoundary(application, Settings(tmp_path, development=development))
    await boundary(
        {"type": "http", "headers": [(b"host", b"127.0.0.1:8000"), (b"origin", origin)]},
        receive,
        send,
    )
    return captured, delivered


async def test_chunked_upload_is_combined_before_dispatch(tmp_path: Path):
    _, delivered = await exercise(
        tmp_path,
        [
            {"type": "http.request", "body": b"{", "more_body": True},
            {"type": "http.request", "body": b"}", "more_body": False},
        ],
    )
    assert delivered[0]["body"] == b"{}"


async def test_disconnected_upload_never_dispatches(tmp_path: Path):
    _, delivered = await exercise(tmp_path, [{"type": "http.disconnect"}])
    assert not delivered


async def test_development_origin_is_explicit(tmp_path: Path):
    response, _ = await exercise(tmp_path, [], origin=b"http://127.0.0.1:5173")
    assert response[0]["status"] == 403
    _, delivered = await exercise(
        tmp_path,
        [{"type": "http.request", "body": b""}],
        development=True,
        origin=b"http://127.0.0.1:5173",
    )
    assert delivered


async def test_slow_upload_has_a_total_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("connectorforge.security.BODY_TIMEOUT_SECONDS", 0.001)
    captured = []

    async def application(scope, receive, send):
        pytest.fail("Slow upload reached application")

    async def receive():
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b""}

    async def send(message):
        captured.append(message)

    await SecurityBoundary(application, Settings(tmp_path))(
        {"type": "http", "headers": [(b"host", b"127.0.0.1:8000")]},
        receive,
        send,
    )
    assert captured[0]["status"] == 408
