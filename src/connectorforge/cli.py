"""Commands for running and maintaining a local workspace."""

import json
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import typer
import uvicorn

from connectorforge.config import Settings, require_safe_sqlite, sqlite_is_patched
from connectorforge.database import Database
from connectorforge.evaluation import EvaluationInput
from connectorforge.security import Role, issue_login_code

app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Run and inspect a local ConnectorForge workspace.",
)


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


@app.command("login-code")
def login_code(role: str = "admin") -> None:
    """Issue a ten-minute, single-use code for a local demo role."""
    if role not in {"admin", "operator", "viewer"}:
        raise typer.BadParameter("Choose admin, operator, or viewer")
    database = Database(Settings.from_env())
    try:
        database.initialize()
        selected: Role = role  # type: ignore[assignment]
        typer.echo(issue_login_code(database, selected))
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


@app.command("evaluate")
def evaluate_command(slug: str, release: str, dataset: Path, wait: bool = True) -> None:
    """Submit independent golden expectations using CONNECTORFORGE_API_KEY."""
    credential = os.environ.get("CONNECTORFORGE_API_KEY")
    if not credential:
        raise typer.BadParameter("Set CONNECTORFORGE_API_KEY to a scoped local credential.")
    definition = EvaluationInput.model_validate_json(dataset.read_text(encoding="utf-8"))
    with httpx.Client(
        base_url=Settings.from_env().origin,
        trust_env=False,
        timeout=25,
        headers={"Authorization": "Bearer " + credential},
    ) as client:
        response = client.post(
            f"/api/v1/connectors/{slug}/releases/{release}/evaluations",
            json=definition.model_dump(exclude_unset=True),
        )
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + 90
        try:
            while wait and job["status"] in {"queued", "running"}:
                if time.monotonic() >= deadline:
                    client.post(f"/api/v1/jobs/{job['id']}/cancel").raise_for_status()
                    raise typer.Exit(1)
                time.sleep(0.2)
                response = client.get(f"/api/v1/jobs/{job['id']}")
                response.raise_for_status()
                job = response.json()
        except KeyboardInterrupt:
            client.post(f"/api/v1/jobs/{job['id']}/cancel")
            raise
        typer.echo(json.dumps(job, indent=2))
        if job["status"] in {"failed", "cancelled"}:
            raise typer.Exit(1)


if __name__ == "__main__":
    app()
