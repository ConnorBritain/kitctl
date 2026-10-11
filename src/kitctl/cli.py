"""CLI for Kit's initial read-only release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kitctl import __version__
from kitctl.inventory import inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kitctl", description="Kit — inventory and manage agent tooling."
    )
    parser.add_argument("--version", action="version", version=f"kitctl {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("inventory", help="Read-only discovery in explicit roots.")
    scan.add_argument(
        "--root",
        action="append",
        type=Path,
        dest="roots",
        metavar="PATH",
        help="Repository or home directory to inspect; repeatable. Defaults to current directory.",
    )
    scan.add_argument("--json", action="store_true", help="Print metadata and hashes as JSON.")
    args = parser.parse_args(argv)
    try:
        report = inventory(args.roots or [Path.cwd()])
    except (OSError, ValueError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for entry in report["entries"]:
            print(f"{entry['kind']:6}  {entry['name']}  {entry['path']}")
        print(f"{len(report['entries'])} items; no files changed.")
        for warning in report["warnings"]:
            print(f"warning: {warning}", file=sys.stderr)
    return 1 if report["warnings"] else 0
