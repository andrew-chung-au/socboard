"""The Dockerfile and .dockerignore (#190), checked from their text, with no Docker.

`check_dockerfile` returns one refusal per broken rule. There is one test per refusal, on a
small Dockerfile text, and one that the real Dockerfile passes. Every value here is synthetic.
"""

import re
from pathlib import Path

import pytest

from socboard.settings import SETTINGS

ROOT = Path(__file__).resolve().parents[1]
SECRET_NAMES = tuple(setting.name for setting in SETTINGS if setting.secret)
START = '["python", "-m", "socboard.server", "--allow-non-loopback-host"]'


def instructions(text):
    """(INSTRUCTION, arguments) for each logical line: comments dropped, continuations joined."""
    joined = re.sub(r"\\[ \t]*\n", " ", text)
    found = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line.partition(" ")
        found.append((word.upper(), rest.strip()))
    return found


def stages(text):
    """The instructions split at each FROM; the last list is the final stage."""
    result = []
    for word, rest in instructions(text):
        if word == "FROM":
            result.append([(word, rest)])
        elif result:
            result[-1].append((word, rest))
    return result


def names_in(rest, name):
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", rest) is not None


def image_of(from_rest):
    parts = [part for part in from_rest.split() if not part.startswith("--")]
    return parts[0]


def check_dockerfile(text, python_version):
    """One message per rule the text breaks; empty when it passes."""
    problems = []
    all_instructions = instructions(text)
    final = stages(text)[-1] if stages(text) else []

    commands = [rest for word, rest in final if word in ("CMD", "ENTRYPOINT")]
    start = " ".join(commands)
    if not commands or "--allow-non-loopback-host" not in start or "socboard.server" not in start:
        problems.append("the start command does not run socboard.server with --allow-non-loopback-host")
    if any("--workers" in rest for word, rest in all_instructions if word in ("CMD", "ENTRYPOINT", "RUN")):
        problems.append("a command uses --workers")
    env_arg = [rest for word, rest in all_instructions if word in ("ENV", "ARG")]
    if any(names_in(rest, "WEB_CONCURRENCY") for rest in env_arg):
        problems.append("WEB_CONCURRENCY is set")
    if any(names_in(rest, "FORWARDED_ALLOW_IPS") for rest in env_arg):
        problems.append("FORWARDED_ALLOW_IPS is set")
    if any(names_in(rest, "SOCBOARD_TRUSTED_PROXIES") for rest in env_arg):
        problems.append("SOCBOARD_TRUSTED_PROXIES is set")
    users = [rest for word, rest in final if word == "USER"]
    if not users or users[-1].split(":")[0].strip().lower() in ("root", "0"):
        problems.append("the last stage runs as root")
    if any(names_in(rest, "VITE_API_BASE_URL") for _, rest in all_instructions):
        problems.append("VITE_API_BASE_URL is set or named")
    if any(word == "ARG" and names_in(rest, "VITE_USE_MOCK") for word, rest in all_instructions):
        problems.append("VITE_USE_MOCK is an ARG")
    for name in SECRET_NAMES:
        if any(names_in(rest, name) for rest in env_arg):
            problems.append(f"{name} is named in an ENV or ARG line")
    if not final:
        problems.append("no FROM")
    else:
        match = re.match(r"python:(\d+)\.(\d+)", image_of(final[0][1]))
        if match is None or f"{match.group(1)}.{match.group(2)}" != python_version.strip():
            problems.append("the final FROM is not a python image of the .python-version minor version")
    return problems


GOOD = f"""FROM python:3.12-slim-bookworm
ENV SOCBOARD_HOST=0.0.0.0
USER 10001:10001
CMD {START}
"""


def test_the_good_text_passes():
    assert check_dockerfile(GOOD, "3.12") == []


REFUSED = [
    ("no start command", GOOD.replace(f"CMD {START}\n", ""), "start command"),
    ("start without the flag", GOOD.replace(', "--allow-non-loopback-host"', ""), "start command"),
    ("start of another module", GOOD.replace("socboard.server", "uvicorn"), "start command"),
    ("--workers in the start command", GOOD.replace("]", ', "--workers", "2"]'), "--workers"),
    ("--workers in a RUN line", GOOD + "RUN uvicorn app --workers 2\n", "--workers"),
    ("WEB_CONCURRENCY in an ENV line", GOOD + "ENV WEB_CONCURRENCY=2\n", "WEB_CONCURRENCY"),
    ("WEB_CONCURRENCY in an ARG line", GOOD + "ARG WEB_CONCURRENCY=2\n", "WEB_CONCURRENCY"),
    ("FORWARDED_ALLOW_IPS to a star", GOOD + "ENV FORWARDED_ALLOW_IPS=*\n", "FORWARDED_ALLOW_IPS"),
    ("FORWARDED_ALLOW_IPS to a proxy", GOOD + "ENV FORWARDED_ALLOW_IPS=10.0.0.2\n", "FORWARDED_ALLOW_IPS"),
    ("FORWARDED_ALLOW_IPS in an ARG line", GOOD + "ARG FORWARDED_ALLOW_IPS\n", "FORWARDED_ALLOW_IPS"),
    ("FORWARDED_ALLOW_IPS in the old ENV form", GOOD + "ENV FORWARDED_ALLOW_IPS *\n", "FORWARDED_ALLOW_IPS"),
    ("SOCBOARD_TRUSTED_PROXIES in an ENV line", GOOD + "ENV SOCBOARD_TRUSTED_PROXIES=10.0.0.2\n", "SOCBOARD_TRUSTED_PROXIES"),
    ("no USER", GOOD.replace("USER 10001:10001\n", ""), "root"),
    ("USER root", GOOD.replace("USER 10001:10001", "USER root"), "root"),
    ("USER 0", GOOD.replace("USER 10001:10001", "USER 0"), "root"),
    ("USER 0:0", GOOD.replace("USER 10001:10001", "USER 0:0"), "root"),
    ("USER root back after a normal user", GOOD.replace("CMD", "USER root\nCMD"), "root"),
    ("VITE_API_BASE_URL set", GOOD + "ENV VITE_API_BASE_URL=http://example.test\n", "VITE_API_BASE_URL"),
    ("VITE_API_BASE_URL as a build argument", GOOD + "ARG VITE_API_BASE_URL\n", "VITE_API_BASE_URL"),
    ("VITE_USE_MOCK as an ARG", GOOD + "ARG VITE_USE_MOCK=false\n", "VITE_USE_MOCK"),
    ("final FROM of another minor version", GOOD.replace("3.12", "3.13"), ".python-version"),
    ("final FROM that is not python", GOOD.replace("python:3.12-slim-bookworm", "debian:bookworm"), ".python-version"),
    ("a FROM with a platform flag of another minor", GOOD.replace("FROM ", "FROM --platform=linux/amd64 ").replace("3.12", "3.11"), ".python-version"),
]


@pytest.mark.parametrize(("text", "message"), [case[1:] for case in REFUSED], ids=[case[0] for case in REFUSED])
def test_a_broken_rule_is_refused(text, message):
    problems = check_dockerfile(text, "3.12")
    assert problems
    assert any(message in problem for problem in problems)


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_every_secret_setting_is_refused_in_an_env_or_arg_line(name):
    for line in (f"ENV {name}=x", f"ARG {name}", f"ENV {name} x"):
        problems = check_dockerfile(GOOD + line + "\n", "3.12")
        assert any(name in problem for problem in problems), line


def test_the_secret_list_is_the_settings_with_secret_true():
    assert SECRET_NAMES
    assert "SOCBOARD_TOKEN_SECRET" in SECRET_NAMES
    assert "SOCBOARD_INGEST_API_KEY" in SECRET_NAMES
    assert "SOCBOARD_DATABASE_URL" in SECRET_NAMES


def test_a_name_inside_another_name_is_not_a_hit():
    assert check_dockerfile(GOOD + "ENV SOCBOARD_TRUSTED_PROXIES_NOTE=1\n", "3.12") == []


def test_a_continued_line_is_read_as_one_instruction():
    text = GOOD + "ENV A=1 \\\n    FORWARDED_ALLOW_IPS=*\n"
    assert any("FORWARDED_ALLOW_IPS" in problem for problem in check_dockerfile(text, "3.12"))


# --- the real files ------------------------------------------------------------------------------


def real_text():
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_the_real_dockerfile_passes():
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8")
    assert check_dockerfile(real_text(), python_version) == []


def test_the_real_dockerfile_builds_the_frontend_with_the_mock_off_and_the_two_build_arguments():
    first = stages(real_text())
    frontend = next(stage for stage in first if "frontend" in stage[0][1].lower().split(" as ")[-1])
    assert any(word == "ENV" and rest == "VITE_USE_MOCK=false" for word, rest in frontend)
    args = [rest for word, rest in frontend if word == "ARG"]
    assert sorted(args) == ["VITE_FEATURE_REASSIGN", "VITE_POLL_INTERVAL_MS"]
    assert any(word == "RUN" and rest == "bun install --frozen-lockfile" for word, rest in frontend)


def test_the_real_dockerfile_installs_only_the_locked_runtime_dependencies():
    runs = [rest for word, rest in instructions(real_text()) if word == "RUN"]
    sync = [rest for rest in runs if rest.startswith("uv sync")]
    assert sync
    assert all("--locked" in rest and "--no-dev" in rest for rest in sync)


def test_the_real_dockerfile_copies_named_paths_only():
    for word, rest in instructions(real_text()):
        if word in ("COPY", "ADD") and "--from=" not in rest:
            sources = rest.split()[:-1]
            assert all(source not in (".", "./", "*") for source in sources), rest


def test_the_real_dockerfile_uses_a_numeric_user_and_the_documented_layout():
    text = real_text()
    assert "USER 10001:10001" in text
    assert "WORKDIR /app" in text
    assert "PATH=/app/.venv/bin:$PATH" in text
    assert "./frontend/dist" in text
    assert "SOCBOARD_HOST=0.0.0.0" in text


def test_the_real_dockerfile_start_command_is_in_exec_form():
    commands = [rest for word, rest in instructions(real_text()) if word == "CMD"]
    assert commands == [START]


# --- .dockerignore -------------------------------------------------------------------------------

REQUIRED_IGNORES = (
    ".git",
    ".scratch",
    "node_modules",
    ".env",
    ".env.*",
    "*.env",
    "!.env.example",
    "*.db",
    "*.db-journal",
    "*.db-wal",
    "*.db-shm",
    "tests",
    "_docs",
    "_session-summaries",
    "_human-tasks",
    "terraform",
    ".github",
    "frontend/dist",
    "frontend/node_modules",
)


def ignore_lines():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


@pytest.mark.parametrize("entry", REQUIRED_IGNORES)
def test_dockerignore_has_the_entry(entry):
    assert entry in ignore_lines()


def test_dockerignore_allows_the_example_env_file_only_after_the_env_patterns():
    lines = ignore_lines()
    for pattern in (".env", ".env.*", "*.env"):
        assert lines.index(pattern) < lines.index("!.env.example")


# --- the labels of the published image (#235) ---------------------------------------------------


def test_the_image_carries_the_source_revision_and_created_labels_from_two_build_arguments():
    final = stages((ROOT / "Dockerfile").read_text(encoding="utf-8"))[-1]
    arguments = {rest.split("=")[0] for word, rest in final if word == "ARG"}
    assert {"GIT_COMMIT", "BUILD_DATE"} <= arguments
    labels = " ".join(rest for word, rest in final if word == "LABEL")
    assert 'org.opencontainers.image.source="https://github.com/' in labels
    assert 'org.opencontainers.image.revision="${GIT_COMMIT}"' in labels
    assert 'org.opencontainers.image.created="${BUILD_DATE}"' in labels


def test_compose_passes_the_build_arguments_to_the_one_build_and_defaults_them_to_unknown():
    import yaml

    build = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))["services"]["app"]["build"]
    assert build["context"] == "."
    assert build["args"] == {"GIT_COMMIT": "${GIT_COMMIT:-unknown}", "BUILD_DATE": "${BUILD_DATE:-unknown}"}
