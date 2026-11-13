"""Probe the backend address for `make backend-run` (#80). Standard library only.

    .venv/bin/python scripts/backend_probe.py port     # is anything listening on the address?
    .venv/bin/python scripts/backend_probe.py health   # does GET /api/health answer 200 {"status": "ok"}?

The address comes from SOCBOARD_HOST and SOCBOARD_PORT, checked by the server's own rules
(`socboard.server.resolve_bind`), so a value the server rejects never reaches a socket or a URL.
Exit codes: 0 = port free / healthy; 1 = port in use / not healthy (port prints why);
3 = the server would reject the settings: nothing is probed, and the server prints its own message.
"""

import json
import os
import socket
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from socboard.server import BindError, resolve_bind  # noqa: E402  (stdlib-only at import time)


def listening(host: str, port: int) -> bool:
    # Fail closed: only "connection refused" on every address means free; a timeout counts as in use.
    for family, kind, proto, _name, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        try:
            with socket.socket(family, kind, proto) as sock:
                sock.settimeout(2)
                sock.connect(address)
            return True
        except socket.timeout:
            return True
        except OSError:
            continue
    return False


def healthy(host: str, port: int) -> bool:
    netloc = f"[{host}]" if ":" in host else host
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy
    try:
        with opener.open(f"http://{netloc}:{port}/api/health", timeout=1) as response:
            return response.status == 200 and json.loads(response.read(1024)) == {"status": "ok"}
    except (OSError, ValueError):  # refused, 404 (HTTPError), timeout, bad JSON
        return False


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("port", "health"):
        print("usage: backend_probe.py port|health", file=sys.stderr)
        return 2
    try:
        host, port = resolve_bind(os.environ)
    except BindError:
        return 3
    if argv[1] == "port":
        if listening(host, port):
            print(f"backend-run: port {port} on {host} is already in use; nothing started")
            return 1
        return 0
    return 0 if healthy(host, port) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
