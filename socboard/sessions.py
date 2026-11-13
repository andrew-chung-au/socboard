"""One database session per request (#13), on #11's engine.

`create_app()` puts a `DatabaseState` on `app.state.database`. The engine is created on the first
request that needs a session, not when the app is built, so `GET /api/health` needs no database
file. `get_session` is the route dependency: FastAPI caches it per request, so `current_user` and
the route share one session. The route commits explicitly before it returns; this dependency only
closes the session, which rolls back anything left uncommitted.
"""

import threading
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from socboard import database


MISSING_DATABASE_MESSAGE = "The database file does not exist; run make migrate first."


class MissingDatabaseError(RuntimeError):
    """A SQLite file URL names a file that is not there. Fixed text: no path, no URL."""


def _refuse_missing_sqlite_file(url: URL) -> None:
    """SQLite creates a missing file on first connect; a request must not do that (#104).

    Only a SQLite URL with a file path is checked; `:memory:`, no file name, a `file:` URI and
    every other backend go to the driver unchanged.
    """
    if url.get_backend_name() != "sqlite" or url.query.get("uri"):
        return
    name = url.database
    if name and name != ":memory:" and not Path(name).exists():
        raise MissingDatabaseError(MISSING_DATABASE_MESSAGE)


class DatabaseState:
    """The database URL, and the engine once something asks for it."""

    def __init__(self, url: URL) -> None:
        self.url = url
        self._engine: Engine | None = None
        self._lock = threading.Lock()

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                _refuse_missing_sqlite_file(self.url)
                self._engine = database.create_db_engine(self.url)
            return self._engine

    def dispose(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None


def get_session(request: Request) -> Iterator[Session]:
    """A session for this request; closed (rolling back anything uncommitted) afterwards."""
    state: DatabaseState = request.app.state.database
    session = Session(state.engine())
    try:
        yield session
    finally:
        session.close()
