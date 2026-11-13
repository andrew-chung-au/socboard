"""Serving the built frontend from the app (#140), one test per acceptance criterion case.

Each test builds a small `dist` in a temporary folder (no bun) and points `SOCBOARD_STATIC_DIR`
at it. Files holding SECRET sit outside `dist`, where a traversal would reach them.
"""

import os

import pytest
from fastapi.testclient import TestClient

from socboard.app import create_app

SECRET = "zz-secret-outside-dist-5c1e"
INDEX = "<!doctype html><title>SOCBoard test page</title><div id=root></div>"
ASSET = "console.log('asset');"


@pytest.fixture
def site(tmp_path):
    """tmp/outer/... holds secrets; tmp/outer/site/dist is the built site."""
    outer = tmp_path / "outer"
    dist = outer / "site" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text(ASSET, encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"icon")
    for secret in (
        outer / "socboard.db",
        outer / "site" / "socboard.db",
        outer / "pyproject.toml",
        outer / "site" / "pyproject.toml",
        outer / "secret.txt",
    ):
        secret.write_text(SECRET, encoding="utf-8")
    return dist


@pytest.fixture
def client(site, monkeypatch):
    monkeypatch.setenv("SOCBOARD_STATIC_DIR", str(site))
    return TestClient(create_app())


def assert_entry_page(response):
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")


def assert_json_error(response, status, code):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert set(response.json()) == {"code", "message"}
    assert response.json()["code"] == code


def assert_no_leak(response):
    assert SECRET not in response.text
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert response.text == INDEX


# --- criterion 1: entry page, screen route, asset ----------------------------------------------


def test_root_returns_the_entry_page(client):
    assert_entry_page(client.get("/"))


def test_screen_route_returns_the_entry_page_with_200(client):
    assert_entry_page(client.get("/cases/1"))


def test_asset_returns_the_file_with_its_content_type(client):
    response = client.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert response.text == ASSET
    assert "javascript" in response.headers["content-type"]


def test_head_on_a_screen_route_returns_headers_without_a_body(client):
    response = client.head("/cases/1")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"].startswith("text/html")


# --- criterion 2: /api stays the API -----------------------------------------------------------


def test_unknown_api_path_is_the_json_404(client):
    assert_json_error(client.get("/api/nothing-here"), 404, "not_found")


def test_api_itself_is_the_json_404(client):
    assert_json_error(client.get("/api"), 404, "not_found")


def test_api_with_a_trailing_slash_is_the_json_404(client):
    assert_json_error(client.get("/api/"), 404, "not_found")


def test_path_that_only_starts_like_api_is_a_screen_route(client):
    assert_entry_page(client.get("/apix"))


def test_wrong_method_on_an_api_route_is_still_405(client):
    response = client.post("/api/health")
    assert_json_error(response, 405, "method_not_allowed")
    assert "GET" in response.headers["allow"]


def test_wrong_method_on_an_unknown_api_path_is_still_404(client):
    assert_json_error(client.post("/api/nothing-here"), 404, "not_found")


# --- criterion 3: only GET and HEAD get the site -----------------------------------------------


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_other_methods_on_a_screen_route_get_a_json_error(client, method):
    response = getattr(client, method)("/cases/1")
    assert_json_error(response, 405, "method_not_allowed")
    assert INDEX not in response.text


# --- criterion 4: /api unchanged ---------------------------------------------------------------


def test_openapi_is_identical_with_and_without_the_site(site, tmp_path, monkeypatch):
    monkeypatch.setenv("SOCBOARD_STATIC_DIR", str(tmp_path / "no-such-folder"))
    without = create_app().openapi()
    monkeypatch.setenv("SOCBOARD_STATIC_DIR", str(site))
    with_site = create_app().openapi()
    assert with_site == without
    assert all("{path" not in path for path in with_site["paths"])


def test_fallback_is_the_last_route_and_api_health_still_answers(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.app.router.routes[-1].path == "/{path:path}"


# --- criterion 5: traversal --------------------------------------------------------------------


def test_encoded_slash_traversal_to_the_database_is_refused(client):
    assert_no_leak(client.get("/..%2f..%2fsocboard.db"))


def test_encoded_dot_traversal_to_pyproject_is_refused(client):
    assert_no_leak(client.get("/%2e%2e/pyproject.toml"))


def test_absolute_path_is_refused(client, site):
    secret = site.parent.parent / "secret.txt"
    # A full URL, so the double slash stays in the path instead of being read as a host.
    response = client.get("http://testserver/" + secret.as_posix())
    assert_no_leak(response)
    assert response.status_code == 404


def test_symlink_out_of_dist_is_refused(client, site):
    link = site / "assets" / "link.js"
    try:
        os.symlink(site.parent.parent / "secret.txt", link)
    except OSError:
        pytest.skip("symlinks are not available here")
    assert_no_leak(client.get("/assets/link.js"))
    assert client.get("/assets/link.js").status_code == 404


# --- criterion 6: file-like and screen-like misses ---------------------------------------------


def test_missing_file_like_path_is_404_not_the_entry_page(client):
    assert_json_error(client.get("/assets/missing.js"), 404, "not_found")


def test_missing_extensionless_path_falls_back_to_the_entry_page(client):
    assert_entry_page(client.get("/queue/anything"))


# --- criterion 7: no dist folder ---------------------------------------------------------------


def test_without_a_dist_folder_the_app_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCBOARD_STATIC_DIR", str(tmp_path / "no-such-folder"))
    app = create_app()
    client = TestClient(app)
    assert_json_error(client.get("/"), 404, "not_found")
    assert_json_error(client.get("/cases/1"), 404, "not_found")
    assert client.get("/api/health").json() == {"status": "ok"}
    assert all(getattr(route, "path", "") != "/{path:path}" for route in app.router.routes)


def test_a_folder_without_index_html_turns_the_feature_off(tmp_path, monkeypatch):
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("SOCBOARD_STATIC_DIR", str(tmp_path / "empty"))
    assert_json_error(TestClient(create_app()).get("/"), 404, "not_found")


# --- criterion 8: no cookie, headers -----------------------------------------------------------


def test_entry_page_and_assets_set_no_cookie_and_the_chosen_cache_headers(client):
    page = client.get("/cases/1")
    asset = client.get("/assets/app-abc123.js")
    other = client.get("/favicon.ico")
    assert page.headers["cache-control"] == "no-cache"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert other.headers["cache-control"] == "no-cache"
    for response in (page, asset, other):
        assert "set-cookie" not in response.headers
    assert not client.cookies
