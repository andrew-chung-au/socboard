"""One database session per request (#13), on #11's engine.

`create_app()` puts a `DatabaseState` on `app.state.database`. The engine is created on the first
request that needs a session, not when the app is built, so `GET /api/health` needs no database
file. `get_session` is the route dependency: FastAPI caches it per request, so `current_user` and
the route share one session. The route commits explicitly before it returns; this dependency only
closes the session, which rolls back anything left uncommitted.

A request never creates a missing SQLite file (#104): this engine, and only this one (`make
migrate` and `make seed` must still create the file), makes SQLite open a file database without
create (`mode=rw` last, or a plain read-only, read-write or memory mode the URL already ends
with; the rule is in `_open_without_create`). A missing file then fails at the first connection
with SQLite's own "unable to open database file" (no path), which the generic 500 handler answers
as `internal_error`. Requests that never query (no token, a bad login body) still get their 401 or
422.
"""

import os
import threading
from collections.abc import Iterator
from urllib.parse import quote

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from socboard import database


_PLAIN_MODES = ("mode=ro", "mode=rw", "mode=memory")


def _open_without_create(dialect, connection_record, cargs, cparams) -> None:
    """Rewrite the SQLite dialect's connect arguments so a file database is never created.

    `cargs[0]` is what the pysqlite dialect computed: with `uri` true, a name starting `file:` is a
    SQLite URI, and anything else is a literal file name (SQLite's own rule). A literal name
    becomes `file://` + its quoted absolute path + `?mode=rw`.

    A `file:` URI is never decoded or rewritten, because SQLite's decoding has corners (a `%00`
    ends the current key or value) that a copy of it could miss. The fragment (from the first
    `#`, which SQLite ignores) is dropped and `mode=rw` is appended as the last parameter, unless
    the last `mode` parameter is written plainly as `mode=ro`, `mode=rw` or `mode=memory` and no
    parameter after it holds a `%`: then SQLite reads that `mode` as written and the URI is kept.

    Why this cannot create a file (measured on SQLite 3.46.1): SQLite reads the `mode`
    parameters in order and each may only narrow the access the earlier ones left; a wider one
    fails the open with "access mode not allowed". The last `mode` SQLite reads is then either
    the kept `ro`, `rw` or `memory`, or the appended `rw`, and none of them carries create.
    Accepted costs: an oddly encoded read-only mode (`mod%65=ro`) followed by the appended
    `mode=rw` fails at the first connection; an oddly encoded `mod%65=memory` followed by it
    opens the named file read-write instead of a memory database (a missing file still fails).
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
    params = query.split("&")
    modes = [i for i, param in enumerate(params) if param.partition("=")[0] == "mode"]
    if modes and params[modes[-1]] in _PLAIN_MODES:
        if not any("%" in param for param in params[modes[-1] + 1 :]):
            return  # read as written: SQLite's last `mode` is ro, rw or memory, never rwc
    cargs[0] = path + "?" + (query + "&" if query else "") + "mode=rw"


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
