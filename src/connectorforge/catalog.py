"""Content-addressed bundles and connector catalog operations."""

import hashlib
import json
import re
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from connectorforge.compiler import compile_document
from connectorforge.database import Database
from connectorforge.execution import PreviewInput, execute_preview
from connectorforge.fixtures import Fixture, validate_fixtures
from connectorforge.mappings import FieldMapping, validate_mappings
from connectorforge.models import Audit, Connector, Release
from connectorforge.problems import Problem
from connectorforge.security import Principal, require

BUNDLE_FORMAT = 1
VERSION = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\Z")
router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class ConnectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")
    name: str = Field(min_length=1, max_length=100)


class ReleaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(max_length=32)
    source: str = Field(max_length=1024 * 1024)
    mappings: dict[str, list[FieldMapping]] = Field(default_factory=dict, max_length=100)
    fixtures: dict[str, Fixture] = Field(default_factory=dict, max_length=100)


def build_bundle(
    slug: str,
    version: str,
    source: str,
    *,
    fixtures: dict[str, Any] | None = None,
    mappings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not VERSION.fullmatch(version):
        raise Problem(
            422, "invalid_version", "Use a stable semantic version with major 1 or later."
        )
    compiled = compile_document(source)
    parsed_fixtures = {
        key: Fixture.model_validate(value) for key, value in (fixtures or {}).items()
    }
    validate_fixtures(parsed_fixtures, compiled["operations"])
    parsed_mappings = {
        key: [FieldMapping.model_validate(field) for field in fields]
        for key, fields in (mappings or {}).items()
    }
    validate_mappings(parsed_mappings, {operation["id"] for operation in compiled["operations"]})
    return {
        "format": BUNDLE_FORMAT,
        "connector": slug,
        "version": version,
        "compiled": compiled,
        "fixtures": {key: value.model_dump() for key, value in parsed_fixtures.items()},
        "mappings": {
            key: [field.model_dump() for field in fields] for key, fields in parsed_mappings.items()
        },
        "policy": {"version": "1", "deadline_ms": 3000, "attempts": 3},
    }


def verify_bundle(bundle: dict[str, Any], expected_hash: str) -> None:
    if bundle.get("format") != BUNDLE_FORMAT:
        raise Problem(409, "incompatible_bundle", "Unsupported connector bundle format.")
    if fingerprint(bundle) != expected_hash:
        raise Problem(409, "corrupt_bundle", "Connector checksum verification failed.")


def store_release(db: Database, bundle: dict[str, Any], actor: str) -> str:
    release_id = fingerprint(bundle)
    try:
        with db.transaction() as session:
            if session.get(Connector, bundle["connector"]) is None:
                raise Problem(404, "not_found", "Connector does not exist.")
            existing = session.scalar(
                select(Release).where(
                    Release.connector == bundle["connector"],
                    Release.version == bundle["version"],
                )
            )
            if existing:
                if existing.id != release_id:
                    raise Problem(
                        409, "version_exists", "This version already identifies another bundle."
                    )
                return release_id
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(Release)
                    .where(
                        Release.connector == bundle["connector"],
                    )
                )
                or 0
            )
            if count >= 100:
                raise Problem(429, "catalog_full", "This connector has reached its release limit.")
            session.add(
                Release(
                    id=release_id,
                    connector=bundle["connector"],
                    version=bundle["version"],
                    major=int(bundle["version"].split(".")[0]),
                    bundle=bundle,
                )
            )
            session.add(
                Audit(
                    actor=actor,
                    action="release.created",
                    resource=release_id,
                    detail={"connector": bundle["connector"], "version": bundle["version"]},
                )
            )
    except IntegrityError as error:
        raise Problem(
            409, "catalog_conflict", "A concurrent update changed this version."
        ) from error
    return release_id


def read_release(db: Database, slug: str, release_id: str) -> Release:
    with db.transaction() as session:
        release = session.get(Release, release_id)
        if release is None or release.connector != slug:
            raise Problem(404, "not_found", "Release does not exist for this connector.")
        verify_bundle(release.bundle, release.id)
        return release


def visible(principal: Principal, slug: str) -> bool:
    return "*" in principal.connectors or slug in principal.connectors


@router.get("")
def list_connectors(request: Request) -> list[dict[str, Any]]:
    principal = require(request)
    with request.app.state.db.transaction() as session:
        return [
            {"slug": row.slug, "name": row.name, "paused": row.paused, "generation": row.generation}
            for row in session.scalars(select(Connector).order_by(Connector.created_at))
            if visible(principal, row.slug)
        ]


@router.post("", status_code=201)
def create_connector(data: ConnectorInput, request: Request) -> dict[str, str]:
    principal = require(request, "manage", data.slug)
    try:
        with request.app.state.db.transaction() as session:
            if session.scalar(select(func.count()).select_from(Connector)) >= 50:
                raise Problem(429, "catalog_full", "Workspace limit: 50 connectors.")
            session.add(Connector(slug=data.slug, name=data.name))
            session.add(Audit(actor=principal.id, action="connector.created", resource=data.slug))
    except IntegrityError as error:
        raise Problem(
            409, "connector_exists", "Choose a different connector identifier."
        ) from error
    return {"slug": data.slug, "name": data.name}


@router.get("/{slug}/releases")
def list_releases(slug: str, request: Request) -> list[dict[str, Any]]:
    require(request, connector=slug)
    with request.app.state.db.transaction() as session:
        if session.get(Connector, slug) is None:
            raise Problem(404, "not_found", "Connector does not exist.")
        return [
            {
                "id": row.id,
                "version": row.version,
                "major": row.major,
                "created_at": row.created_at,
                "operations": len(row.bundle["compiled"]["operations"]),
            }
            for row in session.scalars(
                select(Release).where(Release.connector == slug).order_by(Release.created_at.desc())
            )
        ]


@router.post("/{slug}/releases", status_code=201)
def create_release(slug: str, data: ReleaseInput, request: Request) -> dict[str, str]:
    principal = require(request, "manage", slug)
    release_id = store_release(
        request.app.state.db,
        build_bundle(
            slug,
            data.version,
            data.source,
            mappings={
                key: [field.model_dump() for field in fields]
                for key, fields in data.mappings.items()
            },
            fixtures={key: fixture.model_dump() for key, fixture in data.fixtures.items()},
        ),
        principal.id,
    )
    return {"id": release_id, "version": data.version}


@router.get("/{slug}/releases/{release_id}")
def get_release(slug: str, release_id: str, request: Request) -> dict[str, Any]:
    require(request, connector=slug)
    return read_release(request.app.state.db, slug, release_id).bundle


@router.post("/{slug}/releases/{release_id}/preview")
async def preview(
    slug: str, release_id: str, data: PreviewInput, request: Request
) -> dict[str, Any]:
    require(request, "invoke", slug, data.operation_id)
    release = read_release(request.app.state.db, slug, release_id)
    result = await execute_preview(release.bundle, data)
    result["meta"]["release"] = release_id
    return result
