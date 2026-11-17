"""compose.observability.yaml and the configs it mounts (#292), checked from their text, and from the
rendered configuration where Docker Compose exists.

`problems` returns one refusal per broken rule for a parsed overlay. Each rule has a test on a small copy
that breaks it, and one test that the real file passes. The rendered-config tests are skipped, with a
named reason, where Docker is not installed (the Kiro Web sandbox); CI's verify job has it, so they run
there. Every value here is synthetic.
"""

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "compose.observability.yaml"
BASE = ROOT / "compose.yaml"
SERVICES = {"otel-collector", "tempo", "loki", "prometheus", "alertmanager"}
STORE_VOLUMES = {"tempo-data", "loki-data", "prometheus-data"}
BUDGET_MIB = 1536 - 160  # 1.5 GiB for six services, 160 MiB of it reserved for Grafana (#293)
IMAGE = re.compile(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}")
PORT = re.compile(r"127\.0\.0\.1:\$\{STACK_[A-Z_]+_PORT:-\d+\}:\d+")
MEMORY = re.compile(r"(\d+)([mg])")
# The contract's names (observability/CONTRACT.md): what the collector's allow-list keeps.
RESOURCE = ["service.name", "deployment.environment.name", "service.version"]
ALLOWED = {
    ("trace_statements", "resource"): RESOURCE,
    ("trace_statements", "span"): ["socboard.alert.severity", "socboard.case.id"],
    ("metric_statements", "resource"): RESOURCE,
    ("metric_statements", "datapoint"): ["outcome"],
    ("log_statements", "resource"): RESOURCE,
    ("log_statements", "log"): ["event"],
}
REQUIRED_VARIABLES = {  # the required variables of compose.yaml, so `config` renders; synthetic values
    "SOCBOARD_TOKEN_SECRET": "t" * 40,
    "SOCBOARD_INGEST_API_KEY": "k" * 32,
    "SOCBOARD_SEED_PASSWORD_ANALYST_A": "a" * 14,
    "SOCBOARD_SEED_PASSWORD_ANALYST_B": "b" * 14,
    "SOCBOARD_SEED_PASSWORD_LEAD": "l" * 14,
    "STACK_PG_SUPERUSER_PASSWORD": "s" * 20,
    "STACK_PG_OWNER_PASSWORD": "o" * 20,
    "STACK_PG_APP_PASSWORD": "p" * 20,
}


def load(path=OVERLAY):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def mebibytes(text):
    match = MEMORY.fullmatch(str(text))
    return int(match.group(1)) * (1024 if match.group(2) == "g" else 1) if match else None


def problems(doc):
    found = []
    services = doc.get("services") or {}
    added = set(services) - {"app"}
    if added != SERVICES:
        found.append(f"the overlay adds {sorted(added)}, not {sorted(SERVICES)}")
    total = 0
    for name in sorted(added & SERVICES):
        service = services[name]
        if not IMAGE.fullmatch(str(service.get("image"))):
            found.append(f"{name}: the image is not pinned by tag and index digest")
        if service.get("networks") != ["stack"]:
            found.append(f"{name}: not on the existing stack network only")
        ports = service.get("ports") or []
        if not ports or any(not PORT.fullmatch(str(port)) for port in ports):
            found.append(f"{name}: a port is not published on 127.0.0.1 with a STACK_*_PORT variable and a default")
        limit = mebibytes(service.get("mem_limit"))
        if limit is None:
            found.append(f"{name}: no mem_limit")
        else:
            total += limit
        user = str(service.get("user", "0")).split(":")[0]
        if user in ("0", "root", ""):
            found.append(f"{name}: runs as root (no non-root user)")
        for key in ("privileged", "pid", "network_mode", "cap_add", "devices"):
            if key in service:
                found.append(f"{name}: sets {key}")
        for volume in service.get("volumes") or []:
            if "docker.sock" in str(volume):
                found.append(f"{name}: mounts the Docker socket")
    if total > BUDGET_MIB:
        found.append(f"the five services may use {total} MiB, over the {BUDGET_MIB} MiB left after Grafana's share")
    volumes = doc.get("volumes") or {}
    if set(volumes) != STORE_VOLUMES:
        found.append(f"the named volumes are {sorted(volumes)}, not {sorted(STORE_VOLUMES)}")
    for name, volume in volumes.items():
        options = (volume or {}).get("driver_opts") or {}
        if options.get("type") != "tmpfs" or not re.search(r"(^|,)size=\d+m(,|$)", str(options.get("o"))):
            found.append(f"{name}: not a size-capped volume (a disk limit)")
    app = services.get("app") or {}
    environment = app.get("environment") or {}
    for name in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_RESOURCE_ATTRIBUTES", "OTEL_METRIC_EXPORT_INTERVAL"):
        if name not in environment:
            found.append(f"app: {name} is not set")
    if environment.get("OTEL_EXPORTER_OTLP_ENDPOINT") != "http://otel-collector:4318":
        found.append("app: the exporter does not point at the collector on the stack network")
    if str(environment.get("OTEL_METRIC_EXPORT_INTERVAL")) != "5000":
        found.append("app: the metric export interval is not 5 seconds")
    if set(app) - {"environment", "depends_on"}:
        found.append(f"app: the overlay changes more than its environment and start order: {sorted(set(app) - {'environment', 'depends_on'})}")
    command = " ".join(map(str, (services.get("prometheus") or {}).get("command") or []))
    for flag in ("--web.enable-remote-write-receiver", "--enable-feature=exemplar-storage", "--storage.tsdb.retention.time=24h"):
        if flag not in command:
            found.append(f"prometheus: the command lacks {flag}")
    if not re.search(r"--storage\.tsdb\.retention\.size=\d+MB", command):
        found.append("prometheus: no size limit on the data")
    return found


def broken(change):
    doc = copy.deepcopy(load())
    change(doc)
    return problems(doc)


def test_the_real_overlay_passes_every_rule():
    assert problems(load()) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda d: d["services"].pop("loki"), "adds"),
        (lambda d: d["services"].update({"grafana": {}}), "adds"),
        (lambda d: d["services"]["tempo"].update({"image": "grafana/tempo:3.1.1"}), "not pinned"),
        (lambda d: d["services"]["tempo"].update({"image": "grafana/tempo@sha256:" + "0" * 64}), "not pinned"),
        (lambda d: d["services"]["tempo"].update({"networks": ["default"]}), "stack network"),
        (lambda d: d["services"]["tempo"].update({"ports": ["3200:3200"]}), "127.0.0.1"),
        (lambda d: d["services"]["tempo"].update({"ports": ["0.0.0.0:${STACK_TEMPO_PORT:-3200}:3200"]}), "127.0.0.1"),
        (lambda d: d["services"]["tempo"].update({"ports": ["127.0.0.1:3200:3200"]}), "STACK_*_PORT"),
        (lambda d: d["services"]["tempo"].update({"ports": []}), "127.0.0.1"),
        (lambda d: d["services"]["tempo"].pop("mem_limit"), "no mem_limit"),
        (lambda d: d["services"]["tempo"].update({"mem_limit": "2g"}), "may use"),
        (lambda d: d["services"]["tempo"].pop("user"), "root"),
        (lambda d: d["services"]["tempo"].update({"user": "0:0"}), "root"),
        (lambda d: d["services"]["tempo"].update({"privileged": True}), "privileged"),
        (lambda d: d["services"]["tempo"]["volumes"].append("/var/run/docker.sock:/var/run/docker.sock"), "Docker socket"),
        (lambda d: d["volumes"]["loki-data"].update({"driver_opts": {}}), "disk limit"),
        (lambda d: d["volumes"]["loki-data"]["driver_opts"].update({"o": "uid=10001"}), "disk limit"),
        (lambda d: d["volumes"].update({"extra": {}}), "named volumes"),
        (lambda d: d["services"]["app"]["environment"].pop("OTEL_EXPORTER_OTLP_ENDPOINT"), "OTEL_EXPORTER_OTLP_ENDPOINT"),
        (lambda d: d["services"]["app"]["environment"].update({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://example.com:4318"}), "collector"),
        (lambda d: d["services"]["app"]["environment"].update({"OTEL_METRIC_EXPORT_INTERVAL": "60000"}), "5 seconds"),
        (lambda d: d["services"]["app"].update({"image": "other"}), "more than its environment"),
        (lambda d: d["services"]["prometheus"]["command"].remove("--enable-feature=exemplar-storage"), "exemplar-storage"),
        (lambda d: d["services"]["prometheus"]["command"].remove("--storage.tsdb.retention.time=24h"), "retention.time"),
        (lambda d: d["services"]["prometheus"]["command"].remove("--storage.tsdb.retention.size=128MB"), "size limit"),
    ],
)
def test_each_refused_case_fails(change, expected):
    found = broken(change)
    assert any(expected in line for line in found), found


def test_the_five_services_fit_the_memory_budget_with_grafanas_share():
    doc = load()
    total = sum(mebibytes(doc["services"][name]["mem_limit"]) for name in SERVICES)
    assert total + 160 <= 1536


def test_every_file_the_overlay_mounts_exists():
    doc = load()
    for service in doc["services"].values():
        for volume in service.get("volumes") or []:
            source = str(volume).split(":")[0]
            if source.startswith("./"):
                assert (ROOT / source).exists(), source
    assert (ROOT / "observability" / "alerts" / "queue_latency.yml").exists()  # the rule file #296 added


# --- the configs the overlay mounts ------------------------------------------------------------------


def keep_lists(config):
    """{(pipeline statements key, context): the keep_keys list} read from the collector's transform processor."""
    found = {}
    processor = config["processors"]["transform/allowlist"]
    for key in ("trace_statements", "metric_statements", "log_statements"):
        for group in processor[key]:
            for statement in group["statements"]:
                match = re.fullmatch(r'keep_keys\(attributes, \[(.*)\]\)', statement)
                if match:
                    found[(key, group["context"])] = re.findall(r'"([^"]+)"', match.group(1))
    return found


def test_the_collector_keeps_exactly_the_contracts_names_and_every_one_is_in_the_contract():
    config = yaml.safe_load((ROOT / "observability" / "otel" / "collector.yaml").read_text(encoding="utf-8"))
    assert keep_lists(config) == ALLOWED
    contract = (ROOT / "observability" / "CONTRACT.md").read_text(encoding="utf-8")
    for names in ALLOWED.values():
        for name in names:
            assert f"`{name}`" in contract, name
    # Every signal runs through the allow-list, and nothing else is exported from a pipeline without it.
    for pipeline in config["service"]["pipelines"].values():
        assert pipeline["processors"] == ["transform/allowlist"]
    # Scope and span-event attributes are dropped whole.
    processor = config["processors"]["transform/allowlist"]
    for key, context in (("trace_statements", "scope"), ("trace_statements", "spanevent"), ("metric_statements", "scope"), ("log_statements", "scope")):
        assert any(group["context"] == context and group["statements"] == ['delete_matching_keys(attributes, ".*")'] for group in processor[key]), (key, context)


def test_the_collector_receives_otlp_http_and_routes_each_signal_to_its_store():
    config = yaml.safe_load((ROOT / "observability" / "otel" / "collector.yaml").read_text(encoding="utf-8"))
    assert config["receivers"]["otlp"]["protocols"] == {"http": {"endpoint": "0.0.0.0:4318"}}
    pipelines = config["service"]["pipelines"]
    assert pipelines["traces"]["exporters"] == ["otlphttp/tempo"]
    assert pipelines["logs"]["exporters"] == ["otlphttp/loki"]
    assert pipelines["metrics"]["exporters"] == ["prometheusremotewrite"]
    assert config["exporters"]["otlphttp/loki"]["endpoint"] == "http://loki:3100/otlp"
    assert config["exporters"]["prometheusremotewrite"]["endpoint"] == "http://prometheus:9090/api/v1/write"


def test_prometheus_loads_the_alert_files_and_evaluates_every_15_seconds_and_alertmanager_waits_5():
    prometheus = yaml.safe_load((ROOT / "observability" / "prometheus" / "prometheus.yml").read_text(encoding="utf-8"))
    assert prometheus["global"]["evaluation_interval"] == "15s"
    assert prometheus["rule_files"] == ["/etc/prometheus/alerts/*.yml"]
    assert prometheus["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager:9093"]
    doc = load()
    assert "./observability/alerts:/etc/prometheus/alerts:ro" in doc["services"]["prometheus"]["volumes"]
    alertmanager = yaml.safe_load((ROOT / "observability" / "alertmanager" / "alertmanager.yml").read_text(encoding="utf-8"))
    assert alertmanager["route"]["group_wait"] == "5s"
    assert alertmanager["route"]["receiver"] in {receiver["name"] for receiver in alertmanager["receivers"]}


def test_each_store_keeps_24_hours_at_most():
    loki = yaml.safe_load((ROOT / "observability" / "loki" / "loki.yaml").read_text(encoding="utf-8"))
    assert loki["limits_config"]["retention_period"] == "24h" and loki["compactor"]["retention_enabled"] is True
    assert loki["limits_config"]["allow_structured_metadata"] is True
    tempo = yaml.safe_load((ROOT / "observability" / "tempo" / "tempo.yaml").read_text(encoding="utf-8"))
    assert tempo["overrides"]["defaults"]["compaction"]["block_retention"] == "24h"


def test_the_base_stack_file_does_not_mention_the_overlay():
    text = BASE.read_text(encoding="utf-8")
    assert "otel" not in text.lower() and "observability" not in text.lower()


# --- the rendered configuration (Docker Compose needed) ----------------------------------------------


def compose_json(*files):
    command = ["docker", "compose"]
    for file in files:
        command += ["-f", file]
    result = subprocess.run(
        [*command, "config", "--format", "json"],
        cwd=ROOT,
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"), **REQUIRED_VARIABLES},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-500:]
    return json.loads(result.stdout)


def has_compose():
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_compose = pytest.mark.skipif(not has_compose(), reason="docker compose is not installed here; CI's verify job renders the configuration")


@needs_compose
def test_rendered_every_published_port_of_the_overlay_is_on_127_0_0_1_with_a_host_port():
    config = compose_json("compose.yaml", "compose.observability.yaml")
    for name in SERVICES:
        ports = config["services"][name]["ports"]
        assert ports, name
        for port in ports:
            assert port["host_ip"] == "127.0.0.1" and port["published"], (name, port)
    assert {port["published"] for name in SERVICES for port in config["services"][name]["ports"]} == {"4318", "13133", "3200", "3100", "9090", "9093"}


@needs_compose
def test_rendered_the_app_points_at_the_collector_and_everything_is_on_the_stack_network():
    config = compose_json("compose.yaml", "compose.observability.yaml")
    environment = config["services"]["app"]["environment"]
    assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4318"
    assert environment["OTEL_METRIC_EXPORT_INTERVAL"] == "5000"
    assert environment["OTEL_RESOURCE_ATTRIBUTES"] == "deployment.environment.name=local,service.version=unknown"
    for name in SERVICES:
        assert list(config["services"][name]["networks"]) == ["stack"], name
    assert config["name"] == "socboard"


@needs_compose
def test_rendered_the_base_file_alone_has_no_observability_service():
    alone = compose_json("compose.yaml")
    assert set(alone["services"]) == {"db", "db-init", "migrate", "seed", "app", "proxy"}
    assert not any(key.startswith("OTEL_") for key in alone["services"]["app"]["environment"])


@needs_compose
def test_rendered_a_second_stack_can_move_every_port_by_variable(monkeypatch):
    monkeypatch.setitem(REQUIRED_VARIABLES, "STACK_OTLP_PORT", "14318")
    monkeypatch.setitem(REQUIRED_VARIABLES, "STACK_TEMPO_PORT", "13200")
    config = compose_json("compose.yaml", "compose.observability.yaml")
    assert {port["published"] for port in config["services"]["otel-collector"]["ports"]} == {"14318", "13133"}
    assert config["services"]["tempo"]["ports"][0]["published"] == "13200"
