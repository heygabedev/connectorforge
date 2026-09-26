"""Commands for running and maintaining a local workspace."""

import json
import socket
import sqlite3
import sys

import typer
import uvicorn

from connectorforge.config import Settings, require_safe_sqlite, sqlite_is_patched
from connectorforge.database import Database

app = typer.Typer(no_args_is_help=True, help="Run and inspect a local ConnectorForge workspace.")


@app.command()
def setup() -> None:
    """Create an empty workspace with the current database schema."""
    database = Database(Settings.from_env())
    try:
        database.initialize()
        typer.echo(f"Workspace ready: {database.path}")
    finally:
        database.close()


@app.command()
def migrate() -> None:
    """Apply the application's database migrations."""
    database = Database(Settings.from_env())
    try:
        database.migrate()
        typer.echo(f"Database revision: {database.revision()}")
    finally:
        database.close()


@app.command()
def doctor() -> None:
    """Check runtime compatibility and the configured listening port."""
    settings = Settings.from_env()
    with socket.socket() as probe:
        port_available = probe.connect_ex(("127.0.0.1", settings.port)) != 0
    report = {
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "sqlite_patched": sqlite_is_patched(sqlite3.sqlite_version),
        "state_directory": str(settings.state_dir),
        "port_available": port_available,
    }
    typer.echo(json.dumps(report, indent=2))
    if not report["sqlite_patched"] or not port_available:
        raise typer.Exit(1)


@app.command()
def serve() -> None:
    """Start the application on the loopback interface."""
    require_safe_sqlite()
    settings = Settings.from_env()
    uvicorn.run("connectorforge.api:create_app", factory=True, host="127.0.0.1", port=settings.port)


if __name__ == "__main__":
    app()
