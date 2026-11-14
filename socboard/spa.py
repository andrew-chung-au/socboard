"""Serve the built frontend (#140): the entry page for screen routes and the built files.

Setting, read when the app is built:
- `SOCBOARD_STATIC_DIR`: the folder `make frontend-build` writes. Default: `frontend/dist` in the
  repository root, found from this file, so the working directory does not matter. A relative
  value is relative to the working directory. A folder without `index.html` (or no folder) turns
  the feature off: no route is added and `GET /` is the existing 404.

One fallback route is added last, after every API router (`add_spa_route`). It never matches a
path under `/api`, so the existing JSON 404 and 405 answers do not change. A request for a file
inside the folder returns it; an extension-less path returns `index.html`; any other path with an
extension is a 404. Only GET and HEAD are allowed, so another method gets the JSON 405.
"""

import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Match, Route
from starlette.types import Scope

STATIC_DIR_VAR = "SOCBOARD_STATIC_DIR"
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[1] / "frontend" / "dist"

# The entry page names the hashed bundle, so it must be re-checked on every load; Vite puts a
# content hash in the names under `assets/`, so those never change. Everything else has no hash.
NO_CACHE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"


def resolve_static_dir(source: Mapping[str, str]) -> str | None:
    """The real path of the built site, or None when there is no `index.html` to serve."""
    value = source.get(STATIC_DIR_VAR, "").strip()
    root = os.path.realpath(value if value else DEFAULT_STATIC_DIR)
    return root if os.path.isfile(os.path.join(root, "index.html")) else None


class _FallbackRoute(Route):
    api_prefix = ""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        # Never match the API prefix, so an unknown /api path stays the existing JSON 404.
        path = scope["path"]
        if path == self.api_prefix or path.startswith(self.api_prefix + "/"):
            return Match.NONE, {}
        return super().matches(scope)


def add_spa_route(app: FastAPI, root: str, api_prefix: str) -> None:
    """Register the fallback route; call it after every other route."""

    def serve(request: Request) -> Response:
        relative = request.path_params["path"]
        if "\0" in relative:
            raise HTTPException(404)
        # Join the path without its leading slashes (an absolute path must not replace the root),
        # then resolve symlinks and `..` and require the result to stay inside the folder.
        target = os.path.realpath(os.path.join(root, relative.lstrip("/")))
        if target.startswith(root + os.sep) and os.path.isfile(target):
            name = target[len(root) + 1 :].replace(os.sep, "/")
            cache = IMMUTABLE if name.startswith("assets/") else NO_CACHE
            return FileResponse(target, headers={"Cache-Control": cache})
        if PurePosixPath(relative).suffix:
            raise HTTPException(404)  # a missing file is not a screen route
        return FileResponse(
            os.path.join(root, "index.html"), headers={"Cache-Control": NO_CACHE}
        )

    route = _FallbackRoute("/{path:path}", serve, methods=["GET"], include_in_schema=False)
    route.api_prefix = api_prefix
    app.router.routes.append(route)
