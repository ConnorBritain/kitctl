"""Command-line entry point: argument parsing and output only."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from kitctl import __version__

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2


def _guard(report: dict[str, Any]) -> dict[str, Any]:
    """Final guard: reports carry paths and metadata only, but anything secret-shaped goes."""
    from kitctl.secretscan import redact_obj

    cleaned, kinds = redact_obj(report)
    if kinds:
        cleaned["findings"].append(
            {
                "severity": "warning",
                "code": "secret-redacted",
                "path": "",
                "message": f"Redacted likely secrets from output ({', '.join(kinds)})",
            }
        )
    result: dict[str, Any] = cleaned
    return result


def _exit_for(report: dict[str, Any]) -> int:
    errors = any(finding["severity"] == "error" for finding in report["findings"])
    return EXIT_FINDINGS if errors else EXIT_OK


def _print_inventory(report: dict[str, Any]) -> None:
    entries = report["entries"]
    order = {"managed": 0, "user": 1, "plugin": 2, "project": 3, "local": 4}
    entries = sorted(
        entries,
        key=lambda e: (e["harness"], order[e["scope"]], e["kind"], e["repo"] or "", e["name"]),
    )
    current = None
    for entry in entries:
        group = (entry["harness"], entry["scope"])
        if group != current:
            current = group
            print(f"\n[{entry['harness']} / {entry['scope']}]")
        where = f"  ({entry['provided_by']})" if entry["provided_by"] else ""
        target = f" -> {entry['link_target']}" if entry["link_target"] else ""
        print(f"  {entry['kind']:12} {entry['name']}{where}  {entry['path']}{target}")
    counts = Counter(entry["kind"] for entry in report["entries"])
    summary = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
    repos = len(report["roots"]["repos"])
    print(f"\n{len(report['entries'])} items across {repos} repos ({summary or 'none'}).")
    print("No agent configuration was changed.")
    for finding in report["findings"]:
        print(f"{finding['severity']}: {finding['message']} [{finding['path']}]", file=sys.stderr)


def _inventory(args: argparse.Namespace) -> int:
    from kitctl.config import ConfigError
    from kitctl.inventory import inventory

    try:
        report = _guard(inventory(args.roots, use_cache=not args.no_cache))
    except (OSError, ValueError, ConfigError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_inventory(report)
    return _exit_for(report)


def _init(args: argparse.Namespace) -> int:
    from kitctl.init import run_init

    return run_init(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kitctl", description="kitctl — inventory and manage agent tooling."
    )
    parser.add_argument("--version", action="version", version=f"kitctl {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="First-run setup; writes only kitctl's own config.")
    init.add_argument("--non-interactive", action="store_true", help="Use flags and defaults.")
    init.add_argument(
        "--repo-parent",
        action="append",
        type=Path,
        dest="repo_parents",
        metavar="PATH",
        help="Directory containing repositories; repeatable.",
    )
    init.add_argument("--warehouse", type=Path, help="Warehouse checkout (default ~/kits).")
    init.add_argument("--depth", type=int, default=None, help="Repo discovery depth (default 3).")
    init.add_argument("--force", action="store_true", help="Overwrite an existing config.")
    init.set_defaults(handler=_init)

    scan = commands.add_parser("inventory", help="Read-only discovery of agent tooling.")
    scan.add_argument(
        "--root",
        action="append",
        type=Path,
        dest="roots",
        metavar="PATH",
        help="Repository to inspect; repeatable. Defaults to repos found from `kitctl init` "
        "config, else the current directory. User-level config is always included.",
    )
    scan.add_argument("--json", action="store_true", help="Print the report as JSON.")
    scan.add_argument("--no-cache", action="store_true", help="Don't use the hash cache.")
    scan.set_defaults(handler=_inventory)

    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result
