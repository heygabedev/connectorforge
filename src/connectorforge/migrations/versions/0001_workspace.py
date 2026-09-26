"""Workspace identity, audit history, and durable jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
    )
    op.create_table(
        "audit",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource", sa.String(160), nullable=False),
        sa.Column("detail", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_index("ix_audit_created_at", "audit", ["created_at"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("parent_id", sa.String(32), sa.ForeignKey("jobs.id")),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("result", sa.JSON),
        sa.Column("lease", sa.String(32)),
        sa.Column("lease_until", sa.Float),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','passed','failed','cancelled','paused')",
            name="job_status",
        ),
    )
    op.create_index("ix_jobs_kind", "jobs", ["kind"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("audit")
    op.drop_table("settings")
