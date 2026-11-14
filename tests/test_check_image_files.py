"""scripts/check_image_files.py (#190): the checker for the files of the container image.

Each refused case builds a small folder that looks like an exported image (an app/ folder) and
shows the checker exits 1 and names the path; a clean folder passes. Every value is synthetic.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_image_files.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_image_files", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load_checker()


def clean_image(tmp_path):
    """A small image: the app, a clean frontend build, and a virtual environment."""
    app = tmp_path / "image" / "app"
    (app / "socboard").mkdir(parents=True)
    (app / "socboard" / "server.py").write_text("print('hello')\n", encoding="utf-8")
    (app / "frontend" / "dist" / "assets").mkdir(parents=True)
    (app / "frontend" / "dist" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    (app / "frontend" / "dist" / "assets" / "app.js").write_text("console.log('x');\n", encoding="utf-8")
    (app / "README.md").write_text("# SOCBoard\n", encoding="utf-8")
    (app / ".venv" / "lib").mkdir(parents=True)
    return tmp_path / "image"


def run(folder):
    return subprocess.run([sys.executable, str(SCRIPT), str(folder)], capture_output=True, text=True, timeout=60)


def put(root, relative, text="x\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_clean_folder_passes(tmp_path):
    done = run(clean_image(tmp_path))
    assert done.returncode == 0, done.stderr
    assert "no finding" in done.stdout


# (id, relative path to create, text, what the finding must say)
NAME_CASES = [
    ("env file", "app/.env", "x\n", "named like an env file"),
    ("env file with an extension", "app/socboard/.env.production", "x\n", "named like an env file"),
    ("env file by extension", "app/socboard/prod.env", "x\n", "named like an env file"),
    ("git folder", "app/.git/config", "x\n", ".git folder"),
    ("database", "app/socboard.db", "x\n", "database or journal file"),
    ("sqlite file", "app/data.sqlite3", "x\n", "database or journal file"),
    ("journal file", "app/socboard.db-journal", "x\n", "database or journal file"),
    ("wal file", "app/socboard.db-wal", "x\n", "database or journal file"),
    ("tests folder", "app/tests/test_one.py", "x\n", "tests folder"),
    ("test file outside tests", "app/socboard/test_thing.py", "x\n", "a test file"),
    ("node_modules", "app/frontend/node_modules/a/index.js", "x\n", "node_modules folder"),
]


@pytest.mark.parametrize(
    ("relative", "text", "message"), [case[1:] for case in NAME_CASES], ids=[case[0] for case in NAME_CASES]
)
def test_a_forbidden_name_is_refused_and_named(relative, text, message, tmp_path):
    image = clean_image(tmp_path)
    put(image, relative, text)
    done = run(image)
    assert done.returncode == 1
    assert message in done.stderr
    # The finding names the file, or the forbidden folder that holds it.
    reported = re.findall(r"check_image_files: (\S+):", done.stderr)
    assert reported
    assert all(relative.startswith(path) for path in reported)


def test_the_example_env_file_is_allowed(tmp_path):
    image = clean_image(tmp_path)
    put(image, "app/.env.example", "NAME=\n")
    assert run(image).returncode == 0


def test_the_virtual_environment_is_not_scanned(tmp_path):
    image = clean_image(tmp_path)
    # Other people's code: a tests folder, a test file, a node_modules folder and a secret-like line.
    put(image, "app/.venv/lib/pkg/tests/test_pkg.py", "token = 'ghp_" + "a" * 36 + "'\n")
    put(image, "app/.venv/lib/pkg/node_modules/x.js", "x\n")
    assert run(image).returncode == 0


CONTENT_CASES = [
    ("demo credential in the app", "app/socboard/demo.py", "password = 'analyst-a-pass'\n", "demo credential or hostile fixture"),
    ("demo credential in the frontend build", "app/frontend/dist/assets/x.js", "var a = 'lead-pass';\n", "demo credential or hostile fixture"),
    ("hostile fixture", "app/frontend/dist/assets/y.js", "'<script>alert(1)</script>'\n", "demo credential or hostile fixture"),
    ("fonts host", "app/frontend/dist/index.html", "<link href='https://fonts.googleapis.com/css'>\n", "Google fonts host"),
    ("fonts static host", "app/frontend/dist/assets/z.css", "url(https://fonts.gstatic.com/s/a.woff)\n", "Google fonts host"),
    ("secret-like line in the readme", "app/README.md", "key: ghp_" + "b" * 36 + "\n", "secret-patterns.txt line"),
    ("secret-like line in the package", "app/socboard/conf.py", "AKIA" + "A" * 16 + "\n", "secret-patterns.txt line"),
    ("private key in the build", "app/frontend/dist/assets/k.js", "-----BEGIN RSA PRIVATE KEY-----\n", "secret-patterns.txt line"),
]


@pytest.mark.parametrize(
    ("relative", "text", "message"), [case[1:] for case in CONTENT_CASES], ids=[case[0] for case in CONTENT_CASES]
)
def test_a_forbidden_content_is_refused_without_printing_it(relative, text, message, tmp_path):
    image = clean_image(tmp_path)
    put(image, relative, text)
    done = run(image)
    assert done.returncode == 1
    assert message in done.stderr
    assert relative in done.stderr
    # The matched value is never printed.
    for secret in re.findall(r"(?:ghp_|AKIA)[A-Za-z0-9]+|analyst-a-pass|lead-pass|fonts\.g\w+\.com|BEGIN RSA", text):
        assert secret not in done.stderr + done.stdout


SERVER_OUTPUT_CASES = ["server/index.js", "wrangler.json", "nitro.json", "_worker.js", "assets/chunk.mjs"]


@pytest.mark.parametrize("relative", SERVER_OUTPUT_CASES)
def test_server_or_worker_output_in_the_build_is_refused(relative, tmp_path):
    image = clean_image(tmp_path)
    put(image, f"app/frontend/dist/{relative}")
    done = run(image)
    assert done.returncode == 1
    assert "server or worker output" in done.stderr


def test_a_missing_app_folder_exits_2(tmp_path):
    (tmp_path / "empty").mkdir()
    done = run(tmp_path / "empty")
    assert done.returncode == 2
    assert "no app/" in done.stderr


def test_the_usage_exits_2():
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 2


def test_every_pattern_in_the_secret_list_compiles_and_the_list_is_not_empty():
    patterns = checker.load_secret_patterns()
    assert len(patterns) > 5
    for _, pattern in patterns:
        assert isinstance(pattern, re.Pattern)


def test_a_pattern_that_does_not_compile_stops_the_check(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "patterns.txt"
    bad.write_text("fine\n(unclosed\n", encoding="utf-8")
    monkeypatch.setattr(checker, "SECRET_PATTERNS", bad)
    image = clean_image(tmp_path)
    assert checker.main(["check_image_files.py", str(image)]) == 2
    assert "does not compile" in capsys.readouterr().err


def test_the_checker_uses_the_standard_library_only():
    imported = set(re.findall(r"^(?:import|from)\s+(\w+)", SCRIPT.read_text(encoding="utf-8"), re.MULTILINE))
    assert imported <= set(sys.stdlib_module_names)


def test_the_checker_passes_on_the_projects_own_app_files(tmp_path):
    """The app-owned files as they are in the repository, laid out as the image holds them."""
    app = tmp_path / "image" / "app"
    app.mkdir(parents=True)
    shutil.copy(ROOT / "README.md", app / "README.md")
    shutil.copytree(ROOT / "socboard", app / "socboard", ignore=shutil.ignore_patterns("__pycache__"))
    done = run(tmp_path / "image")
    assert done.returncode == 0, done.stderr
