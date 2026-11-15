"""Remove secrets from the browser test's failure artifacts before CI uploads them (#234).

    python3 e2e/redact_traces.py e2e/test-results compose.secrets

A Playwright trace records what the test typed (the sign-in password), the headers the page sent (the
bearer token) and the answers it got (the token in the login response), and the repository is public,
so the artifact must hold none of them. This replaces, in every file under the results folder and in
every member of every `.zip` in it (except images and video): each value in the secrets file that is
at least 8 characters long, any `Bearer <token>`, and any `"token": "<value>"`. It then reads
everything again and fails if a secret value is still there.

Exit 0 when done (or when there is no results folder: a passing run leaves none). Exit 1 when a zip cannot
be read or a value is still found afterwards. Exit 2 when the secrets file cannot be read: the caller
must not upload anything then. It prints counts and file paths, never a value.
"""

import re
import sys
import zipfile
from pathlib import Path

PLACEHOLDER = b"[redacted]"
MIN_LENGTH = 8
SKIPPED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webm", ".gif"}
BEARER = re.compile(rb"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
TOKEN_FIELD = re.compile(rb'("token"\s*:\s*")[^"]*(")')


def secret_values(secrets_file: Path) -> list[bytes]:
    """The values in `NAME=value` lines that are long enough to be worth hiding, longest first."""
    values = set()
    for line in secrets_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and len(value.strip()) >= MIN_LENGTH:
            values.add(value.strip().encode())
    return sorted(values, key=len, reverse=True)


def scrub(data: bytes, values: list[bytes]) -> bytes:
    for value in values:
        data = data.replace(value, PLACEHOLDER)
    data = BEARER.sub(rb"\1" + PLACEHOLDER, data)
    return TOKEN_FIELD.sub(rb"\1" + PLACEHOLDER + rb"\2", data)


def remaining(data: bytes, values: list[bytes]) -> bool:
    return any(value in data for value in values)


def scrub_zip(path: Path, values: list[bytes]) -> int:
    """Rewrite one zip with every member scrubbed; the number of members changed."""
    changed = 0
    with zipfile.ZipFile(path) as source:
        members = [(info, source.read(info)) for info in source.infolist()]
    rewritten = path.with_name(path.name + ".redacted")
    with zipfile.ZipFile(rewritten, "w", zipfile.ZIP_DEFLATED) as target:
        for info, data in members:
            if Path(info.filename).suffix.lower() not in SKIPPED_SUFFIXES:
                clean = scrub(data, values)
                changed += clean != data
                data = clean
            target.writestr(info.filename, data)
    rewritten.replace(path)
    return changed


def zip_leaks(path: Path, values: list[bytes]) -> bool:
    with zipfile.ZipFile(path) as archive:
        return any(
            remaining(archive.read(info), values)
            for info in archive.infolist()
            if Path(info.filename).suffix.lower() not in SKIPPED_SUFFIXES
        )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: redact_traces.py RESULTS_DIR SECRETS_FILE", file=sys.stderr)
        return 2
    results, secrets_file = Path(argv[0]), Path(argv[1])
    try:
        values = secret_values(secrets_file)
    except (OSError, ValueError):
        print("redact: the secrets file could not be read; nothing was changed and nothing may be uploaded", file=sys.stderr)
        return 2
    if not values:
        print("redact: the secrets file holds no value to hide; nothing may be uploaded", file=sys.stderr)
        return 2
    if not results.is_dir():
        print("redact: no results folder; nothing to redact")
        return 0
    files = [path for path in sorted(results.rglob("*")) if path.is_file() and path.suffix.lower() not in SKIPPED_SUFFIXES]
    changed = 0
    failed = []
    for path in files:
        try:
            if path.suffix.lower() == ".zip":
                changed += scrub_zip(path, values)
                if zip_leaks(path, values):
                    failed.append(path)
            else:
                data = path.read_bytes()
                clean = scrub(data, values)
                if clean != data:
                    path.write_bytes(clean)
                    changed += 1
                if remaining(path.read_bytes(), values):
                    failed.append(path)
        except (OSError, zipfile.BadZipFile):
            failed.append(path)
    for path in failed:
        print(f"redact: could not make {path} safe", file=sys.stderr)
    print(f"redact: {len(files)} files checked, {changed} changed, {len(failed)} not safe")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
