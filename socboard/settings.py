"""Every runtime setting in one list (#141), and the start-up checks of deployed mode.

`SETTINGS` names each variable the backend, `make seed` or `make run` reads. The README's variable
table and the example env file copy their names from it, and `tests/test_settings.py` fails when
they disagree. `scripts/run_preflight.py` takes its `REQUIRED` names from the entries marked
`make_run`. The names themselves stay in the modules that read them, so none is written twice.

Deployed mode is on only when `SOCBOARD_MODE` is exactly `deployed`. Then `check_deployed` refuses
an ingest key or database URL that is missing or unusable, and a token secret of one repeated
character, by raising `ConfigError`; `python -m socboard.server` exits 2 on it. Every message
names the variable, never its value.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from socboard import database, ingest, spa
from socboard.auth import SECRET_ENV_NAME, ConfigError
from socboard.seed import USERS

MODE_VAR = "SOCBOARD_MODE"
DEPLOYED_VALUE = "deployed"


@dataclass(frozen=True)
class Setting:
    name: str
    secret: bool
    deployed: bool  # deployed mode refuses to start without a usable value
    make_run: bool  # `make run` refuses to start without it (scripts/run_preflight.py)
    rule: str


# Order matters: the make_run entries, in this order, are the preflight's REQUIRED tuple.
SETTINGS = (
    Setting(
        SECRET_ENV_NAME,
        secret=True,
        deployed=True,
        make_run=True,
        rule="at least 32 characters, not counting leading and trailing spaces; "
        "in deployed mode also not one repeated character, whitespace ignored",
    ),
    *(
        Setting(variable, secret=True, deployed=False, make_run=True, rule="at least 12 characters; read by make seed only")
        for _, _, _, variable in USERS
    ),
    Setting(
        ingest.KEY_ENV_NAME,
        secret=True,
        deployed=True,
        make_run=True,
        rule="at least 16 characters (without it ingest is closed); in deployed mode required, "
        "at least 16 characters, no leading or trailing whitespace, and not one repeated character "
        "(whitespace ignored)",
    ),
    Setting(
        database.ENV_NAME,
        secret=True,
        deployed=True,
        make_run=False,
        rule="a valid database URL (default: socboard.db in the repository root); required in deployed mode",
    ),
    Setting("SOCBOARD_HOST", secret=False, deployed=False, make_run=False, rule="127.0.0.1, localhost or ::1 (default 127.0.0.1)"),
    Setting("SOCBOARD_PORT", secret=False, deployed=False, make_run=False, rule="a whole number from 1 to 65535 (default 8000)"),
    Setting(spa.STATIC_DIR_VAR, secret=False, deployed=False, make_run=False, rule="the built frontend folder (default frontend/dist)"),
    Setting(
        MODE_VAR,
        secret=False,
        deployed=False,
        make_run=False,
        rule=f"exactly {DEPLOYED_VALUE} turns deployed mode on; any other value, or none, leaves it off",
    ),
)

SECRET_REPEATED_MESSAGE = (
    f"{SECRET_ENV_NAME} must not be one repeated character, whitespace ignored, in deployed mode."
)
KEY_MESSAGE = (
    f"{ingest.KEY_ENV_NAME} must be set to at least {ingest.MIN_KEY_LENGTH} characters, with no leading "
    "or trailing whitespace and not one repeated character, in deployed mode."
)
DATABASE_MESSAGE = f"{database.ENV_NAME} must be set to a database URL in deployed mode."


def is_deployed(environ: Mapping[str, str]) -> bool:
    return environ.get(MODE_VAR) == DEPLOYED_VALUE


def _one_repeated_character(value: str) -> bool:
    return len(set("".join(value.split()))) <= 1


def _key_unusable(key: str) -> bool:
    # HTTP strips header whitespace, so a key with edge whitespace could never match.
    return key != key.strip() or len(key) < ingest.MIN_KEY_LENGTH or _one_repeated_character(key)


# (refuses the value, message) for each setting marked `deployed`; tests/test_settings.py checks
# the keys match the list. The secret's length was already checked by `resolve_token_secret`.
DEPLOYED_CHECKS = {
    SECRET_ENV_NAME: (_one_repeated_character, SECRET_REPEATED_MESSAGE),
    ingest.KEY_ENV_NAME: (_key_unusable, KEY_MESSAGE),
    database.ENV_NAME: (lambda url: not url.strip(), DATABASE_MESSAGE),
}


def check_deployed(environ: Mapping[str, str]) -> None:
    """Raise `ConfigError` for the first unusable setting. Call after `resolve_token_secret`,
    which already refuses a missing, short or blank secret in every mode."""
    for setting in SETTINGS:
        if setting.deployed:
            unusable, message = DEPLOYED_CHECKS[setting.name]
            if unusable(environ.get(setting.name, "")):
                raise ConfigError(message)
