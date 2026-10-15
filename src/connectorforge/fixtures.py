"""Registered in-process mock transport with declarative response fixtures."""

import asyncio
import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from connectorforge.compiler import bounded
from connectorforge.problems import Problem

Fault = Literal[
    "none",
    "timeout",
    "throttle",
    "malformed",
    "unavailable",
    "disconnect",
    "schema_drift",
    "expired",
]


class Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: int = Field(default=200, ge=200, le=599)
    body: Any = None
    headers: dict[str, str] = Field(default_factory=dict, max_length=8)
    delay_ms: int = Field(default=0, ge=0, le=5000)

    @field_validator("body")
    @classmethod
    def safe_body(cls, value: Any) -> Any:
        bounded(value)
        if len(json.dumps(value, allow_nan=False).encode()) > 1024 * 1024:
            raise ValueError("Response fixture exceeds 1 MiB")
        return value

    @field_validator("headers")
    @classmethod
    def safe_headers(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if key.lower() not in {"retry-after", "x-next-cursor", "x-rate-limit-remaining"}:
                raise ValueError("Unsupported fixture response header")
            if len(item) > 128 or "\r" in item or "\n" in item:
                raise ValueError("Invalid fixture response header")
        return value


def validate_fixtures(fixtures: dict[str, Fixture], operations: list[dict[str, Any]]) -> None:
    indexed = {operation["id"]: operation for operation in operations}
    for name, fixture in fixtures.items():
        if name not in indexed or str(fixture.status) not in indexed[name]["responses"]:
            raise Problem(
                422, "invalid_fixture", "Fixtures must identify a declared operation and status."
            )
        if fixture.status in {204, 304} and fixture.body is not None:
            raise Problem(
                422, "invalid_fixture", "No-content responses cannot have a fixture body."
            )


async def dispatch_fixture(
    bundle: dict[str, Any],
    operation: dict[str, Any],
    *,
    fault: Fault = "none",
    attempt: int = 1,
    arguments: dict[str, Any] | None = None,
) -> httpx.Response:
    configured = bundle["fixtures"].get(operation["id"])
    if configured is None:
        raise Problem(
            409, "fixture_missing", "Configure a response fixture before running this operation."
        )
    fixture = Fixture.model_validate(configured)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != f"{bundle['connector']}.mock.invalid":
            raise Problem(403, "network_blocked", "Destination is not a registered mock provider.")
        await asyncio.sleep(fixture.delay_ms / 1000)
        if fault == "timeout":
            await asyncio.sleep(10)
        if fault == "disconnect":
            raise httpx.ReadError("Mock connection interrupted")
        if fault == "throttle" and attempt == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate_limited"})
        if fault == "unavailable":
            return httpx.Response(503, json={"error": "unavailable"})
        if fault == "expired":
            return httpx.Response(401, json={"error": "expired"})
        if fault == "malformed":
            return httpx.Response(
                fixture.status, content=b"{broken", headers={"Content-Type": "application/json"}
            )
        body = {"unexpected": True} if fault == "schema_drift" else fixture.body
        if fixture.status == 204 or operation["method"] == "HEAD":
            return httpx.Response(fixture.status, headers=fixture.headers)
        return httpx.Response(fixture.status, json=body, headers=fixture.headers)

    # This client has no network-capable fallback, proxy, or redirect handling.
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
    ) as client:
        return await client.request(
            operation["method"],
            f"https://{bundle['connector']}.mock.invalid{operation['path']}",
            json=(arguments or {}).get("body"),
        )
