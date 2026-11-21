"""Alembic environment. Always called programmatically (kb_platform.migrate) with an open connection."""
from alembic import context

from kb_platform.db import Base
from kb_platform import models  # noqa: F401  (register tables)

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connection = config.attributes["connection"]
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True,
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
