"""Local-only application configuration and runtime checks."""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def sqlite_is_patched(version: str) -> bool:
    parts = tuple(int(part) for part in version.split("."))
    return parts >= (3, 51, 3) or (3, 50, 7) <= parts < (3, 51) or (3, 44, 6) <= parts < (3, 45)


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    port: int = 8000
    development: bool = False

    def __post_init__(self) -> None:
        if not 1024 <= self.port <= 65535:
            raise ValueError("Port must be between 1024 and 65535")

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            state_dir=Path(os.environ.get("CONNECTORFORGE_HOME", ".connectorforge")).resolve(),
            port=int(os.environ.get("CONNECTORFORGE_PORT", "8000")),
            development=os.environ.get("CONNECTORFORGE_DEV") == "1",
        )


def require_safe_sqlite() -> None:
    if not sqlite_is_patched(sqlite3.sqlite_version):
        raise RuntimeError(
            f"SQLite {sqlite3.sqlite_version} lacks the WAL-reset fix. "
            "Install a current Python 3.13 runtime with SQLite 3.51.3+ or a patched backport."
        )
