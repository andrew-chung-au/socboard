"""The manifest of the observability evidence (#293), and the check that it is whole and holds no canary.

    python observability/screenshots/manifest.py build <dir> --run-id <id> --run-url <url> --commit <sha>
    python observability/screenshots/manifest.py check <dir>

CI never commits. A person downloads the `socboard observability` run's artifact, puts its files in
`_docs/evidence/observability/<date>/`, and runs `build` there: it writes `manifest.json` (the run id, the
run URL, the commit, and for each file what it shows). `check` exits 1, naming each problem, unless the
manifest has those fields, lists exactly the files that are in the folder besides itself, each exists, each
screenshot is a PNG, each JSON file parses, and no file holds a canary string of observability/CONTRACT.md
section 8 or a word that looks like a password line. tests/test_observability_evidence.py runs `check` over
every evidence folder in the repository. Standard library only; it prints no file content.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CONTRACT = ROOT / "observability" / "CONTRACT.md"
PNG = b"\x89PNG\r\n\x1a\n"
SHOWS = {
    "dashboard.png": "Grafana's dashboard 'SOCBoard: alert to queue' over the run's probe window",
    "trace.png": "One probe trace in Tempo, opened from its trace id",
    "logs.png": "The log lines of that trace in Loki, found by its trace id",
    "trace.json": "Tempo's answer for that trace id, with canary strings and the Grafana password replaced",
    "logs.json": "Loki's answer for that trace id (the trace id is structured metadata), redacted the same way",
    "exemplars.json": "Prometheus's exemplars for the probe's histogram, redacted the same way",
    "window.json": "The trace id and the absolute from and to (milliseconds since the epoch) the screenshots used",
}
SECRET_LINE = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY)\s*[=:]\s*\S{8,}", re.IGNORECASE)
RUN_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+")
COMMIT = re.compile(r"[0-9a-f]{40}")


def canaries() -> list[str]:
    """The canary values of CONTRACT section 8, read from the contract."""
    return re.findall(r"^[a-z-]+=(CANARY-[A-Za-z0-9-]+)$", CONTRACT.read_text(encoding="utf-8"), re.MULTILINE)


def build(folder: Path, run_id: str, run_url: str, commit: str) -> dict:
    names = sorted(path.name for path in folder.iterdir() if path.is_file() and path.name != "manifest.json")
    unknown = [name for name in names if name not in SHOWS]
    if unknown:
        raise ValueError(f"files the manifest has no description for: {unknown}")
    manifest = {
        "run_id": run_id,
        "run_url": run_url,
        "commit": commit,
        "files": [{"name": name, "shows": SHOWS[name]} for name in names],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def check(folder: Path) -> list[str]:
    found: list[str] = []
    path = folder / "manifest.json"
    if not path.is_file():
        return ["manifest.json is missing"]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return ["manifest.json is not JSON"]
    if not isinstance(manifest, dict):
        return ["manifest.json is not an object"]
    if not re.fullmatch(r"\d+", str(manifest.get("run_id", ""))):
        found.append("run_id is not a run number")
    if not RUN_URL.fullmatch(str(manifest.get("run_url", ""))):
        found.append("run_url is not a GitHub Actions run URL")
    elif not str(manifest["run_url"]).endswith("/" + str(manifest.get("run_id"))):
        found.append("run_url does not end in the run_id")
    if not COMMIT.fullmatch(str(manifest.get("commit", ""))):
        found.append("commit is not a full commit hash")
    listed = manifest.get("files")
    if not isinstance(listed, list) or not listed:
        return [*found, "files is not a list of files"]
    names = []
    for entry in listed:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not str(entry.get("shows", "")).strip():
            found.append(f"a file entry has no name or no description: {entry}")
            continue
        names.append(entry["name"])
    present = sorted(p.name for p in folder.iterdir() if p.is_file() and p.name != "manifest.json")
    for name in sorted(set(names) - set(present)):
        found.append(f"{name} is in the manifest but not in the folder")
    for name in sorted(set(present) - set(names)):
        found.append(f"{name} is in the folder but not in the manifest")
    if len(names) != len(set(names)):
        found.append("a file is listed twice")
    values = canaries()
    if len(values) < 7:
        found.append("the contract's canary list could not be read")
    for name in sorted(set(names) & set(present)):
        data = (folder / name).read_bytes()
        if name.endswith(".png") and not data.startswith(PNG):
            found.append(f"{name} is not a PNG")
        if name.endswith(".json"):
            try:
                json.loads(data)
            except ValueError:
                found.append(f"{name} is not JSON")
        text = data.decode("utf-8", errors="ignore")
        for value in values:
            if value in text:
                found.append(f"{name} holds a canary string of the contract")
        if name.endswith(".json") and SECRET_LINE.search(text):
            found.append(f"{name} holds something that looks like a secret")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("folder", type=Path)
    make.add_argument("--run-id", required=True)
    make.add_argument("--run-url", required=True)
    make.add_argument("--commit", required=True)
    look = commands.add_parser("check")
    look.add_argument("folder", type=Path)
    args = parser.parse_args(argv)
    if args.command == "build":
        try:
            build(args.folder, args.run_id, args.run_url, args.commit)
        except (ValueError, OSError) as error:
            print(f"observability manifest: {error}", file=sys.stderr)
            return 2
        problems = check(args.folder)
    else:
        problems = check(args.folder)
    for line in problems:
        print(f"observability manifest: {line}")
    if not problems:
        print("observability manifest: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
