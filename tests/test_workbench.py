import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_compiler import spec

from connectorforge.catalog import build_bundle
from connectorforge.execution import (
    Arguments,
    PreviewInput,
    execute_preview,
    normalize_response,
    validate_arguments,
)
from connectorforge.fixtures import Fixture, validate_fixtures
from connectorforge.problems import Problem


def bundle():
    return build_bundle(
        "inventory", "1.0.0", json.dumps(spec()), fixtures={"listItems": {"body": ["demo"]}}
    )


def test_workbench_import_save_preview(admin: TestClient):
    admin.post("/api/v1/connectors", json={"slug": "inventory", "name": "Inventory"})
    response = admin.post(
        "/api/v1/connectors/inventory/releases",
        json={
            "version": "1.0.0",
            "source": json.dumps(spec()),
            "fixtures": {"listItems": {"body": ["sample"]}},
        },
    )
    assert response.status_code == 201
    release = response.json()["id"]
    path = f"/api/v1/connectors/inventory/releases/{release}/preview"
    preview = admin.post(path, json={"operation_id": "listItems"})
    assert preview.status_code == 200
    assert preview.json()["data"] == ["sample"]
    assert preview.json()["meta"]["release"] == release
    assert admin.post(path, json={"operation_id": "unknown"}).status_code == 404
    assert (
        admin.post(
            path, json={"operation_id": "listItems", "arguments": {"query": {"unknown": 1}}}
        ).status_code
        == 422
    )
    assert (
        admin.post(path, json={"operation_id": "listItems", "fault": "malformed"}).status_code
        == 502
    )


@pytest.mark.parametrize(
    "fault", ["malformed", "unavailable", "disconnect", "throttle", "schema_drift", "expired"]
)
async def test_fault_profiles_are_real_execution_failures(fault):
    with pytest.raises(Problem):
        await execute_preview(bundle(), PreviewInput(operation_id="listItems", fault=fault))


async def test_overall_deadline_bounds_in_process_transport():
    value = bundle()
    value["policy"]["deadline_ms"] = 10
    with pytest.raises(Problem) as error:
        await execute_preview(value, PreviewInput(operation_id="listItems", fault="timeout"))
    assert error.value.status == 504


async def test_missing_fixture_has_an_explicit_setup_error():
    value = bundle()
    value["fixtures"] = {}
    with pytest.raises(Problem) as error:
        await execute_preview(value, PreviewInput(operation_id="listItems"))
    assert error.value.code == "fixture_missing"


@pytest.mark.parametrize(
    "headers",
    [{"Set-Cookie": "bad"}, {"Retry-After": "0\r\nInjected: true"}, {"Retry-After": "x" * 129}],
)
def test_fixtures_cannot_set_sensitive_or_invalid_headers(headers):
    with pytest.raises(ValidationError):
        Fixture(headers=headers)


def test_fixture_bounds_and_declared_statuses():
    operation = bundle()["compiled"]["operations"]
    for fixture in [{"unknown": Fixture()}, {"listItems": Fixture(status=201)}]:
        with pytest.raises(Problem):
            validate_fixtures(fixture, operation)
    with pytest.raises(Problem):
        validate_fixtures(
            {"noContent": Fixture(status=204, body={})},
            [{"id": "noContent", "responses": {"204": None}}],
        )
    assert Fixture(headers={"Retry-After": "0"}).headers == {"Retry-After": "0"}


def test_response_contract_and_mapping_failures():
    value = bundle()
    operation = value["compiled"]["operations"][0]
    for response in [
        httpx.Response(201, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, content=b"bad"),
    ]:
        with pytest.raises(Problem):
            normalize_response(value, operation, response)
    operation["responses"]["204"] = None
    assert normalize_response(value, operation, httpx.Response(204)) is None
    with pytest.raises(Problem):
        normalize_response(value, operation, httpx.Response(204, content=b"bad"))
    operation["responses"]["200"] = None
    assert normalize_response(value, operation, httpx.Response(200)) is None
    with pytest.raises(Problem):
        normalize_response(value, operation, httpx.Response(200, json={}))


def test_required_request_body_and_read_only_fields():
    operation = {
        "parameters": [],
        "body_required": True,
        "body": {
            "type": "object",
            "required": ["name", "id"],
            "properties": {"name": {"type": "string"}, "id": {"type": "integer", "readOnly": True}},
        },
    }
    with pytest.raises(Problem):
        validate_arguments(operation, Arguments())
    validate_arguments(operation, Arguments(body={"name": "Demo"}))
    with pytest.raises(Problem):
        validate_arguments(operation, Arguments(body={"name": "Demo", "id": 1}))
    operation["body"] = None
    with pytest.raises(Problem):
        validate_arguments(operation, Arguments(body=None))


def test_timestamp_formats_are_enforced_in_both_directions():
    value = bundle()
    operation = value["compiled"]["operations"][0]
    schema = {"type": "string", "format": "date-time"}
    operation["body"], operation["body_required"] = schema, True
    operation["responses"]["200"] = schema
    with pytest.raises(Problem, match="Request body"):
        validate_arguments(operation, Arguments(body="not-a-timestamp"))
    with pytest.raises(Problem, match="declared schema"):
        normalize_response(value, operation, httpx.Response(200, json="not-a-timestamp"))
    validate_arguments(operation, Arguments(body="2026-10-15T10:00:00Z"))
    assert (
        normalize_response(value, operation, httpx.Response(200, json="2026-10-15T10:00:00+02:00"))
        == "2026-10-15T10:00:00+02:00"
    )
