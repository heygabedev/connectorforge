"""Contract validation shared by previews and connector invocation."""

import asyncio
import time
from typing import Any

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field

from connectorforge.compiler import bounded
from connectorforge.fixtures import Fault, dispatch_fixture
from connectorforge.mappings import FieldMapping, directional_schema, map_response
from connectorforge.problems import Problem


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: dict[str, Any] = Field(default_factory=dict, max_length=50)
    query: dict[str, Any] = Field(default_factory=dict, max_length=50)
    header: dict[str, Any] = Field(default_factory=dict, max_length=50)
    body: Any = None


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=64)
    arguments: Arguments = Field(default_factory=Arguments)
    fault: Fault = "none"


def operation_by_id(bundle: dict[str, Any], operation_id: str) -> dict[str, Any]:
    for operation in bundle["compiled"]["operations"]:
        if operation["id"] == operation_id:
            return dict(operation)
    raise Problem(404, "operation_missing", "Operation does not exist in this release.")


def validate_arguments(operation: dict[str, Any], arguments: Arguments) -> None:
    bounded(arguments.model_dump())
    for location in ("path", "query", "header"):
        declared = {
            param["name"]: param for param in operation["parameters"] if param["in"] == location
        }
        values: dict[str, Any] = getattr(arguments, location)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {name: param["schema"] for name, param in declared.items()},
            "required": [name for name, param in declared.items() if param.get("required")],
        }
        if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(values):
            raise Problem(
                422, "invalid_arguments", f"Invalid {location} parameters for this operation."
            )
    supplied = "body" in arguments.model_fields_set
    if not supplied and operation["body_required"]:
        raise Problem(422, "invalid_arguments", "This operation requires a request body.")
    if supplied:
        schema = operation["body"]
        if schema is None or not Draft202012Validator(
            directional_schema(schema, request=True), format_checker=FormatChecker()
        ).is_valid(arguments.body):
            raise Problem(
                422, "invalid_arguments", "Request body does not match the operation contract."
            )


def normalize_response(
    bundle: dict[str, Any], operation: dict[str, Any], response: httpx.Response
) -> Any:
    if response.status_code >= 400:
        raise Problem(502, "provider_error", f"Mock provider returned HTTP {response.status_code}.")
    if str(response.status_code) not in operation["responses"]:
        raise Problem(502, "response_contract", "Provider returned an undeclared status.")
    if operation["method"] == "HEAD" or response.status_code == 204:
        if response.content:
            raise Problem(502, "response_contract", "Unexpected body in a no-content response.")
        return None
    schema = operation["responses"][str(response.status_code)]
    if schema is None:
        if response.content:
            raise Problem(502, "response_contract", "The response contract declares no body.")
        return None
    try:
        value = response.json()
    except ValueError as error:
        raise Problem(502, "response_contract", "Provider returned malformed JSON.") from error
    bounded(value)
    if not Draft202012Validator(
        directional_schema(schema, request=False), format_checker=FormatChecker()
    ).is_valid(value):
        raise Problem(
            502, "response_contract", "Provider response does not match its declared schema."
        )
    fields = [
        FieldMapping.model_validate(field) for field in bundle["mappings"].get(operation["id"], [])
    ]
    return map_response(value, fields)


async def execute_preview(bundle: dict[str, Any], data: PreviewInput) -> dict[str, Any]:
    operation = operation_by_id(bundle, data.operation_id)
    started = time.perf_counter()
    validate_arguments(operation, data.arguments)
    try:
        async with asyncio.timeout(bundle["policy"]["deadline_ms"] / 1000):
            response = await dispatch_fixture(
                bundle,
                operation,
                fault=data.fault,
                arguments=data.arguments.model_dump(exclude_unset=True),
            )
            value = normalize_response(bundle, operation, response)
    except TimeoutError as error:
        raise Problem(
            504, "deadline_exceeded", "Operation exceeded its overall deadline."
        ) from error
    except httpx.TransportError as error:
        raise Problem(
            502, "provider_transport", "Mock transport could not complete the request."
        ) from error
    return {
        "data": value,
        "meta": {
            "operation": operation["id"],
            "status": response.status_code,
            "version": bundle["version"],
            "isolated": True,
            "attempts": 1,
        },
        "timeline": [
            {"stage": "validation", "status": "passed"},
            {"stage": "mock_dispatch", "status": "passed"},
            {
                "stage": "normalization",
                "status": "passed",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        ],
    }
