"""Fail clearly when a data-layer package is missing.

The data layer (#11) needs SQLAlchemy 2.x and Alembic. The health route and the rest of the app do
not import this module, so the app still starts without them; any data module that is imported
without them stops with `MissingPackageError`, which says which package and what to run.
"""

import importlib
from types import ModuleType

_NAMES = {"sqlalchemy": "SQLAlchemy 2.x", "alembic": "Alembic"}


class MissingPackageError(ImportError):
    """A package the data layer needs is not installed (or is too old)."""


def require(name: str) -> ModuleType:
    """Import `name` or raise `MissingPackageError` with a plain, fixed message."""
    label = _NAMES.get(name, name)
    try:
        module = importlib.import_module(name)
    except ImportError as error:
        raise MissingPackageError(
            f"The data layer needs {label}, which is not installed. "
            "Add it with uv add (it must be in uv.lock) and run make install."
        ) from error
    if name == "sqlalchemy":
        major = str(getattr(module, "__version__", "0")).split(".", 1)[0]
        if not major.isdigit() or int(major) < 2:
            raise MissingPackageError(
                "The data layer needs SQLAlchemy 2.x; an older version is installed. "
                "Update it with uv add and run make install."
            )
    return module
