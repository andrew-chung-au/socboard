"""compose.yaml and its files (#221), checked from their text, with no Docker.

`problems` returns one refusal per broken rule for a parsed Compose file. Each rule has a test on a
small copy that breaks it, and one test that the real file passes. Every value here is synthetic.
"""

import copy
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
SERVICES = {"db", "db-init", "migrate", "seed", "app", "proxy"}
SUPERUSER = "STACK_PG_SUPERUSER_PASSWORD"
REQUIRED = (
    "SOCBOARD_TOKEN_SECRET",
    "SOCBOARD_INGEST_API_KEY",
    "SOCBOARD_SEED_PASSWORD_ANALYST_A",
    "SOCBOARD_SEED_PASSWORD_ANALYST_B",
    "SOCBOARD_SEED_PASSWORD_LEAD",
    SUPERUSER,
    "STACK_PG_OWNER_PASSWORD",
    "STACK_PG_APP_PASSWORD",
)
ORDER = {  # service: the one it waits for, and how
    "db-init": ("db", "service_healthy"),
    "migrate": ("db-init", "service_completed_successfully"),
    "seed": ("migrate", "service_completed_successfully"),
    "app": ("seed", "service_completed_successfully"),
    "proxy": ("app", "service_healthy"),
}


def load():
    text = COMPOSE.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def flat(value):
    return yaml.safe_dump(value)


def problems(doc, text):
    found = []
    services = doc["services"]
    if set(services) != SERVICES:
        found.append(f"services are {sorted(services)}")
        return found
    for name, service in services.items():
        for port in service.get("ports", []):
            if not str(port).startswith("127.0.0.1:"):
                found.append(f"{name} publishes {port} (loopback only)")
        if service.get("privileged") or service.get("network_mode") == "host":
            found.append(f"{name} is privileged or on the host network")
    if {name for name, s in services.items() if "ports" in s} != {"app", "proxy"}:
        found.append("only app and proxy publish a port")
    if re.search(r"FORWARDED_ALLOW_IPS\s*[:=]\s*['\"]?\*", text):
        found.append("FORWARDED_ALLOW_IPS is *")
    if "FORWARDED_ALLOW_IPS" in flat(services["app"]):
        found.append("app sets FORWARDED_ALLOW_IPS (leave uvicorn's default)")
    holders = {name for name, s in services.items() if SUPERUSER in flat(s)}
    if holders != {"db", "db-init"}:
        found.append(f"the superuser's password is held by {sorted(holders)}")
    for name, service in services.items():
        for key in ("command", "entrypoint", "healthcheck"):
            if "PASSWORD" in flat(service.get(key, "")):
                found.append(f"{name} {key} names a password")
    for name in REQUIRED:
        if f"${{{name}:?" not in text:
            found.append(f"{name} is not required with the :? form")
        if f"${{{name}:-" in text or f"${{{name}-" in text:
            found.append(f"{name} has a default")
    for name, (after, condition) in ORDER.items():
        if services[name].get("depends_on", {}).get(after, {}).get("condition") != condition:
            found.append(f"{name} does not wait for {after} ({condition})")
    if not services["db"].get("healthcheck"):
        found.append("db has no health check")
    if not services["app"].get("healthcheck"):
        found.append("app has no health check")
    if "command" in services["app"] or "entrypoint" in services["app"]:
        found.append("app overrides the image's start command")
    if services["app"]["environment"].get("SOCBOARD_MODE") != "deployed":
        found.append("app is not in deployed mode")
    if len({services[n]["image"] for n in ("db-init", "migrate", "seed", "app")}) != 1:
        found.append("db-init, migrate, seed and app use different images")
    owner, app_role = "socboard_owner:", "socboard_app:"
    if owner not in services["migrate"]["environment"]["SOCBOARD_DATABASE_URL"]:
        found.append("migrate does not connect as the owner role")
    for name in ("seed", "app"):
        if app_role not in services[name]["environment"]["SOCBOARD_DATABASE_URL"]:
            found.append(f"{name} does not connect as the application role")
    proxy_address = services["proxy"]["networks"]["stack"]["ipv4_address"]
    if services["app"]["environment"].get("SOCBOARD_TRUSTED_PROXIES") != proxy_address:
        found.append("app does not trust exactly the proxy's address")
    if not proxy_address.startswith("10.231.77.") or not doc["networks"]["stack"]["ipam"]["config"][0]["subnet"].startswith("10.231.77."):
        found.append("the proxy's address is outside the network's fixed subnet")
    mounts = services["db"]["volumes"]
    if mounts != ["${STACK_PGDATA_DIR:-socboard-pgdata}:/var/lib/postgresql/data"]:
        found.append("db's data directory is not the configurable one")
    if services["db"]["environment"].get("PGDATA") != "/var/lib/postgresql/data/pgdata":
        found.append("PGDATA is not a subdirectory of the mount")
    return found


def test_the_real_file_passes_every_rule():
    doc, text = load()
    assert problems(doc, text) == []


def broken(change):
    doc, text = load()
    doc = copy.deepcopy(doc)
    text = change(doc, text) or text
    return problems(doc, text)


@pytest.mark.parametrize("published", ["0.0.0.0:8000:8000", "8000:8000", "8000"])
def test_a_port_not_on_loopback_is_refused(published):
    assert any("loopback" in p for p in broken(lambda doc, text: doc["services"]["app"].update(ports=[published])))


def test_a_published_database_port_is_refused():
    assert broken(lambda doc, text: doc["services"]["db"].update(ports=["127.0.0.1:5432:5432"]))


@pytest.mark.parametrize("key, value", [("privileged", True), ("network_mode", "host")])
def test_a_privileged_or_host_network_service_is_refused(key, value):
    assert any("privileged" in p for p in broken(lambda doc, text: doc["services"]["app"].update({key: value})))


def test_a_wildcard_forwarded_list_is_refused():
    assert any("FORWARDED_ALLOW_IPS" in p for p in broken(lambda doc, text: text + "\n#  FORWARDED_ALLOW_IPS: '*'\n"))


def test_the_superuser_password_in_the_app_is_refused():
    def change(doc, text):
        doc["services"]["app"]["environment"]["X"] = "${" + SUPERUSER + ":?x}"

    assert any("superuser" in p for p in broken(change))


def test_a_password_on_a_command_line_is_refused():
    def change(doc, text):
        doc["services"]["migrate"]["command"] = ["psql", "-W", "${STACK_PG_APP_PASSWORD}"]

    assert any("command names a password" in p for p in broken(change))


def test_a_default_for_a_required_variable_is_refused():
    assert any("has a default" in p for p in broken(lambda doc, text: text.replace("${STACK_PG_APP_PASSWORD:?", "${STACK_PG_APP_PASSWORD:-x}${STACK_PG_APP_PASSWORD:?", 1)))


def test_a_missing_wait_is_refused():
    def change(doc, text):
        doc["services"]["app"]["depends_on"] = {}

    assert any("app does not wait" in p for p in broken(change))


def test_an_overridden_start_command_is_refused():
    assert any("start command" in p for p in broken(lambda doc, text: doc["services"]["app"].update(command=["python"])))


def test_the_wrong_role_for_the_migration_is_refused():
    def change(doc, text):
        env = doc["services"]["migrate"]["environment"]
        env["SOCBOARD_DATABASE_URL"] = env["SOCBOARD_DATABASE_URL"].replace("socboard_owner", "socboard_app")

    assert any("owner role" in p for p in broken(change))


def test_an_untrusted_proxy_address_is_refused():
    def change(doc, text):
        doc["services"]["app"]["environment"]["SOCBOARD_TRUSTED_PROXIES"] = "10.231.77.99"

    assert any("proxy's address" in p for p in broken(change))


def test_a_different_image_for_db_init_is_refused():
    assert any("different images" in p for p in broken(lambda doc, text: doc["services"]["db-init"].update(image="postgres:16.15")))


# --- compose/db_init.py -------------------------------------------------------------------------

OWNER_PASSWORD, APP_PASSWORD = "OwnerSecretForTest1", "AppSecretForTest2"


def load_db_init():
    spec = importlib.util.spec_from_file_location("db_init", ROOT / "compose" / "db_init.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeConnection:
    """Records each statement as text; `roles_exist` is what the existence checks answer."""

    def __init__(self, roles_exist):
        self.statements = []
        self.roles_exist = roles_exist
        self.pgconn = self

    def encrypt_password(self, password, user, algorithm):
        assert algorithm == b"scram-sha-256"
        return b"SCRAM-SHA-256$4096:c2FsdA==$" + user + b"-verifier"

    def execute(self, query, params=None):
        self.statements.append(query if isinstance(query, str) else query.as_string(None))
        self.last = params
        return self

    def fetchone(self):
        return (1,) if self.roles_exist else None


@pytest.mark.parametrize("roles_exist", [False, True])
def test_db_init_sends_verifiers_never_a_password_and_makes_no_superuser(roles_exist):
    db_init = load_db_init()
    environ = {"STACK_PG_OWNER_PASSWORD": OWNER_PASSWORD, "STACK_PG_APP_PASSWORD": APP_PASSWORD}
    conn = FakeConnection(roles_exist)
    db_init.set_up_roles(conn, environ)
    db_init.grant(conn)
    sent = "\n".join(conn.statements)
    assert OWNER_PASSWORD not in sent and APP_PASSWORD not in sent
    verb = "ALTER" if roles_exist else "CREATE"
    assert f'{verb} ROLE "socboard_owner" LOGIN NOSUPERUSER PASSWORD \'SCRAM-SHA-256$4096:c2FsdA==$socboard_owner-verifier\'' in sent
    assert f'{verb} ROLE "socboard_app" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD' in sent
    assert "SUPERUSER" not in sent.replace("NOSUPERUSER", "")
    assert 'ALTER DEFAULT PRIVILEGES FOR ROLE "socboard_owner"' in sent
    assert ("CREATE DATABASE" in sent) is (not roles_exist)


def test_db_init_names_the_usual_cause_when_it_cannot_log_in_and_prints_no_driver_text(monkeypatch, capsys):
    import psycopg

    db_init = load_db_init()

    def refuse(*args, **kwargs):
        raise psycopg.OperationalError('password authentication failed for user "postgres" at host "db"')

    monkeypatch.setattr(db_init.psycopg, "connect", refuse)
    assert db_init.main({"STACK_PG_SUPERUSER_PASSWORD": "x"}) == 1
    shown = capsys.readouterr()
    assert "secrets file was deleted" in shown.err and "password authentication" not in shown.out + shown.err


def test_the_proxy_is_plain_http_to_the_app():
    caddyfile = (ROOT / "compose" / "Caddyfile").read_text(encoding="utf-8")
    assert "auto_https off" in caddyfile and ":80 {" in caddyfile and "reverse_proxy app:8000" in caddyfile


def test_the_database_image_is_the_tag_the_postgres_job_uses():
    doc, _ = load()
    workflow = (ROOT / ".github" / "workflows" / "postgres.yml").read_text(encoding="utf-8")
    tag = re.search(r"image: (postgres:16\.\d+)", workflow).group(1)
    assert doc["services"]["db"]["image"].startswith(tag + "@sha256:")
