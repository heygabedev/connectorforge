"""Application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from connectorforge import __version__
from connectorforge.config import Settings
from connectorforge.database import Database


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

    @application.get("/healthz", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/readyz", tags=["health"])
    def ready() -> dict[str, str]:
        application.state.db.verify_schema()
        return {"status": "ready"}

    return application
