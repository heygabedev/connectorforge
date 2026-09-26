"""Application entry point."""

from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from connectorforge import __version__
from connectorforge.config import Settings
from connectorforge.database import Database
from connectorforge.problems import Problem, handle_problem, handle_validation
from connectorforge.security import SecurityBoundary
from connectorforge.security import router as security_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(settings)
        database.initialize()
        application.state.db = database
        try:
            yield
        finally:
            database.close()

    application = FastAPI(title="ConnectorForge", version=__version__, lifespan=lifespan)
    application.state.settings = settings
    application.state.login_attempts = deque(maxlen=10)
    application.add_middleware(SecurityBoundary, settings=settings)
    application.add_exception_handler(Problem, handle_problem)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, handle_validation)  # type: ignore[arg-type]
    application.include_router(security_router)

    @application.get("/healthz", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/readyz", tags=["health"])
    def ready() -> dict[str, str]:
        application.state.db.verify_schema()
        return {"status": "ready"}

    return application
