"""Persistent records shared by the application services."""

import time
import uuid
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    resource: Mapped[str] = mapped_column(String(160))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','passed','failed','cancelled','paused')",
            name="job_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    actor: Mapped[str] = mapped_column(String(80))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    lease: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[float | None] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class LoginCode(Base):
    __tablename__ = "login_codes"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[float] = mapped_column(Float)


class AccessToken(Base):
    __tablename__ = "access_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list[str]] = mapped_column(JSON)
    connectors: Mapped[list[str]] = mapped_column(JSON)
    operations: Mapped[list[str]] = mapped_column(JSON)
    csrf: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)
    revoked: Mapped[bool] = mapped_column(default=False)
