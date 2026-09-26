"""One-time login codes and revocable credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_codes",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
    )
    op.create_table(
        "access_tokens",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("scopes", sa.JSON, nullable=False),
        sa.Column("connectors", sa.JSON, nullable=False),
        sa.Column("operations", sa.JSON, nullable=False),
        sa.Column("csrf", sa.String(64)),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False),
    )
    op.create_index("ix_access_tokens_digest", "access_tokens", ["digest"], unique=True)


def downgrade() -> None:
    op.drop_table("access_tokens")
    op.drop_table("login_codes")
