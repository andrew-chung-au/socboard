"""compose.yaml and its files (#221), checked from their text, with no Docker.

`problems` returns one refusal per broken rule for a parsed Compose file. Each rule has a test on a
small copy that breaks it, and one test that the real file passes. Every value here is synthetic.
"""

import copy
import importlib.util
import ipaddress
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


NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def expand_text(value, env):
    """Compose's interpolation for a string: ${V}, $V, ${V:-d}, ${V-d}, nested defaults and $$ (#271).

    Only what compose.yaml uses. A ${V:?message} is left as it is when V is unset or empty (the :? rule
    is checked on the file's text); set, it gives the value. The default of a missing variable may
    itself hold a variable.
    """
    out, i = [], 0
    while i < len(value):
        if value.startswith("$$", i):
            out.append("$")
            i += 2
        elif value.startswith("${", i):
            depth, j = 1, i + 2
            while j < len(value) and depth:
                depth += {"{": 1, "}": -1}.get(value[j], 0)
                j += 1
            if depth:
                raise ValueError(f"unterminated ${{ in {value!r}")
            inner = value[i + 2 : j - 1]
            name = NAME.match(inner)
            if not name:
                raise ValueError(f"bad variable name in {value!r}")
            rest = inner[name.end() :]
            current = env.get(name.group())
            if rest == "":
                out.append(current or "")
            elif rest.startswith(":-"):
                out.append(current if current else expand_text(rest[2:], env))
            elif rest.startswith("-"):
                out.append(current if current is not None else expand_text(rest[1:], env))
            elif rest.startswith(":?") or rest.startswith("?"):
                out.append(current if current else value[i:j])
            else:
                raise ValueError(f"unsupported form in {value!r}")
            i = j
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def expand(value, env):
    """The parsed file with every string interpolated by env; the original is not changed."""
    if isinstance(value, str):
        return expand_text(value, env)
    if isinstance(value, list):
        return [expand(item, env) for item in value]
    if isinstance(value, dict):
        return {key: expand(item, env) for key, item in value.items()}
    return value


def default_values():
    """What the real file's own defaults give: the project name and the network prefix (the dev values).

    Read from the real file, not from the copy under test, so a copy that changes its subnet is refused.
    """
    doc, _ = load()
    expanded = expand(doc, {})
    return expanded["name"], str(expanded["networks"]["stack"]["ipam"]["config"][0]["subnet"]).removesuffix(".0/24")


def gateway_problems(config, proxy_address, prefix):
    """The stack network's pinned gateway (#261): set, `<prefix>.1`, inside the subnet, outside ip_range."""
    gateway = config.get("gateway")
    if gateway is None or str(gateway).strip() == "":
        return ["the stack network has no gateway pinned"]
    try:
        address = ipaddress.IPv4Address(str(gateway))
        subnet = ipaddress.IPv4Network(str(config["subnet"]))
        pool = ipaddress.IPv4Network(str(config["ip_range"]))
        proxy = ipaddress.IPv4Address(str(proxy_address))
    except (ValueError, KeyError):
        return ["the gateway, the subnet, the ip_range or the proxy address is not a valid IPv4 value"]
    found = []
    if str(address) != f"{prefix}.1":
        found.append(f"the gateway is {address}, not {prefix}.1")
    if address not in subnet:
        found.append("the gateway is outside the subnet")
    if address in pool:
        found.append("the gateway is inside ip_range, where Docker hands out container addresses")
    if address in (subnet.network_address, subnet.broadcast_address):
        found.append("the gateway is the network or the broadcast address")
    if address == proxy:
        found.append("the gateway is the proxy's address")
    if proxy in pool:
        found.append("the proxy's address is inside ip_range, so Docker could give it to another container")
    return found


def network_problems(doc, prefix, env):
    """The five addresses and the project name follow the variables, one pool entry only (#271)."""
    found = []
    entries = doc["networks"]["stack"]["ipam"]["config"]
    if len(entries) != 1:
        return [f"the stack network has {len(entries)} pool entries, not exactly one"]
    config = entries[0]
    proxy_address = doc["services"]["proxy"]["networks"]["stack"]["ipv4_address"]
    expected = {
        "subnet": f"{prefix}.0/24",
        "ip_range": f"{prefix}.128/25",
    }
    for key, want in expected.items():
        if str(config.get(key)) != want:
            found.append(f"the network's {key} is {config.get(key)}, not {want}")
    if proxy_address != f"{prefix}.10":
        found.append(f"the proxy's address is {proxy_address}, not {prefix}.10")
    want_trusted = env.get("STACK_TRUSTED_PROXIES") or proxy_address
    if doc["services"]["app"]["environment"].get("SOCBOARD_TRUSTED_PROXIES") != want_trusted:
        found.append("app does not trust exactly the proxy's address" if not env.get("STACK_TRUSTED_PROXIES") else "app does not trust STACK_TRUSTED_PROXIES")
    return found + gateway_problems(config, proxy_address, prefix)


def problems(doc, text, env=None):
    env = env or {}
    raw = doc
    default_name, default_prefix = default_values()
    doc = expand(raw, env)
    prefix = env.get("STACK_NET") or default_prefix
    found = []
    if doc.get("name") != (env.get("STACK_PROJECT") or default_name):
        found.append(f"the project name is {doc.get('name')}, not {env.get('STACK_PROJECT') or default_name}")
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
    if "FORWARDED_ALLOW_IPS" in flat(raw["services"]["app"]):
        found.append("app sets FORWARDED_ALLOW_IPS (leave uvicorn's default)")
    holders = {name for name, s in raw["services"].items() if SUPERUSER in flat(s)}
    if holders != {"db", "db-init"}:
        found.append(f"the superuser's password is held by {sorted(holders)}")
    for name, service in raw["services"].items():
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
    found += network_problems(doc, prefix, env)
    mounts = raw["services"]["db"]["volumes"]
    if mounts != ["${STACK_PGDATA_DIR:-socboard-pgdata}:/var/lib/postgresql/data"]:
        found.append("db's data directory is not the configurable one")
    if services["db"]["environment"].get("PGDATA") != "/var/lib/postgresql/data/pgdata":
        found.append("PGDATA is not a subdirectory of the mount")
    return found


def test_the_real_file_passes_every_rule():
    doc, text = load()
    assert problems(doc, text) == []


def broken(change, env=None):
    doc, text = load()
    doc = copy.deepcopy(doc)
    text = change(doc, text) or text
    return problems(doc, text, env)


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


# --- the stack network's gateway (#261) ----------------------------------------------------------


def set_ipam(**changes):
    def change(doc, text):
        config = doc["networks"]["stack"]["ipam"]["config"][0]
        for key, value in changes.items():
            if value is None:
                config.pop(key, None)
            else:
                config[key] = value

    return change


def default_gateway():
    """The gateway the file's own defaults give; the README names it too."""
    doc, _ = load()
    return expand(doc, {})["networks"]["stack"]["ipam"]["config"][0]["gateway"]


def test_the_real_gateway_is_pinned_inside_the_subnet_and_outside_the_pool():
    doc, _ = load()
    config = expand(doc, {})["networks"]["stack"]["ipam"]["config"][0]
    _, prefix = default_values()
    assert config["gateway"] == f"{prefix}.1"
    assert config["ip_range"] == f"{prefix}.128/25" and config["subnet"] == f"{prefix}.0/24"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (set_ipam(gateway=None), "no gateway pinned"),
        (set_ipam(gateway=""), "no gateway pinned"),
        (set_ipam(gateway="10.231.77.2"), "not 10.231.77.1"),
        (set_ipam(gateway="10.231.78.1"), "outside the subnet"),
        (set_ipam(gateway="10.231.77.128"), "inside ip_range"),
        (set_ipam(gateway="10.231.77.200"), "inside ip_range"),
        (set_ipam(gateway="10.231.77.10"), "the proxy's address"),
        (set_ipam(gateway="10.231.77.0"), "network or the broadcast"),
        (set_ipam(gateway="10.231.77.255"), "network or the broadcast"),
        (set_ipam(gateway="10.231.77"), "not a valid IPv4"),
        (set_ipam(gateway="a.b.c.d"), "not a valid IPv4"),
        (set_ipam(gateway="10.231.77.1/24"), "not a valid IPv4"),
    ],
    ids=["missing", "empty", "other address", "other subnet", "measured value", "in the pool", "the proxy", "network address", "broadcast address", "short", "letters", "with a prefix"],
)
def test_a_gateway_that_breaks_a_rule_is_refused(change, expected):
    assert any(expected in line for line in broken(change)), broken(change)


def test_a_proxy_address_inside_the_pool_is_refused():
    def change(doc, text):
        doc["services"]["proxy"]["networks"]["stack"]["ipv4_address"] = "10.231.77.130"
        doc["services"]["app"]["environment"]["SOCBOARD_TRUSTED_PROXIES"] = "10.231.77.130"

    found = broken(change)
    assert any("proxy's address is inside ip_range" in line for line in found), found


def readme_gateway_problems(readme_text, gateway):
    paragraph = next((line for line in readme_text.splitlines() if "The forwarded address" in line), "")
    if f"`{gateway}`" in paragraph:
        return []
    return [
        f"terraform/README.md's 'The forwarded address' paragraph does not name {gateway}; "
        "change compose.yaml's gateway default and that paragraph together (#261)"
    ]


def test_the_terraform_readme_names_the_same_gateway_as_compose_yaml():
    assert readme_gateway_problems((ROOT / "terraform" / "README.md").read_text(encoding="utf-8"), default_gateway()) == []


@pytest.mark.parametrize(
    "text",
    [
        "",
        "- **The forwarded address (for D20):** the app sees the gateway.",
        "- **The forwarded address (for D20):** the app sees `10.231.77.128`.",
        "- **The forwarded address (for D20):** the app sees 10.231.77.1 with no backticks.",
        "an unrelated line naming `10.231.77.1`",
    ],
    ids=["empty", "no address", "the measured address", "no backticks", "another paragraph"],
)
def test_a_readme_that_does_not_name_the_gateway_in_its_forwarded_address_paragraph_is_refused(text):
    found = readme_gateway_problems(text, "10.231.77.1")
    assert found and "compose.yaml's gateway" in found[0]


# --- the project name, the network prefix and the trusted proxy as variables (#271) -----------------

SECOND = {"STACK_PROJECT": "socboard-prod", "STACK_NET": "10.231.78"}


@pytest.mark.parametrize(
    ("value", "env", "expected"),
    [
        ("literal", {}, "literal"),
        ("${A:-d}", {}, "d"),
        ("${A:-d}", {"A": "x"}, "x"),
        ("${A:-d}", {"A": ""}, "d"),
        ("${A-d}", {"A": ""}, ""),
        ("${A-d}", {}, "d"),
        ("${A:-${B:-z}}.10", {}, "z.10"),
        ("${A:-${B:-z}}.10", {"B": "y"}, "y.10"),
        ("${A:-${B:-z}}.10", {"A": "x", "B": "y"}, "x.10"),
        ("${A:-10.231.77}.0/24", {"A": "10.231.78"}, "10.231.78.0/24"),
        ("127.0.0.1:${P:-8000}:8000", {}, "127.0.0.1:8000:8000"),
        ("${IMG:-socboard-stack:local}", {}, "socboard-stack:local"),
        ("$$HOME ${A:-d}", {}, "$HOME d"),
        ("${R:?R is required}", {}, "${R:?R is required}"),
        ("${R:?R is required}", {"R": "v"}, "v"),
    ],
)
def test_the_expander_applies_composes_default_rules(value, env, expected):
    assert expand_text(value, env) == expected


@pytest.mark.parametrize("value", ["${A:-d", "${:-d}", "${A:+d}"])
def test_the_expander_refuses_what_it_does_not_model(value):
    with pytest.raises(ValueError):
        expand_text(value, {})


def test_the_expander_does_not_change_its_input():
    doc, _ = load()
    before = copy.deepcopy(doc)
    expand(doc, SECOND)
    assert doc == before


def test_the_real_file_passes_every_rule_with_no_variables_and_with_a_second_stack():
    doc, text = load()
    assert problems(doc, text) == []
    assert problems(doc, text, SECOND) == []
    assert problems(doc, text, {**SECOND, "STACK_TRUSTED_PROXIES": "10.231.78.1"}) == []


def test_the_second_stack_moves_every_address_and_shares_none_with_dev():
    doc, _ = load()
    dev, second = expand(doc, {}), expand(doc, SECOND)
    assert dev["name"] != second["name"] == "socboard-prod"
    dev_net = ipaddress.IPv4Network(dev["networks"]["stack"]["ipam"]["config"][0]["subnet"])
    second_net = ipaddress.IPv4Network(second["networks"]["stack"]["ipam"]["config"][0]["subnet"])
    assert not dev_net.overlaps(second_net)
    second_config = second["networks"]["stack"]["ipam"]["config"][0]
    assert second_config == {"subnet": "10.231.78.0/24", "ip_range": "10.231.78.128/25", "gateway": "10.231.78.1"}
    assert second["services"]["proxy"]["networks"]["stack"]["ipv4_address"] == "10.231.78.10"
    assert second["services"]["app"]["environment"]["SOCBOARD_TRUSTED_PROXIES"] == "10.231.78.10"
    assert "10.231.77" not in yaml.safe_dump(second)


def test_a_trusted_proxy_variable_reaches_the_app_and_nothing_else():
    doc, _ = load()
    server = expand(doc, {"STACK_TRUSTED_PROXIES": "10.231.77.1"})
    assert server["services"]["app"]["environment"]["SOCBOARD_TRUSTED_PROXIES"] == "10.231.77.1"
    assert server["services"]["proxy"]["networks"]["stack"]["ipv4_address"] == "10.231.77.10"


def test_a_second_stack_on_dev_s_prefix_is_refused_as_overlapping():
    doc, _ = load()
    dev = ipaddress.IPv4Network(expand(doc, {})["networks"]["stack"]["ipam"]["config"][0]["subnet"])
    clash = ipaddress.IPv4Network(expand(doc, {"STACK_NET": "10.231.77"})["networks"]["stack"]["ipam"]["config"][0]["subnet"])
    assert dev.overlaps(clash)  # the check the prod issues apply to the values they choose


@pytest.mark.parametrize("where", ["subnet", "ip_range", "gateway", "proxy", "name"])
def test_an_address_left_literal_does_not_follow_the_prefix_and_is_refused(where):
    def change(doc, text):
        config = doc["networks"]["stack"]["ipam"]["config"][0]
        if where == "proxy":
            doc["services"]["proxy"]["networks"]["stack"]["ipv4_address"] = "10.231.77.10"
        elif where == "name":
            doc["name"] = "socboard"
        else:
            config[where] = {"subnet": "10.231.77.0/24", "ip_range": "10.231.77.128/25", "gateway": "10.231.77.1"}[where]

    assert broken(change) == []  # a literal equal to the default is right for dev; the second stack shows it
    assert broken(change, SECOND), where


def test_a_trusted_address_that_ignores_the_variable_is_refused():
    def change(doc, text):
        doc["services"]["app"]["environment"]["SOCBOARD_TRUSTED_PROXIES"] = "${STACK_NET:-10.231.77}.10"

    assert any("STACK_TRUSTED_PROXIES" in line for line in broken(change, {"STACK_TRUSTED_PROXIES": "10.231.77.1"}))


def test_a_trusted_address_that_differs_from_the_proxy_under_the_defaults_is_refused():
    def change(doc, text):
        doc["services"]["app"]["environment"]["SOCBOARD_TRUSTED_PROXIES"] = "${STACK_TRUSTED_PROXIES:-10.231.77.1}"

    assert any("proxy's address" in line for line in broken(change))


def test_a_second_pool_entry_is_refused():
    def change(doc, text):
        doc["networks"]["stack"]["ipam"]["config"].append({"subnet": "10.231.99.0/24", "gateway": "10.231.99.1"})

    found = broken(change)
    assert any("2 pool entries, not exactly one" in line for line in found), found


def test_no_pool_entry_is_refused():
    assert any("0 pool entries" in line for line in broken(lambda doc, text: doc["networks"]["stack"]["ipam"].update(config=[])))
