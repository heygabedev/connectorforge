from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from connectorforge.config import Settings
from connectorforge.database import Database
from connectorforge.models import Audit, Job, Setting


@pytest.fixture
def database(tmp_path: Path):
    db = Database(Settings(tmp_path))
    db.initialize()
    yield db
    db.close()


def test_initialization_is_idempotent(database: Database) -> None:
    with database.transaction() as session:
        identity = session.get(Setting, "identity").value
    database.initialize()
    with database.transaction() as session:
        assert session.get(Setting, "identity").value == identity
    with database.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_audit_rolls_back_with_transaction(database: Database) -> None:
    with pytest.raises(ValueError), database.transaction() as session:
        session.add(Audit(actor="admin", action="test", resource="workspace"))
        raise ValueError("Simulated transaction failure")
    with database.transaction() as session:
        assert session.scalar(select(Audit)) is None


def test_foreign_keys_are_enforced(database: Database) -> None:
    with pytest.raises(IntegrityError), database.transaction() as session:
        session.add(Job(kind="test", actor="admin", payload={}, parent_id="missing"))


def test_unknown_revision_is_rejected(database: Database) -> None:
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='future'"))
    with pytest.raises(RuntimeError, match="incompatible"):
        database.initialize()


def test_job_status_constraint(database: Database) -> None:
    with pytest.raises(IntegrityError), database.transaction() as session:
        session.add(Job(kind="test", actor="admin", payload={}, status="invented"))
