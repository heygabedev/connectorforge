"""Supervise bounded child processes and lease persistent evaluation jobs."""

import asyncio
import multiprocessing
import time
from contextlib import suppress
from multiprocessing.connection import Connection
from typing import Any

from sqlalchemy import select, update

from connectorforge.database import Database
from connectorforge.models import AccessToken, Job, new_id
from connectorforge.problems import Problem
from connectorforge.security import PERMISSIONS

TASK_SECONDS = 20.0
QUEUE_LIMIT = 32


def check_actor(db: Database, actor: str, permission: str, connector: str) -> None:
    with db.transaction() as session:
        token = session.get(AccessToken, actor)
        if token is None or token.revoked or token.expires_at <= time.time():
            raise Problem(401, "unauthorized", "The job credential is no longer current.")
        if permission not in token.scopes or permission not in PERMISSIONS[token.role]:
            raise Problem(403, "forbidden", "The job credential no longer grants this action.")
        if "*" not in token.connectors and connector not in token.connectors:
            raise Problem(
                403, "connector_scope", "The connector is outside the job credential scope."
            )


def child_entry(kind: str, payload: dict[str, Any], channel: Connection) -> None:
    try:
        if kind == "compile":
            from connectorforge.catalog import build_bundle

            result = build_bundle(**payload)
        else:
            from connectorforge.evaluation import EvaluationInput, evaluate

            result = asyncio.run(
                evaluate(payload["bundle"], EvaluationInput.model_validate(payload["definition"]))
            )
        channel.send({"result": result})
    except Problem as error:
        channel.send(
            {"error": {"status": error.status, "code": error.code, "detail": error.detail}}
        )
    except Exception:
        channel.send(
            {
                "error": {
                    "status": 422,
                    "code": "worker_failed",
                    "detail": "Worker could not process this input.",
                }
            }
        )
    finally:
        channel.close()


class Supervisor:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.context = multiprocessing.get_context("spawn")
        self.slots = asyncio.Semaphore(2)
        self.waiting = 0
        self.seconds = TASK_SECONDS

    async def execute(
        self,
        kind: str,
        payload: dict[str, Any],
        actor: str,
        connector: str,
        job_id: str | None = None,
        lease: str | None = None,
    ) -> dict[str, Any]:
        if self.waiting >= 8:
            raise Problem(429, "worker_busy", "Compilation capacity is full; try again shortly.")
        self.waiting += 1
        try:
            async with asyncio.timeout(self.seconds):
                async with self.slots:
                    return await self._execute(kind, payload, actor, connector, job_id, lease)
        except TimeoutError as error:
            raise Problem(
                504, "worker_deadline", "Worker exceeded its overall time budget."
            ) from error
        finally:
            self.waiting -= 1

    async def _execute(
        self,
        kind: str,
        payload: dict[str, Any],
        actor: str,
        connector: str,
        job_id: str | None,
        lease: str | None,
    ) -> dict[str, Any]:
        permission = "manage" if kind == "compile" else "evaluate"
        check_actor(self.db, actor, permission, connector)
        parent, child = self.context.Pipe(duplex=False)
        process = self.context.Process(target=child_entry, args=(kind, payload, child), daemon=True)
        process.start()
        child.close()
        try:
            while True:
                check_actor(self.db, actor, permission, connector)
                if job_id:
                    with self.db.transaction() as session:
                        row = session.get(Job, job_id)
                        if row is None or row.status != "running" or row.lease != lease:
                            raise Problem(
                                409,
                                "job_cancelled",
                                "The evaluation was cancelled or its lease changed.",
                            )
                if parent.poll():
                    try:
                        message = parent.recv()
                    except EOFError as error:
                        raise Problem(
                            502, "worker_exit", "Worker exited without a result."
                        ) from error
                    if "error" in message:
                        raise Problem(**message["error"])
                    return dict(message["result"])
                if not process.is_alive():
                    raise Problem(502, "worker_exit", "Worker exited without a result.")
                await asyncio.sleep(0.05)
        finally:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 2)
            process.close()
            parent.close()

    def claim(self) -> tuple[str, str] | None:
        now = time.time()
        with self.db.transaction() as session:
            session.execute(
                update(Job)
                .where(Job.kind == "evaluation", Job.status == "running", Job.lease_until < now)
                .values(status="queued", lease=None, lease_until=None, updated_at=now)
            )
            row = session.scalar(
                select(Job)
                .where(Job.status == "queued", Job.kind == "evaluation")
                .order_by(Job.created_at)
                .limit(1)
            )
            if row is None:
                return None
            lease = new_id()
            changed = session.execute(
                update(Job)
                .where(Job.id == row.id, Job.status == "queued")
                .values(
                    status="running",
                    lease=lease,
                    lease_until=now + self.seconds + 10,
                    attempts=Job.attempts + 1,
                    updated_at=now,
                )
            )
            return (row.id, lease) if changed.rowcount == 1 else None  # type: ignore[attr-defined]

    async def run(self) -> None:
        while True:
            claimed = self.claim()
            if claimed is None:
                await asyncio.sleep(0.1)
                continue
            job_id, lease = claimed
            with self.db.transaction() as session:
                row = session.get(Job, job_id)
                if row is None:
                    continue
                payload, actor = row.payload, row.actor
            try:
                result: dict[str, Any] | None = await self.execute(
                    "evaluation", payload, actor, payload["connector"], job_id, lease
                )
                status, error = ("passed" if result and result["passed"] else "failed"), None
            except Problem as problem:
                result, status, error = None, "failed", problem.code
            except asyncio.CancelledError:
                with self.db.transaction() as session:
                    session.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.lease == lease, Job.status == "running")
                        .values(status="queued", lease=None, lease_until=None)
                    )
                raise
            with self.db.transaction() as session:
                session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.lease == lease, Job.status == "running")
                    .values(
                        status=status,
                        result=result,
                        error=error,
                        lease=None,
                        lease_until=None,
                        updated_at=time.time(),
                    )
                )


async def stop_worker(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
