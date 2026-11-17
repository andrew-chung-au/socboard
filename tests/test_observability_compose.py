"""compose.observability.yaml and the configs it mounts (#292), checked from their text, and the checker
of Docker Compose's rendered configuration (observability/ci/rendered.py), tried on synthetic renderings.

`problems` returns one refusal per broken rule for a parsed overlay. Each rule has a test on a small copy
that breaks it, and one test that the real file passes. Nothing here needs Docker, so nothing is skipped:
the real rendering is checked by observability/ci/check.sh, which pipes `docker compose config` into
rendered.py in the `socboard observability` run. Every value here is synthetic.
"""

import copy
import importlib.util
import io
import json
import re
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



# --- the rendered-configuration checker (observability/ci/rendered.py) --------------------------------

spec = importlib.util.spec_from_file_location("observability_rendered", ROOT / "observability" / "ci" / "rendered.py")
rendered = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rendered)


def port(published, host_ip="127.0.0.1", target=1):
    return {"mode": "ingress", "host_ip": host_ip, "target": target, "published": str(published), "protocol": "tcp"}


def good_rendering():
    """The shape `docker compose config --format json` gives for the layered stack, with synthetic values."""
    published = {"otel-collector": ["4318", "13133"], "tempo": ["3200"], "loki": ["3100"], "prometheus": ["9090"], "alertmanager": ["9093"]}
    services = {name: {"ports": [port(p) for p in ports], "networks": {"stack": None}} for name, ports in published.items()}
    services["app"] = {
        "environment": {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4318",
            "OTEL_METRIC_EXPORT_INTERVAL": "5000",
            "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment.name=local,service.version=unknown",
        }
    }
    return {"name": "socboard", "services": services}


def run_checker(mode, config):
    out = io.StringIO()
    import contextlib

    with contextlib.redirect_stdout(out):
        code = rendered.main([mode], stdin=io.StringIO(json.dumps(config)))
    return code, out.getvalue()


def test_a_good_rendering_passes_the_overlay_check():
    assert run_checker("overlay", good_rendering()) == (0, "rendered overlay: ok\n")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda c: c["services"]["tempo"].update({"ports": [port(3200, host_ip="0.0.0.0")]}), "tempo: a port is not published on 127.0.0.1"),
        (lambda c: c["services"]["tempo"].update({"ports": [{"mode": "ingress", "target": 3200, "published": "3200", "protocol": "tcp"}]}), "tempo: a port is not published on 127.0.0.1"),
        (lambda c: c["services"]["tempo"].update({"ports": [port("")]}), "tempo: a port is not published on 127.0.0.1"),
        (lambda c: c["services"]["tempo"].update({"ports": []}), "tempo: publishes no port"),
        (lambda c: c["services"]["loki"].update({"networks": {"stack": None, "default": None}}), "loki: not on the stack network only"),
        (lambda c: c["services"].pop("alertmanager"), "alertmanager: not in the rendered configuration"),
        (lambda c: c["services"]["app"]["environment"].update({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://example.com"}), "the exporter does not point at the collector"),
        (lambda c: c["services"]["app"]["environment"].update({"OTEL_METRIC_EXPORT_INTERVAL": "60000"}), "5000 milliseconds"),
        (lambda c: c["services"]["app"]["environment"].update({"OTEL_RESOURCE_ATTRIBUTES": "service.version=x"}), "OTEL_RESOURCE_ATTRIBUTES"),
    ],
)
def test_each_refused_rendering_fails_the_overlay_check_and_names_the_service(change, expected):
    config = good_rendering()
    change(config)
    code, output = run_checker("overlay", config)
    assert code == 1 and expected in output, output


def test_the_base_check_passes_the_stack_as_it_was_and_fails_an_observability_service_or_variable():
    base = {"services": {name: {} for name in rendered.BASE_SERVICES}}
    base["services"]["app"] = {"environment": {"SOCBOARD_MODE": "deployed"}}
    assert run_checker("base", base) == (0, "rendered base: ok\n")
    base["services"]["tempo"] = {}
    code, output = run_checker("base", base)
    assert code == 1 and "services are" in output
    del base["services"]["tempo"]
    base["services"]["app"]["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://x"
    code, output = run_checker("base", base)
    assert code == 1 and "OTEL_EXPORTER_OTLP_ENDPOINT" in output


def test_the_checker_refuses_bad_input_with_exit_2(capsys):
    assert rendered.main(["nonsense"], stdin=io.StringIO("{}")) == 2
    assert rendered.main(["overlay"], stdin=io.StringIO("not json")) == 2
    assert rendered.main([], stdin=io.StringIO("{}")) == 2
    assert rendered.main(["overlay"], stdin=io.StringIO("[]")) == 1  # JSON, but not a configuration: every service is missing
    assert "refused" in capsys.readouterr().err
