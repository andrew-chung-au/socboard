"""The drill's final log scan (#299): no model key prefix and no canary string in any job's log.

    python observability/drill/logscan.py LOG [LOG ...] [--prefix TEXT] [--contract observability/CONTRACT.md]

Looks in each log file for the prefix of a model key (default `sk-ant-`) and for each canary value of
CONTRACT.md section 8. It prints the file, the line number and the kind (`key prefix` or `canary <kind>`),
never the text it matched. Exit codes: 0 nothing found; 1 something found; 2 a log or the contract
cannot be read, or the contract holds no canary (a scan with nothing to look for must not pass).
Standard library only.
"""

import argparse
import re
import sys
from collections.abc import Mapping
from pathlib import Path

KEY_PREFIX = "sk-ant-"
CONTRACT = Path(__file__).resolve().parent.parent / "CONTRACT.md"
CANARY_LINE = re.compile(r"^([a-z-]+)=(CANARY-\S+)$", re.MULTILINE)


def read_canaries(contract_text: str) -> dict[str, str]:
    """kind -> value, from the `kind=CANARY-...` lines of the contract."""
    return {kind: value for kind, value in CANARY_LINE.findall(contract_text)}


def scan(logs: Mapping[str, str], prefix: str, canaries: Mapping[str, str]) -> list[str]:
    """One line per hit: `<file>:<line>: <kind>`. The matched text is never returned."""
    needles = [("key prefix", prefix)] + [(f"canary {kind}", value) for kind, value in canaries.items()]
    hits = []
    for name, text in logs.items():
        for number, line in enumerate(text.split("\n"), 1):
            hits += [f"{name}:{number}: {kind}" for kind, needle in needles if needle in line]
    return hits


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("logs", nargs="+", help="log files to scan")
    parser.add_argument("--prefix", default=KEY_PREFIX, help=f"the model key's prefix (default {KEY_PREFIX})")
    parser.add_argument("--contract", default=str(CONTRACT), help="the contract holding the canary list")
    args = parser.parse_args(argv)
    if not args.prefix:
        print("refused: the prefix is empty", file=sys.stderr)
        return 2
    try:
        canaries = read_canaries(Path(args.contract).read_text(encoding="utf-8"))
        logs = {name: Path(name).read_text(encoding="utf-8", errors="replace") for name in args.logs}
    except OSError:
        print("cannot read a log or the contract", file=sys.stderr)
        return 2
    if not canaries:
        print("refused: the contract lists no canary", file=sys.stderr)
        return 2
    hits = scan(logs, args.prefix, canaries)
    for hit in hits:
        print(hit)
    print(f"scanned {len(logs)} log files for the key prefix and {len(canaries)} canaries: " + (f"{len(hits)} hits" if hits else "none found"))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
