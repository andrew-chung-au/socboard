"""Check Docker Compose's rendered configuration of the observability overlay (#292).

    docker compose -f compose.yaml -f compose.observability.yaml config --format json | python observability/ci/rendered.py overlay
    docker compose -f compose.yaml config --format json | python observability/ci/rendered.py base

`overlay` reads the layered configuration and exits 1, naming each problem, unless every port of the five
overlay services is published on 127.0.0.1 with a host port (a port with no host address, or on another one,
would be open to the network), every one of them is on the `stack` network only, and the app's exporter
points at the collector with the 5-second metric interval. `base` reads compose.yaml alone and exits 1
unless it is still the six services of the app's stack with no OpenTelemetry variable, so leaving the
overlay out of the command gives the stack as it was. The text checks of the overlay file are in
tests/test_observability_compose.py; this script is what looks at what Compose actually makes of it, and
check.sh runs it where Docker exists. Standard library only; it prints no value except port numbers.
"""

import json
import sys
from collections.abc import Mapping

OVERLAY_SERVICES = ("otel-collector", "tempo", "loki", "prometheus", "alertmanager")
BASE_SERVICES = {"db", "db-init", "migrate", "seed", "app", "proxy"}
LOOPBACK = "127.0.0.1"


def overlay_problems(config: Mapping) -> list[str]:
    found = []
    services = config.get("services") or {}
    for name in OVERLAY_SERVICES:
        service = services.get(name)
        if not isinstance(service, Mapping):
            found.append(f"{name}: not in the rendered configuration")
            continue
        ports = service.get("ports") or []
        if not ports:
            found.append(f"{name}: publishes no port")
        for port in ports:
            if not isinstance(port, Mapping) or port.get("host_ip") != LOOPBACK or not port.get("published"):
                found.append(f"{name}: a port is not published on {LOOPBACK} with a host port: {port}")
        if sorted(service.get("networks") or []) != ["stack"]:
            found.append(f"{name}: not on the stack network only")
    environment = (services.get("app") or {}).get("environment") or {}
    if environment.get("OTEL_EXPORTER_OTLP_ENDPOINT") != "http://otel-collector:4318":
        found.append("app: the exporter does not point at the collector")
    if str(environment.get("OTEL_METRIC_EXPORT_INTERVAL")) != "5000":
        found.append("app: the metric export interval is not 5000 milliseconds")
    if "deployment.environment.name=" not in str(environment.get("OTEL_RESOURCE_ATTRIBUTES")) or "service.version=" not in str(environment.get("OTEL_RESOURCE_ATTRIBUTES")):
        found.append("app: OTEL_RESOURCE_ATTRIBUTES does not set deployment.environment.name and service.version")
    return found


def base_problems(config: Mapping) -> list[str]:
    found = []
    services = config.get("services") or {}
    if set(services) != BASE_SERVICES:
        found.append(f"the base file's services are {sorted(services)}, not {sorted(BASE_SERVICES)}")
    environment = (services.get("app") or {}).get("environment") or {}
    leaked = sorted(key for key in environment if key.startswith("OTEL_"))
    if leaked:
        found.append(f"the base file sets {leaked} on the app")
    return found


def main(argv: list[str] | None = None, stdin=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ("overlay", "base"):
        print("usage: rendered.py overlay|base  (the JSON of `docker compose config` on standard input)", file=sys.stderr)
        return 2
    try:
        config = json.load(stdin or sys.stdin)
    except ValueError:
        print("observability rendered: refused: standard input is not JSON", file=sys.stderr)
        return 2
    found = (overlay_problems if args[0] == "overlay" else base_problems)(config if isinstance(config, Mapping) else {})
    for line in found:
        print(f"rendered {args[0]}: {line}")
    if not found:
        print(f"rendered {args[0]}: ok")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
