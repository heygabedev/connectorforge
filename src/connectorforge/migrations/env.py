"""Run migrations using the application's existing connection."""

from alembic import context

from connectorforge.models import Base

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
