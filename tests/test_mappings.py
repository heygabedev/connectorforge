from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from connectorforge.mappings import (
    FieldMapping,
    directional_schema,
    map_response,
    pointer,
    transform,
    validate_mappings,
)
from connectorforge.problems import Problem


def test_missing_null_and_zero_are_distinct():
    fields = [
        FieldMapping(target="amount", source="/amount"),
        FieldMapping(target="optional", source="/absent", required=False),
    ]
    assert map_response({"amount": 0}, fields) == {"amount": 0}
    assert map_response({"amount": None}, fields) == {"amount": None}
    with pytest.raises(Problem, match="absent"):
        map_response({}, fields)


def test_json_pointers_escape_and_array_indices():
    data = {"a/b": {"~": ["value"]}}
    assert pointer(data, "/a~1b/~0/0") == (True, "value")
    assert pointer(data, "") == (True, data)
    for path in ["/missing", "/a~1b/~0/1", "/a~1b/~0/00", "/a~1b/~0/-1", "/a~1b/~0/text"]:
        assert pointer(data, path) == (False, None)


@pytest.mark.parametrize(
    "data",
    [
        {"source": "not-a-pointer"},
        {"source": "/bad~2escape"},
        {"source": "/" * 33},
        {"target": "constructor"},
        {"target": "__proto__"},
        {"target": "prototype"},
        {"transform": "eval"},
    ],
)
def test_unsafe_mapping_definitions_are_rejected(data):
    with pytest.raises(ValidationError):
        FieldMapping.model_validate({"source": "/id", "target": "id", **data})


def test_mapping_targets_and_operations_must_be_unambiguous():
    field = FieldMapping(source="/id", target="id")
    for mappings in [{"unknown": [field]}, {"list": [field, field]}, {"list": [field] * 65}]:
        with pytest.raises(Problem):
            validate_mappings(mappings, {"list"})
    validate_mappings({"list": [field]}, {"list"})


def test_exact_money_and_timezone_normalization():
    fields = [
        FieldMapping(target="amount", source="/price", transform="decimal"),
        FieldMapping(target="created", source="/created", transform="timestamp"),
        FieldMapping(target="count", source="/count", transform="integer"),
    ]
    assert map_response(
        {"price": "123456789.0100", "created": "2026-10-15T10:00:00+02:00", "count": "0"}, fields
    ) == {"amount": "123456789.0100", "created": "2026-10-15T08:00:00Z", "count": 0}


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (True, "integer"),
        ("1.5", "integer"),
        ("01", "integer"),
        (0.1, "decimal"),
        (True, "decimal"),
        ("NaN", "decimal"),
        ("1e999", "decimal"),
        ("bad", "decimal"),
        (4, "timestamp"),
        ("2026-10-15", "timestamp"),
        ("bad", "timestamp"),
        (None, "timestamp"),
        (1, "unsupported"),
    ],
)
def test_invalid_values_fail_without_echoing_response_data(value, kind):
    with pytest.raises(Problem):
        transform(value, kind)


def test_mappings_do_not_mutate_inputs():
    data = [{"a": {"value": 1}}, {"a": {"value": 2}}]
    mapped = map_response(data, [FieldMapping(target="copy", source="/a")])
    mapped[0]["copy"]["value"] = 100
    assert data[0]["a"]["value"] == 1
    assert map_response(data, []) == data


@given(st.integers(min_value=-(10**20), max_value=10**20))
def test_integer_and_decimal_normalization_is_exact(value):
    assert transform(str(value), "integer") == value
    assert Decimal(transform(value, "decimal")) == value


def test_directional_fields_are_removed_and_forbidden():
    demo_value = "example"
    schema = {
        "type": "object",
        "required": ["id", "secret", "name"],
        "properties": {
            "id": {"type": "string", "readOnly": True},
            "secret": {"type": "string", "writeOnly": True},
            "name": {"type": "string"},
        },
        "additionalProperties": {"type": "string"},
    }
    request = Draft202012Validator(directional_schema(schema, request=True))
    response = Draft202012Validator(directional_schema(schema, request=False))
    assert request.is_valid({"secret": demo_value, "name": "Demo"})
    assert not request.is_valid({"id": "1", "secret": demo_value, "name": "Demo"})
    assert response.is_valid({"id": "1", "name": "Demo"})
    assert not response.is_valid({"id": "1", "name": "Demo", "secret": demo_value})
    nested = directional_schema({"type": "array", "items": schema}, request=True)
    assert "id" not in nested["items"]["properties"]
    assert "id" in schema["properties"]
