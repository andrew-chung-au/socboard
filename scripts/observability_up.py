"""`make observability-up` and `make observability-down` (#293): the app stack with the observability layer.

    uv run --locked python scripts/observability_up.py up
    uv run --locked python scripts/observability_up.py down

`up` checks first, and starts nothing when a check fails (exit 2, one line naming the reason): Docker is on
the PATH, and both secrets scripts are there. Then it runs what `make compose-up` runs, with both Compose
files: `scripts/compose_secrets.py` (the stack's secrets), `scripts/observability_secrets.py` (Grafana's
admin password, in `observability.secrets`; never printed), and `docker compose ... up --build --wait`,
which waits until every service with a health check is healthy (the app and Grafana) and every service
without one is running. `down` removes the containers and the network and keeps the volumes, like
`make compose-down`. The Grafana address is printed at the end of `up`; the password is not.
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_SCRIPTS = ("scripts/compose_secrets.py", "scripts/observability_secrets.py")
COMPOSE = (
    "docker", "compose",
    "--env-file", "compose.secrets", "--env-file", "observability.secrets",
    "-f", "compose.yaml", "-f", "compose.observability.yaml",
)  # fmt: skip
WAIT_TIMEOUT = "300"


def main(
    argv: Sequence[str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[[Sequence[str]], int] = lambda command: subprocess.run(command, cwd=ROOT, check=False).returncode,
    root: Path = ROOT,
    environ: dict | None = None,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ("up", "down"):
        print("usage: observability_up.py up|down", file=sys.stderr)
        return 2
    action = args[0]
    if which("docker") is None:
        print(f"observability-{action}: Docker is not installed or not on the PATH; nothing was started", file=sys.stderr)
        return 2
    if action == "up":
        for script in SECRET_SCRIPTS:
            if not (root / script).is_file():
                print(f"observability-up: {script} is missing, so the secrets cannot be made; nothing was started", file=sys.stderr)
                return 2
        for script in SECRET_SCRIPTS:
            code = run([sys.executable, script])
            if code != 0:
                print(f"observability-up: {script} failed (exit {code}); nothing was started", file=sys.stderr)
                return code
        code = run([*COMPOSE, "up", "--build", "--wait", "--wait-timeout", WAIT_TIMEOUT])
        if code == 0:
            port = (os.environ if environ is None else environ).get("STACK_GRAFANA_PORT") or "3000"
            print(f"observability-up: Grafana is on http://127.0.0.1:{port}/ (user admin; the password is in observability.secrets)")
        return code
    return run([*COMPOSE, "down"])


if __name__ == "__main__":
    raise SystemExit(main())
