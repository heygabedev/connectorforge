"""SQLite lifecycle and explicit transaction boundaries."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from connectorforge.config import Settings, require_safe_sqlite
from connectorforge.models import Setting, new_id


class Database:
    def __init__(self, settings: Settings) -> None:
        require_safe_sqlite()
        settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = settings.state_dir / "workspace.sqlite3"
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        event.listen(self.engine, "connect", self._configure)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure(connection: sqlite3.Connection, _: ConnectionPoolEntry) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")

    def migration_config(self) -> Config:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        return config

    def revision(self) -> str | None:
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()

    def initialize(self) -> None:
        if self.revision() is None:
            self.migrate()
        self.verify_schema()
        with self.transaction() as session:
            if session.get(Setting, "identity") is None:
                session.add(Setting(key="identity", value={"id": new_id()}))

    def verify_schema(self) -> None:
        expected = ScriptDirectory.from_config(self.migration_config()).get_current_head()
        if self.revision() != expected:
            raise RuntimeError("Database schema is incompatible. Run connectorforge migrate.")

    def migrate(self) -> None:
        config = self.migration_config()
        current = self.revision()
        scripts = ScriptDirectory.from_config(config)
        if current and scripts.get_revision(current) is None:
            raise RuntimeError("Unknown database revision")
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.sessions.begin() as session:
            yield session

    def close(self) -> None:
        self.engine.dispose()
