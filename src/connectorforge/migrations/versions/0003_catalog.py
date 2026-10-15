"""Immutable connector releases and operational catalog."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("slug", sa.String(48), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("paused", sa.Boolean, nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
    )
    op.create_table(
        "releases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("connector", sa.String(48), sa.ForeignKey("connectors.slug"), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("major", sa.Integer, nullable=False),
        sa.Column("bundle", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.UniqueConstraint("connector", "version", name="connector_version"),
    )
    op.create_index("ix_releases_connector", "releases", ["connector"])
    op.execute(
        "CREATE TRIGGER immutable_release_update BEFORE UPDATE ON releases "
        "BEGIN SELECT RAISE(ABORT, 'releases are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER immutable_release_delete BEFORE DELETE ON releases "
        "BEGIN SELECT RAISE(ABORT, 'releases are immutable'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER immutable_release_delete")
    op.execute("DROP TRIGGER immutable_release_update")
    op.drop_table("releases")
    op.drop_table("connectors")
