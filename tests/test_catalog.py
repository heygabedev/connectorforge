import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from test_compiler import spec

from connectorforge.catalog import build_bundle, fingerprint, read_release, verify_bundle
from connectorforge.models import Audit, Release
from connectorforge.problems import Problem


def make_release(admin: TestClient, slug="inventory", version="1.0.0"):
    return admin.post(
        f"/api/v1/connectors/{slug}/releases",
        json={"version": version, "source": json.dumps(spec())},
    )


def test_content_hash_is_order_independent():
    one = build_bundle("inventory", "1.0.0", json.dumps(spec()))
    reordered = {key: spec()[key] for key in reversed(spec())}
    two = build_bundle("inventory", "1.0.0", json.dumps(reordered))
    assert fingerprint(one) == fingerprint(two)
    assert fingerprint({"x": 1, "y": 2}) == fingerprint({"y": 2, "x": 1})
    with pytest.raises(Problem):
        build_bundle("inventory", "latest", json.dumps(spec()))
    with pytest.raises(Problem, match="format"):
        verify_bundle({"format": 100}, "invalid")
    with pytest.raises(Problem, match="checksum"):
        verify_bundle(one, "invalid")


def test_catalog_versions_and_database_immutability(admin: TestClient):
    assert (
        admin.post(
            "/api/v1/connectors", json={"slug": "inventory", "name": "Inventory"}
        ).status_code
        == 201
    )
    assert (
        admin.post(
            "/api/v1/connectors", json={"slug": "inventory", "name": "Duplicate"}
        ).status_code
        == 409
    )
    result = make_release(admin)
    assert result.status_code == 201
    release_id = result.json()["id"]
    assert make_release(admin).json()["id"] == release_id
    changed = spec()
    changed["info"]["title"] = "Changed"
    assert (
        admin.post(
            "/api/v1/connectors/inventory/releases",
            json={"version": "1.0.0", "source": json.dumps(changed)},
        ).status_code
        == 409
    )
    assert len(admin.get("/api/v1/connectors/inventory/releases").json()) == 1
    bundle = admin.get(f"/api/v1/connectors/inventory/releases/{release_id}").json()
    assert fingerprint(bundle) == release_id
    db = admin.app.state.db
    for statement in [update(Release).values(version="2.0.0"), delete(Release)]:
        with pytest.raises(IntegrityError), db.transaction() as session:
            session.execute(statement)
    with db.transaction() as session:
        assert (
            len(list(session.scalars(select(Audit).where(Audit.action == "release.created")))) == 1
        )
    with pytest.raises(Problem):
        read_release(db, "another", release_id)


def test_catalog_validation_and_missing_records(admin: TestClient):
    assert (
        admin.post("/api/v1/connectors", json={"slug": "../bad", "name": "Bad"}).status_code == 422
    )
    assert make_release(admin, "missing").status_code == 404
    assert admin.get("/api/v1/connectors/missing/releases").status_code == 404
    assert admin.get("/api/v1/connectors/missing/releases/missing").status_code == 404


def test_scoped_credentials_filter_catalog(admin: TestClient):
    for slug in ("inventory", "billing"):
        admin.post("/api/v1/connectors", json={"slug": slug, "name": slug})
    token = admin.post(
        "/api/v1/keys",
        json={"name": "limited", "role": "viewer", "scopes": ["read"], "connectors": ["inventory"]},
    ).json()["token"]
    headers = {"authorization": "Bearer " + token}
    assert [item["slug"] for item in admin.get("/api/v1/connectors", headers=headers).json()] == [
        "inventory"
    ]
    assert admin.get("/api/v1/connectors/billing/releases", headers=headers).status_code == 403
    assert (
        admin.post(
            "/api/v1/connectors", headers=headers, json={"slug": "bad", "name": "Bad"}
        ).status_code
        == 403
    )
