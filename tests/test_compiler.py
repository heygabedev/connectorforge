import copy
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from connectorforge.compiler import (
    bounded,
    compile_document,
    content_schema,
    parse_document,
    resolve,
    schema_contract,
    security_requirements,
    security_schemes,
)
from connectorforge.problems import Problem


def spec():
    return {
        "openapi": "3.1.0",
        "info": {"title": "Inventory", "version": "1.0.0"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "array", "items": {"type": "string"}}
                                }
                            }
                        },
                    },
                }
            }
        },
    }


def compile_spec(document):
    return compile_document(json.dumps(document))


def test_valid_json_and_yaml_compile_to_equivalent_contracts():
    import yaml

    document = spec()
    assert compile_spec(document) == compile_document(yaml.safe_dump(document))
    assert compile_spec(document)["operations"][0]["responses"]["200"]["type"] == "array"


@pytest.mark.parametrize(
    "source",
    [
        "",
        "[]",
        "{}",
        "{bad",
        "a: &anchor {x: 1}\nb: *anchor",
        "!!python/object/apply:os.system [echo unsafe]",
        "x: 1\nx: 2",
        '{"x": 1, "x": 2}',
        "date: 2020-01-01",
        "true: value",
        "long: " + "x" * 16385,
        '{"x": NaN}',
        "a: " + "[1, " * 40 + "1" + "]" * 40,
        " " * (1024 * 1024 + 1),
    ],
    ids=lambda source: f"input-{len(source)}",
)
def test_unsafe_or_invalid_input_is_rejected(source):
    with pytest.raises(Problem):
        compile_document(source)


def test_reference_resolution_and_escaping():
    document = spec()
    document["components"] = {"schemas": {"a/b": {"type": "string"}}}
    document["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ] = {"$ref": "#/components/schemas/a~1b"}
    assert compile_spec(document)["operations"][0]["responses"]["200"] == {"type": "string"}


@pytest.mark.parametrize(
    "reference",
    [
        {"$ref": "https://example.com/schema"},
        {"$ref": "file:///etc/passwd"},
        {"$ref": "#/components/missing"},
        {"$ref": 3},
        {"$ref": "#/components/schemas/A", "description": "sibling"},
    ],
)
def test_unsupported_references(reference):
    with pytest.raises(Problem):
        resolve({"components": {"schemas": {"A": {"type": "string"}}}, "x": reference})


def test_circular_and_excessive_expansion_is_rejected():
    with pytest.raises(Problem):
        resolve({"components": {"schemas": {"A": {"$ref": "#/components/schemas/A"}}}})
    with pytest.raises(Problem):
        bounded([None] * 12001)
    with pytest.raises(Problem):
        bounded({"x" * 257: True})
    with pytest.raises(Problem):
        resolve(
            {
                "components": {"schemas": {"A": list(range(6000))}},
                "one": {"$ref": "#/components/schemas/A"},
                "two": {"$ref": "#/components/schemas/A"},
            }
        )


@pytest.mark.parametrize(
    "schema",
    [
        True,
        {"pattern": "(a+)+$"},
        {"oneOf": []},
        {"nullable": True},
        {"type": "unknown"},
        {"maxLength": 20000},
        {"maxItems": 2000},
        {"maxItems": "large"},
        {"format": "binary"},
        {"readOnly": True, "writeOnly": True},
        {"properties": []},
        {"items": {"pattern": "unsafe"}},
        {"additionalProperties": {"type": "unknown"}},
    ],
)
def test_schema_subset_rejects_unsupported_behavior(schema):
    with pytest.raises(Problem):
        schema_contract(schema, "3.1.0")


def test_openapi_30_conversion_preserves_constraints():
    assert schema_contract({"type": "integer", "nullable": True}, "3.0.3")["type"] == [
        "integer",
        "null",
    ]
    assert schema_contract({"nullable": False}, "3.0.3") == {}
    assert schema_contract({"nullable": True}, "3.0.3") == {}
    assert schema_contract({"minimum": 1, "exclusiveMinimum": True}, "3.0.3") == {
        "exclusiveMinimum": 1
    }
    assert schema_contract({"maximum": 2, "exclusiveMaximum": False}, "3.0.3") == {"maximum": 2}
    assert (
        schema_contract(
            {"additionalProperties": {"type": "string"}, "properties": {"id": {"type": "integer"}}},
            "3.1.0",
        )["properties"]["id"]["type"]
        == "integer"
    )
    for invalid in [{"exclusiveMinimum": True}, {"exclusiveMaximum": 4}, {"nullable": "yes"}]:
        with pytest.raises(Problem):
            schema_contract(invalid, "3.0.3")


SCHEMES = {
    "Bearer": {"type": "http", "scheme": "bearer"},
    "ApiKey": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
    "OAuth": {
        "type": "oauth2",
        "flows": {
            "authorizationCode": {
                "authorizationUrl": "https://mock.invalid/authorize",
                "tokenUrl": "https://mock.invalid/token",
                "scopes": {"read": "Read items"},
            }
        },
    },
}


def test_security_preserves_and_or_and_operation_overrides():
    document = spec()
    document["components"] = {"securitySchemes": SCHEMES}
    alternatives = [{"Bearer": [], "ApiKey": []}, {"OAuth": ["read"]}, {}]
    document["security"] = alternatives
    assert compile_spec(document)["operations"][0]["security"] == alternatives
    document["paths"]["/items"]["get"]["security"] = []
    assert compile_spec(document)["operations"][0]["security"] == []


@pytest.mark.parametrize(
    "schemes",
    [
        [],
        {"bad-name": {}},
        {"Bad": []},
        {"Bad": {"type": "http", "scheme": "basic"}},
        {"Bad": {"type": "apiKey", "in": "header", "name": "Host"}},
        {"Bad": {"type": "oauth2", "flows": {}}},
        {"Bad": {"type": "oauth2", "flows": {"authorizationCode": {"scopes": {}}}}},
    ],
)
def test_unsupported_authentication(schemes):
    with pytest.raises(Problem):
        security_schemes({"components": {"securitySchemes": schemes}})


@pytest.mark.parametrize(
    "requirements",
    [None, [{}] * 9, [1], [{"Missing": []}], [{"Bearer": "bad"}], [{"OAuth": ["write"]}]],
)
def test_invalid_security_requirements(requirements):
    with pytest.raises(Problem):
        security_requirements(requirements, SCHEMES)


@pytest.mark.parametrize(
    "content", [None, {}, {"text/plain": {}}, {"application/json": {}, "text/plain": {}}]
)
def test_unsupported_content_types(content):
    with pytest.raises(Problem):
        content_schema(content, "3.1.0")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("openapi", 3),
        ("openapi", "3.2.0"),
        ("info", {}),
        ("paths", {}),
        ("paths", []),
        ("webhooks", {}),
    ],
)
def test_invalid_root(field, value):
    document = spec()
    document[field] = value
    with pytest.raises(Problem):
        compile_spec(document)


@pytest.mark.parametrize(
    ("path", "item"),
    [
        ("../items", {}),
        ("/../items", {}),
        ("//items", {}),
        ("/items", []),
        ("/items", {"trace": {}}),
        ("/items", {"get": []}),
        ("/items", {"get": {"callbacks": {}}}),
        ("/items", {"get": {"operationId": "bad-id"}}),
        ("/items", {"get": {"operationId": 4}}),
        ("/items", {"description": "No operations"}),
    ],
)
def test_invalid_paths_and_operations(path, item):
    document = spec()
    document["paths"] = {path: item}
    with pytest.raises(Problem):
        compile_spec(document)


@pytest.mark.parametrize(
    "parameter",
    [
        [],
        {"in": "cookie"},
        {"in": "query", "name": "bad name"},
        {"in": "header", "name": "Host"},
        {"in": "query", "name": "q", "style": "deepObject"},
        {"in": "query", "name": "q", "allowReserved": True},
        {"in": "query", "name": "q", "schema": {"type": "object"}},
        {"in": "query", "name": "q", "schema": {"type": "array", "items": {"type": "object"}}},
        {"in": "path", "name": "id", "schema": {"type": "string"}},
    ],
)
def test_invalid_parameters(parameter):
    document = spec()
    document["paths"]["/items"]["get"]["parameters"] = [parameter]
    with pytest.raises(Problem):
        compile_spec(document)


def test_parameters_body_and_response_contract():
    document = spec()
    operation = document["paths"].pop("/items")["get"]
    operation["parameters"] = [
        {"in": "path", "name": "id", "required": True, "schema": {"type": "string"}},
        {"in": "query", "name": "tags", "schema": {"type": "array", "items": {"type": "string"}}},
    ]
    operation["requestBody"] = {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    }
    operation["responses"]["204"] = {"description": "No content"}
    document["paths"]["/items/{id}"] = {"parameters": [], "post": operation}
    compiled = compile_spec(document)["operations"][0]
    assert compiled["body_required"]
    assert compiled["responses"]["204"] is None
    document["paths"]["/items/{id}"]["post"]["parameters"] = []
    with pytest.raises(Problem):
        compile_spec(document)


def test_duplicate_identifiers_and_ambiguous_templates():
    document = spec()
    document["paths"]["/other"] = copy.deepcopy(document["paths"]["/items"])
    with pytest.raises(Problem):
        compile_spec(document)
    document["paths"] = {"/{a}": {"summary": "One"}, "/{b}": {"summary": "Two"}}
    with pytest.raises(Problem):
        compile_spec(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("responses", {}),
        ("responses", {"default": {}}),
        ("responses", {"200": []}),
        ("requestBody", []),
        ("parameters", 4),
    ],
)
def test_invalid_operation_shapes(field, value):
    document = spec()
    document["paths"]["/items"]["get"][field] = value
    with pytest.raises(Problem):
        compile_spec(document)


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(max_size=20),
        lambda child: (
            st.lists(child, max_size=4) | st.dictionaries(st.text(max_size=10), child, max_size=4)
        ),
        max_leaves=20,
    )
)
def test_arbitrary_json_never_escapes_as_an_unhandled_exception(value):
    try:
        result = compile_document(json.dumps(value))
    except Problem as error:
        assert error.status == 422
    else:
        assert result["operations"]


def test_bounded_json_primitives():
    bounded({"values": [True, False, None, 3, 1.5, "text"]})
    assert parse_document('{"answer":42}') == {"answer": 42}
