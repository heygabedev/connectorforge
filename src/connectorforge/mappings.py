"""Bounded field selection and explicit value normalization."""

import copy
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from connectorforge.problems import Problem


class FieldMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    source: str = Field(max_length=512)
    transform: Literal["copy", "integer", "decimal", "timestamp"] = "copy"
    required: bool = True

    @field_validator("target")
    @classmethod
    def safe_target(cls, value: str) -> str:
        if value in {"constructor", "prototype"}:
            raise ValueError("Reserved mapping target")
        return value

    @field_validator("source")
    @classmethod
    def json_pointer(cls, value: str) -> str:
        if value and (not value.startswith("/") or re.search(r"~(?![01])", value)):
            raise ValueError("Use an RFC 6901 JSON Pointer")
        if value.count("/") > 32:
            raise ValueError("Pointer exceeds the nesting budget")
        return value


def validate_mappings(mappings: dict[str, list[FieldMapping]], operation_ids: set[str]) -> None:
    for operation, fields in mappings.items():
        if operation not in operation_ids or len(fields) > 64:
            raise Problem(422, "invalid_mapping", "Use a known operation and at most 64 fields.")
        if len({field.target for field in fields}) != len(fields):
            raise Problem(422, "invalid_mapping", "Mapping targets must be unique.")


def pointer(value: Any, path: str) -> tuple[bool, Any]:
    if not path:
        return True, value
    for raw in path[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif (
            isinstance(value, list)
            and re.fullmatch(r"0|[1-9][0-9]*", key)
            and int(key) < len(value)
        ):
            value = value[int(key)]
        else:
            return False, None
    return True, value


def transform(value: Any, kind: str) -> Any:
    if kind == "copy":
        return copy.deepcopy(value)
    try:
        if kind == "integer":
            if isinstance(value, bool) or not re.fullmatch(r"-?(0|[1-9][0-9]*)", str(value)):
                raise ValueError("Expected an integer")
            return int(value)
        if kind == "decimal":
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise ValueError("Expected an exact decimal string or integer")
            decimal = Decimal(value)
            if not decimal.is_finite() or abs(decimal.adjusted()) > 100:
                raise ValueError("Decimal exceeds the supported range")
            return format(decimal, "f")
        if kind == "timestamp":
            if not isinstance(value, str):
                raise ValueError("Expected an ISO timestamp")
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None:
                raise ValueError("An explicit timezone is required")
            return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, InvalidOperation, OverflowError) as error:
        raise Problem(502, "mapping_failed", "A response value cannot be normalized.") from error
    raise Problem(422, "invalid_mapping", "Unsupported transformation.")


def map_response(value: Any, fields: list[FieldMapping]) -> Any:
    if not fields:
        return copy.deepcopy(value)
    if isinstance(value, list):
        return [map_response(item, fields) for item in value]
    output = {}
    for field in fields:
        found, item = pointer(value, field.source)
        if not found:
            if field.required:
                raise Problem(
                    502, "mapping_failed", f"Required mapped field is absent: {field.target}."
                )
            continue
        output[field.target] = transform(item, field.transform)
    return output


def directional_schema(schema: dict[str, Any], *, request: bool) -> dict[str, Any]:
    """Exclude the opposite direction's fields, including its required entries."""
    output = copy.deepcopy(schema)
    if isinstance(output.get("properties"), dict):
        flag = "readOnly" if request else "writeOnly"
        removed = {name for name, value in output["properties"].items() if value.get(flag)}
        output["properties"] = {
            name: directional_schema(value, request=request)
            for name, value in output["properties"].items()
            if name not in removed
        }
        if "required" in output:
            output["required"] = [name for name in output["required"] if name not in removed]
        if removed:
            output["not"] = {"anyOf": [{"required": [name]} for name in sorted(removed)]}
    for key in ("items", "additionalProperties"):
        if isinstance(output.get(key), dict):
            output[key] = directional_schema(output[key], request=request)
    return output
