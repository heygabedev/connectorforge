"""Compile a bounded OpenAPI subset into data-only operation contracts."""

import json
import math
import re
from typing import Any, Never, cast

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from yaml.tokens import AliasToken, AnchorToken, TagToken

from connectorforge.problems import Problem

COMPILER_VERSION = "1"
MAX_NODES = 12000
MAX_DEPTH = 32
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
HEADER = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
    "format",
    "nullable",
    "readOnly",
    "writeOnly",
    "description",
    "title",
    "default",
    "example",
    "examples",
    "deprecated",
}


def fail(message: str) -> Never:
    raise Problem(422, "invalid_specification", message)


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail("Duplicate object keys are not supported.")
        result[key] = value
    return result


class StrictLoader(yaml.SafeLoader):
    pass


def yaml_mapping(loader: StrictLoader, node: yaml.MappingNode) -> dict[str, Any]:
    return unique_pairs(
        [(loader.construct_object(k), loader.construct_object(v)) for k, v in node.value]
    )


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def bounded(value: Any) -> None:
    remaining = MAX_NODES

    def visit(item: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if depth > MAX_DEPTH or remaining < 0:
            fail("Document exceeds the node or nesting budget.")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 256:
                    fail("Object keys must be strings of at most 256 characters.")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, str):
            if len(item) > 16384:
                fail("Strings must not exceed 16384 characters.")
        elif isinstance(item, float):
            if not math.isfinite(item):
                fail("Numbers must be finite.")
        elif item is not None and not isinstance(item, (int, bool)):
            fail("Only JSON-compatible values are supported.")

    visit(value, 0)


def parse_document(source: str) -> dict[str, Any]:
    if len(source.encode()) > 1024 * 1024:
        fail("Specification exceeds 1 MiB.")
    try:
        if source.lstrip().startswith(("{", "[")):
            document = json.loads(source, object_pairs_hook=unique_pairs)
        else:
            for token in yaml.scan(source):
                if isinstance(token, (AliasToken, AnchorToken, TagToken)):
                    fail("YAML aliases, anchors, and explicit tags are unsupported.")
            document = yaml.load(source, Loader=StrictLoader)
    except (ValueError, yaml.YAMLError, RecursionError, TypeError):
        fail("Cannot parse this specification as JSON or safe YAML.")
    bounded(document)
    if not isinstance(document, dict):
        fail("Specification must be an object.")
    return document


def resolve(document: dict[str, Any]) -> dict[str, Any]:
    count = 0

    def expand(item: Any, chain: tuple[str, ...] = (), depth: int = 0) -> Any:
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            fail("Expanded references exceed the compilation budget.")
        if isinstance(item, dict):
            if "$ref" in item:
                ref = item["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/components/"):
                    fail("References must point to local components.")
                if ref in chain or len(item) != 1:
                    fail("Recursive references and reference siblings are unsupported.")
                target: Any = document
                try:
                    for part in ref[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    fail("Reference target does not exist.")
                return expand(target, (*chain, ref), depth + 1)
            return {key: expand(value, chain, depth + 1) for key, value in item.items()}
        if isinstance(item, list):
            return [expand(child, chain, depth + 1) for child in item]
        return item

    result: dict[str, Any] = expand(document)
    return result


def schema_contract(schema: Any, version: str) -> dict[str, Any]:
    if not isinstance(schema, dict) or set(schema) - SCHEMA_KEYS:
        fail("Schema contains unsupported keywords. Combinators and patterns are unsupported.")
    output = dict(schema)
    if "nullable" in output:
        nullable = output.pop("nullable")
        if not version.startswith("3.0.") or not isinstance(nullable, bool):
            fail("nullable is supported only as an OpenAPI 3.0 boolean.")
        if nullable and "type" in output:
            output["type"] = [output["type"], "null"]
    for keyword, limit in (("maxLength", 16384), ("maxItems", 1000)):
        if keyword in output and (not isinstance(output[keyword], int) or output[keyword] > limit):
            fail(f"{keyword} exceeds the supported validation budget.")
    if output.get("format") not in {
        None,
        "date",
        "date-time",
        "uuid",
        "email",
        "int32",
        "int64",
        "float",
        "double",
    }:
        fail("Unsupported schema format.")
    if output.get("readOnly") and output.get("writeOnly"):
        fail("A field cannot be both readOnly and writeOnly.")
    if "properties" in output:
        if not isinstance(output["properties"], dict):
            fail("Schema properties must be an object.")
        output["properties"] = {
            key: schema_contract(value, version) for key, value in output["properties"].items()
        }
    for key in ("items", "additionalProperties"):
        if key in output and isinstance(output[key], dict):
            output[key] = schema_contract(output[key], version)
    if version.startswith("3.0."):
        for exclusive, inclusive in (
            ("exclusiveMinimum", "minimum"),
            ("exclusiveMaximum", "maximum"),
        ):
            if exclusive in output:
                flag = output.pop(exclusive)
                if not isinstance(flag, bool) or (flag and inclusive not in output):
                    fail("OpenAPI 3.0 exclusive bounds require a boolean and a numeric bound.")
                if flag:
                    output[exclusive] = output.pop(inclusive)
    try:
        Draft202012Validator.check_schema(output)
    except SchemaError:
        fail("Schema is not valid for the supported JSON Schema subset.")
    return output


def security_schemes(document: dict[str, Any]) -> dict[str, Any]:
    schemes = document.get("components", {}).get("securitySchemes", {})
    if not isinstance(schemes, dict):
        fail("Security schemes must be an object.")
    for name, scheme in schemes.items():
        if not IDENTIFIER.fullmatch(name) or not isinstance(scheme, dict):
            fail("Invalid security scheme name or definition.")
        kind = scheme.get("type")
        if kind == "http" and scheme.get("scheme") == "bearer":
            continue
        if (
            kind == "apiKey"
            and scheme.get("in") == "header"
            and HEADER.fullmatch(scheme.get("name", ""))
            and scheme["name"].lower()
            not in {
                "host",
                "cookie",
                "connection",
                "authorization",
                "content-length",
            }
        ):
            continue
        if kind == "oauth2":
            flows = scheme.get("flows", {})
            flow = flows.get("authorizationCode", {})
            if (
                set(flows) == {"authorizationCode"}
                and isinstance(flow.get("scopes"), dict)
                and all(isinstance(flow.get(key), str) for key in ("authorizationUrl", "tokenUrl"))
            ):
                continue
        fail("Supported authentication: bearer, header API key, and authorization-code OAuth.")
    return cast(dict[str, Any], schemes)


def security_requirements(value: Any, schemes: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 8:
        fail("Security must be a bounded array of alternatives.")
    for requirement in value:
        if not isinstance(requirement, dict):
            fail("Each security alternative must be an object.")
        for name, scopes in requirement.items():
            if name not in schemes or not isinstance(scopes, list):
                fail("Security requirement refers to an unknown scheme or invalid scopes.")
            allowed = schemes[name].get("flows", {}).get("authorizationCode", {}).get("scopes", {})
            if any(not isinstance(scope, str) or scope not in allowed for scope in scopes):
                fail("Security requirement contains unsupported scopes.")
    return value


def content_schema(content: Any, version: str) -> dict[str, Any]:
    if not isinstance(content, dict) or set(content) != {"application/json"}:
        fail("Only application/json content is supported.")
    return schema_contract(content["application/json"].get("schema", {}), version)


def compile_document(source: str) -> dict[str, Any]:
    try:
        return _compile(source)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        fail("Malformed specification structure.")


def _compile(source: str) -> dict[str, Any]:
    original = parse_document(source)
    document = resolve(original)
    version = document.get("openapi", "")
    if not isinstance(version, str) or not re.fullmatch(r"3\.[01]\.\d+", version):
        fail("Supported OpenAPI versions are 3.0.x and 3.1.x.")
    info, paths = document.get("info"), document.get("paths")
    if (
        not isinstance(info, dict)
        or not isinstance(info.get("title"), str)
        or not isinstance(info.get("version"), str)
    ):
        fail("info.title and info.version are required strings.")
    if not isinstance(paths, dict) or not paths:
        fail("At least one path is required.")
    if any(key in document for key in ("webhooks", "jsonSchemaDialect")):
        fail("Custom dialects and OpenAPI webhook definitions are unsupported.")
    schemes = security_schemes(document)
    inherited = security_requirements(document.get("security", []), schemes)
    operations: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    patterns: set[str] = set()
    for path, item in paths.items():
        if not re.fullmatch(r"/(?:[A-Za-z0-9_{}./-]*)", path) or ".." in path or "//" in path:
            fail("Paths must be safe, absolute URL paths without traversal.")
        pattern = re.sub(r"\{[^}]+\}", "{}", path)
        if pattern in patterns or not isinstance(item, dict):
            fail("Ambiguous path templates or invalid path item.")
        patterns.add(pattern)
        if set(item) - METHODS - {"parameters", "summary", "description", "servers"}:
            fail("Unsupported path-item field.")
        for method, operation in item.items():
            if method not in METHODS:
                continue
            if not isinstance(operation, dict) or "callbacks" in operation:
                fail("Invalid operation or unsupported callback.")
            operation_id = operation.get("operationId", "")
            if (
                not isinstance(operation_id, str)
                or not IDENTIFIER.fullmatch(operation_id)
                or operation_id in identifiers
            ):
                fail("Each operationId must be a unique ASCII identifier of at most 64 characters.")
            identifiers.add(operation_id)
            parameters: dict[tuple[str, str], dict[str, Any]] = {}
            for param in [*item.get("parameters", []), *operation.get("parameters", [])]:
                if not isinstance(param, dict) or param.get("in") not in {
                    "query",
                    "path",
                    "header",
                }:
                    fail("Only query, path, and header parameters are supported.")
                name, location = param.get("name", ""), param["in"]
                if not isinstance(name, str) or not HEADER.fullmatch(name):
                    fail("Parameter names must be safe identifiers.")
                if location == "header" and name.lower() in {
                    "host",
                    "authorization",
                    "cookie",
                    "content-length",
                    "connection",
                }:
                    fail("Reserved headers cannot be parameters.")
                expected_style = "form" if location == "query" else "simple"
                if param.get("style", expected_style) != expected_style or param.get(
                    "allowReserved"
                ):
                    fail("Unsupported parameter serialization.")
                schema = schema_contract(param.get("schema", {}), version)
                if not isinstance(schema.get("type"), str) or schema["type"] not in {
                    "string",
                    "integer",
                    "number",
                    "boolean",
                    "array",
                }:
                    fail("Parameters require primitive types or arrays.")
                if schema.get("type") == "array" and schema.get("items", {}).get("type") not in {
                    "string",
                    "integer",
                    "number",
                    "boolean",
                }:
                    fail("Array parameters require primitive items.")
                if location == "path" and not param.get("required"):
                    fail("Path parameters must be required.")
                parameters[(location, name)] = {**param, "schema": schema}
            path_names = set(re.findall(r"\{([A-Za-z0-9_]+)\}", path))
            if (
                path_names != {name for location, name in parameters if location == "path"}
                or path.count("{") != len(path_names)
                or path.count("}") != len(path_names)
            ):
                fail("Path template parameters must match declared path parameters.")
            responses = operation.get("responses")
            if not isinstance(responses, dict) or not responses:
                fail("Operations must declare responses.")
            normalized: dict[str, Any] = {}
            for status, response in responses.items():
                if not re.fullmatch(r"[1-5][0-9]{2}", str(status)) or not isinstance(
                    response, dict
                ):
                    fail("Responses require explicit HTTP status codes.")
                normalized[str(status)] = (
                    content_schema(response["content"], version) if "content" in response else None
                )
            body = operation.get("requestBody")
            if body is not None and not isinstance(body, dict):
                fail("Request bodies must be objects.")
            operations.append(
                {
                    "id": operation_id,
                    "method": method.upper(),
                    "path": path,
                    "parameters": list(parameters.values()),
                    "body": content_schema(body.get("content"), version) if body else None,
                    "body_required": bool(body and body.get("required")),
                    "responses": normalized,
                    "security": security_requirements(
                        operation.get("security", inherited), schemes
                    ),
                }
            )
    if not operations or len(operations) > 100:
        fail("Specifications must contain between 1 and 100 operations.")
    return {
        "compiler": COMPILER_VERSION,
        "title": info["title"],
        "source": original,
        "operations": operations,
        "security_schemes": schemes,
    }
