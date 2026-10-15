"""Application entry point."""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from connectorforge import __version__
from connectorforge.catalog import router as catalog_router
from connectorforge.config import Settings
from connectorforge.database import Database
from connectorforge.jobs import router as jobs_router
from connectorforge.models import Audit, Job
from connectorforge.problems import Problem, handle_problem, handle_validation
from connectorforge.security import SecurityBoundary, require
from connectorforge.security import router as security_router
from connectorforge.worker import Supervisor, stop_worker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(settings)
        database.initialize()
        application.state.db = database
        application.state.supervisor = Supervisor(database)
        worker = asyncio.create_task(application.state.supervisor.run())
        try:
            yield
        finally:
            await stop_worker(worker)
            database.close()

    application = FastAPI(title="ConnectorForge", version=__version__, lifespan=lifespan)
    application.state.settings = settings
    application.state.login_attempts = deque(maxlen=10)
    application.add_middleware(SecurityBoundary, settings=settings)
    application.add_exception_handler(Problem, handle_problem)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, handle_validation)  # type: ignore[arg-type]
    application.include_router(security_router)
    application.include_router(catalog_router)
    application.include_router(jobs_router)

    @application.get("/healthz", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/readyz", tags=["health"])
    def ready() -> dict[str, str]:
        application.state.db.verify_schema()
        return {"status": "ready"}

    @application.get("/api/v1/workspace", tags=["workspace"])
    def workspace(request: Request) -> dict[str, str | int]:
        principal = require(request)
        with application.state.db.transaction() as session:
            audits = session.scalar(select(func.count()).select_from(Audit))
            jobs = session.scalar(select(func.count()).select_from(Job))
        return {
            "name": "Local workspace",
            "role": principal.role,
            "version": __version__,
            "audit_events": audits,
            "jobs": jobs,
        }

    static = Path(__file__).parent / "static"
    if not static.exists():
        static = Path(__file__).resolve().parents[2] / "web" / "dist"
    if static.exists():
        application.mount("/", StaticFiles(directory=static, html=True), name="console")

    return application
