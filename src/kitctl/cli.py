"""Command-line entry point: argument parsing and output only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kitctl import __version__

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2


def _inventory(args: argparse.Namespace) -> int:
    from kitctl.inventory import inventory
    from kitctl.secretscan import redact_obj

    try:
        report = inventory(args.roots or [Path.cwd()])
    except (OSError, ValueError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    # Final guard: reports carry paths and metadata only, but a path can still look secret.
    report, kinds = redact_obj(report)
    if kinds:
        report["findings"].append(
            {
                "severity": "warning",
                "code": "secret-redacted",
                "path": "",
                "message": f"Redacted likely secrets from output ({', '.join(kinds)})",
            }
        )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for entry in report["entries"]:
            suffix = f" -> {entry['link_target']}" if entry["link_target"] else ""
            print(f"{entry['kind']:6}  {entry['name']}  {entry['path']}{suffix}")
        print(f"{len(report['entries'])} items; no files changed.")
        for finding in report["findings"]:
            print(
                f"{finding['severity']}: {finding['message']} [{finding['path']}]", file=sys.stderr
            )
    errors = any(finding["severity"] == "error" for finding in report["findings"])
    return EXIT_FINDINGS if errors else EXIT_OK


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
    scan.set_defaults(handler=_inventory)
    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result
