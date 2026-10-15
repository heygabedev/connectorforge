import copy
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_compiler import spec
from typer.testing import CliRunner

from connectorforge.catalog import build_bundle, store_release
from connectorforge.cli import app as cli
from connectorforge.config import Settings
from connectorforge.database import Database
from connectorforge.evaluation import EvaluationInput, evaluate
from connectorforge.models import AccessToken, Connector, Job
from connectorforge.problems import Problem
from connectorforge.worker import Supervisor, check_actor


def definition():
    return EvaluationInput.model_validate_json(
        (Path(__file__).parent / "data/initial-golden.json").read_text()
    )


def prepared(admin: TestClient):
    db = admin.app.state.db
    with db.transaction() as session:
        session.add(Connector(slug="inventory", name="Inventory"))
        actor = session.scalar(select(AccessToken)).id
    bundle = build_bundle(
        "inventory", "1.0.0", json.dumps(spec()), fixtures={"listItems": {"body": ["sample"]}}
    )
    release = store_release(db, bundle, actor)
    return db, actor, bundle, release


def wait_job(client: TestClient, job_id: str):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.05)
    pytest.fail("Evaluation did not finish")


async def test_evaluation_is_deterministic_and_expectations_are_independent():
    bundle = build_bundle(
        "inventory", "1.0.0", json.dumps(spec()), fixtures={"listItems": {"body": ["sample"]}}
    )
    first = await evaluate(bundle, definition())
    assert first["passed"]
    assert first == await evaluate(bundle, definition())
    broken = copy.deepcopy(bundle)
    broken["fixtures"]["listItems"]["body"] = ["regression"]
    second = await evaluate(broken, definition())
    assert not second["passed"]
    assert not second["cases"][0]["passed"]
    assert first["fingerprint"] != second["fingerprint"]
    assert "regression" not in json.dumps(second)
    duplicate = definition()
    duplicate.cases.append(duplicate.cases[0])
    with pytest.raises(Problem, match="unique"):
        await evaluate(bundle, duplicate)


def test_jobs_run_in_supervised_processes_and_persist_results(admin: TestClient):
    _, _, _, release = prepared(admin)
    response = admin.post(
        f"/api/v1/connectors/inventory/releases/{release}/evaluations",
        json=definition().model_dump(exclude_unset=True),
    )
    assert response.status_code == 202
    job = wait_job(admin, response.json()["id"])
    assert job["status"] == "passed"
    assert job["attempts"] == 1
    assert job["result"]["context"]["bundle"] == release
    assert "payload" not in job
    assert admin.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 409
    assert len(admin.get("/api/v1/jobs").json()) == 1
    assert admin.get("/api/v1/jobs/unknown").status_code == 404
    assert admin.post("/api/v1/jobs/unknown/cancel").status_code == 404


def test_submission_rejects_duplicate_cases_and_unknown_operations(admin: TestClient):
    _, _, _, release = prepared(admin)
    value = definition().model_dump(exclude_unset=True)
    value["cases"].append(value["cases"][0])
    endpoint = f"/api/v1/connectors/inventory/releases/{release}/evaluations"
    assert admin.post(endpoint, json=value).status_code == 422
    value = definition().model_dump(exclude_unset=True)
    value["cases"][0]["input"]["operation_id"] = "unknown"
    assert admin.post(endpoint, json=value).status_code == 404


def test_running_evaluation_can_be_cancelled_without_late_overwrite(admin: TestClient):
    _, _, _, release = prepared(admin)
    value = definition().model_dump(exclude_unset=True)
    value["cases"][0]["input"]["fault"] = "timeout"
    response = admin.post(
        f"/api/v1/connectors/inventory/releases/{release}/evaluations", json=value
    )
    job_id = response.json()["id"]
    assert admin.post(f"/api/v1/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    time.sleep(0.2)
    assert admin.get(f"/api/v1/jobs/{job_id}").json()["status"] == "cancelled"


def test_revoked_job_credentials_are_rejected(admin: TestClient):
    db, actor, _, _ = prepared(admin)
    with db.transaction() as session:
        session.get(AccessToken, actor).revoked = True
    with pytest.raises(Problem, match="current"):
        check_actor(db, actor, "evaluate", "inventory")


async def test_worker_deadline_and_capacity_are_bounded(admin: TestClient):
    db, actor, _, _ = prepared(admin)
    worker = Supervisor(db)
    worker.waiting = 8
    with pytest.raises(Problem) as error:
        await worker.execute("compile", {}, actor, "inventory")
    assert error.value.status == 429
    worker.waiting, worker.seconds = 0, 0.001
    with pytest.raises(Problem) as error:
        await worker.execute(
            "compile",
            {"slug": "inventory", "version": "1.0.0", "source": json.dumps(spec())},
            actor,
            "inventory",
        )
    assert error.value.code == "worker_deadline"
    assert worker.waiting == 0
    assert not worker.context.active_children()


def test_abandoned_job_lease_is_reclaimed_but_paused_jobs_stay_paused(tmp_path: Path):
    db = Database(Settings(tmp_path))
    db.initialize()
    actor = "test"
    with db.transaction() as session:
        old = Job(
            kind="evaluation",
            actor=actor,
            payload={},
            status="running",
            lease="old",
            lease_until=0,
        )
        paused = Job(kind="evaluation", actor=actor, payload={}, status="paused")
        unrelated = Job(kind="webhook", actor=actor, payload={}, status="running", lease_until=0)
        session.add_all([old, paused, unrelated])
        session.flush()
        old_id, paused_id, unrelated_id = old.id, paused.id, unrelated.id
    worker = Supervisor(db)
    claimed = worker.claim()
    assert claimed and claimed[0] == old_id and claimed[1] != "old"
    with db.transaction() as session:
        assert session.get(Job, old_id).status == "running"
        assert session.get(Job, paused_id).status == "paused"
        assert session.get(Job, unrelated_id).status == "running"
    db.close()


def test_job_visibility_and_queue_limits(admin: TestClient):
    db, _, _, release = prepared(admin)
    token = admin.post("/api/v1/keys", json={"name": "other"}).json()["token"]
    with db.transaction() as session:
        for _ in range(32):
            session.add(Job(kind="reserved", actor="different", payload={"connector": "billing"}))
    response = admin.post(
        f"/api/v1/connectors/inventory/releases/{release}/evaluations",
        json=definition().model_dump(exclude_unset=True),
    )
    assert response.status_code == 429
    assert admin.get("/api/v1/jobs", headers={"authorization": "Bearer " + token}).json() == []


def test_cli_runs_evaluations_through_authenticated_api(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _, _, _, release = prepared(admin)
    token = admin.post(
        "/api/v1/keys", json={"name": "evaluation CLI", "scopes": ["read", "evaluate"]}
    ).json()["token"]
    monkeypatch.setenv("CONNECTORFORGE_API_KEY", token)
    factory = httpx.Client

    def transport(request):
        response = admin.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr(
        "connectorforge.cli.httpx.Client",
        lambda **kwargs: factory(transport=httpx.MockTransport(transport), **kwargs),
    )
    result = CliRunner().invoke(
        cli,
        ["evaluate", "inventory", release, str(Path(__file__).parent / "data/initial-golden.json")],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "passed"
    assert token not in result.stdout
    monkeypatch.delenv("CONNECTORFORGE_API_KEY")
    result = CliRunner().invoke(cli, ["evaluate", "inventory", release, "missing.json"])
    assert result.exit_code == 2


async def test_explicit_null_and_absent_body_remain_distinct_after_persistence():
    source = {
        "openapi": "3.1.0",
        "info": {"title": "Nullable body", "version": "1.0.0"},
        "paths": {
            "/nothing": {
                "post": {
                    "operationId": "nothing",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "null"}}},
                    },
                    "responses": {"204": {"description": "No content"}},
                }
            }
        },
    }
    value = build_bundle(
        "nullable", "1.0.0", json.dumps(source), fixtures={"nothing": {"status": 204}}
    )
    golden = EvaluationInput.model_validate(
        {
            "dataset_version": "1.0.0",
            "cases": [
                {
                    "id": "explicit-null",
                    "input": {"operation_id": "nothing", "arguments": {"body": None}},
                    "expected": {"kind": "success", "status": 204, "data": None},
                },
                {
                    "id": "absent-body",
                    "input": {"operation_id": "nothing"},
                    "expected": {"kind": "error", "code": "invalid_arguments"},
                },
            ],
        }
    )
    persisted = EvaluationInput.model_validate(golden.model_dump(exclude_unset=True))
    assert (await evaluate(value, persisted))["passed"]
