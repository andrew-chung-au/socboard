"""One database session per request (#13), on #11's engine.

`create_app()` puts a `DatabaseState` on `app.state.database`. The engine is created on the first
request that needs a session, not when the app is built, so `GET /api/health` needs no database
file. `get_session` is the route dependency: FastAPI caches it per request, so `current_user` and
the route share one session. The route commits explicitly before it returns; this dependency only
closes the session, which rolls back anything left uncommitted.

A request never creates a missing SQLite file (#104): this engine, and only this one (`make
migrate` and `make seed` must still create the file), makes SQLite open a file database with
`mode=rw`. A missing file then fails at the first connection with SQLite's own "unable to open
database file" (no path), which the generic 500 handler answers as `internal_error`. Requests that
never query (no token, a bad login body) still get their 401 or 422.
"""

import os
import threading
from collections.abc import Iterator
from urllib.parse import quote, unquote

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from socboard import database


def _open_without_create(dialect, connection_record, cargs, cparams) -> None:
    """Rewrite the SQLite dialect's connect arguments so a file database opens with `mode=rw`.

    `cargs[0]` is what the pysqlite dialect computed: with `uri` true, a name starting `file:` is a
    SQLite URI, and anything else is a literal file name (SQLite's own rule).
    """
    name = cargs[0]
    if not name or name == ":memory:":
        return  # in memory, or SQLite's private temporary database: no file
    if not (cparams.get("uri") and name.startswith("file:")):
        # The leading `//` is an empty authority, so a path starting `//` stays a path.
        cargs[0] = "file://" + quote(os.path.abspath(name)) + "?mode=rw"
        cparams["uri"] = True
        return
    path, _, query = name.partition("#")[0].partition("?")  # SQLite ignores the fragment
    params = []
    has_mode = False
    for param in filter(None, query.split("&")):
        key, _, value = param.partition("=")
        if unquote(key) == "mode":  # SQLite decodes keys and values before reading them
            has_mode = True
            if unquote(value) == "rwc":  # SQLite applies each `mode` in turn; only rwc creates
                param = "mode=rw"
        params.append(param)
    if not has_mode:
        params.append("mode=rw")
    cargs[0] = path + "?" + "&".join(params)


class DatabaseState:
    """The database URL, and the engine once something asks for it."""

    def __init__(self, url: URL) -> None:
        self.url = url
        self._engine: Engine | None = None
        self._lock = threading.Lock()

    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                engine = database.create_db_engine(self.url)
                if engine.dialect.name == "sqlite":
                    event.listen(engine, "do_connect", _open_without_create)
                self._engine = engine
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
