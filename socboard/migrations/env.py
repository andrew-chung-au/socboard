"""Alembic environment for SOCBoard (#11).

Run it through `python -m socboard.migrate` (or a test), which hands in the connection:
`config.attributes["connection"]`. There is no `alembic.ini` and no offline (SQL script) mode.

`render_item` renders the `UtcDateTime` column type as `sa.DateTime(timezone=True)`, so a generated
migration runs unedited and imports nothing from `socboard`. Migration files hold CHECK text as
literals and never import `socboard.domain`.
"""

from alembic import context

from socboard.db_types import UtcDateTime
from socboard.models import metadata

config = context.config


def render_item(type_, obj, autogen_context):
    if type_ == "type" and isinstance(obj, UtcDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("Run migrations with make migrate (python -m socboard.migrate).")
    context.configure(
        connection=connection,
        target_metadata=metadata,
        render_item=render_item,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) mode is not supported.")

run_migrations_online()
