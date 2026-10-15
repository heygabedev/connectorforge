"""Authenticated evaluation job submission and cancellation."""

import time
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from connectorforge.catalog import read_release
from connectorforge.evaluation import EvaluationInput
from connectorforge.execution import operation_by_id
from connectorforge.models import Audit, Job
from connectorforge.problems import Problem
from connectorforge.security import Principal, require
from connectorforge.worker import QUEUE_LIMIT

router = APIRouter(prefix="/api/v1", tags=["evaluations"])


def visible(row: Job, principal: Principal) -> bool:
    return (row.actor == principal.id or principal.role == "admin") and (
        "*" in principal.connectors or row.payload.get("connector") in principal.connectors
    )


def summarize(row: Job) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "result": row.result,
        "error": row.error,
        "attempts": row.attempts,
        "created_at": row.created_at,
        "connector": row.payload.get("connector"),
        "release": row.payload.get("release"),
    }


@router.post("/connectors/{slug}/releases/{release_id}/evaluations", status_code=202)
def submit(slug: str, release_id: str, data: EvaluationInput, request: Request) -> dict[str, str]:
    principal = require(request, "evaluate", slug)
    release = read_release(request.app.state.db, slug, release_id)
    identifiers = set()
    for case in data.cases:
        require(request, "evaluate", slug, case.input.operation_id)
        operation_by_id(release.bundle, case.input.operation_id)
        if case.id in identifiers:
            raise Problem(422, "duplicate_case", "Golden case identifiers must be unique.")
        identifiers.add(case.id)
    with request.app.state.db.transaction() as session:
        active = session.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))
        )
        if active >= QUEUE_LIMIT:
            raise Problem(429, "queue_full", "The evaluation queue is full.")
        row = Job(
            kind="evaluation",
            actor=principal.id,
            payload={
                "connector": slug,
                "release": release_id,
                "bundle": release.bundle,
                "definition": data.model_dump(exclude_unset=True),
            },
        )
        session.add(row)
        session.flush()
        session.add(Audit(actor=principal.id, action="evaluation.submitted", resource=row.id))
        return {"id": row.id, "status": "queued"}


@router.get("/jobs")
def list_jobs(request: Request) -> list[dict[str, Any]]:
    principal = require(request)
    with request.app.state.db.transaction() as session:
        return [
            summarize(row)
            for row in session.scalars(select(Job).order_by(Job.created_at.desc()).limit(100))
            if visible(row, principal)
        ]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    principal = require(request)
    with request.app.state.db.transaction() as session:
        row = session.get(Job, job_id)
        if row is None or not visible(row, principal):
            raise Problem(404, "not_found", "Job does not exist in your scope.")
        return summarize(row)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, str]:
    principal = require(request, "evaluate")
    with request.app.state.db.transaction() as session:
        row = session.get(Job, job_id)
        if row is None or not visible(row, principal):
            raise Problem(404, "not_found", "Job does not exist in your scope.")
        if row.status not in {"queued", "running"}:
            raise Problem(409, "job_finished", "This job has already finished.")
        row.status, row.lease, row.lease_until = "cancelled", None, None
        row.updated_at = time.time()
        session.add(Audit(actor=principal.id, action="evaluation.cancelled", resource=job_id))
    return {"id": job_id, "status": "cancelled"}
