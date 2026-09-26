"""Application entry point."""

from fastapi import FastAPI

from connectorforge import __version__
from connectorforge.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="ConnectorForge", version=__version__)
    application.state.settings = settings or Settings.from_env()

    @application.get("/healthz", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return application
