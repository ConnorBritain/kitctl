"""Kit command line. Every command here reads only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from kitctl import __version__
from kitctl.inventory import inventory
from kitctl.model import ModelError, warehouse_root
from kitctl.resolve import resolve
from kitctl.validate import validate


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
    check = commands.add_parser("validate", help="Check warehouse documents and loadouts.")
    check.add_argument(
        "path", nargs="?", type=Path, help="Warehouse root (default: KIT_WAREHOUSE or ~/kits)."
    )
    check.add_argument("--json", action="store_true", help="Print findings as JSON.")
    show = commands.add_parser(
        "resolve", help="Print a loadout's effective config and capability gaps; reads only."
    )
    show.add_argument("target", metavar="LOADOUT[:PROFILE]")
    show.add_argument("--harness", choices=("claude", "codex"), required=True)
    show.add_argument("--warehouse", type=Path, help="Warehouse root (default: ~/kits).")
    show.add_argument(
        "--observed",
        type=Path,
        metavar="FILE",
        help="JSON list of identities enabled outside Kit; enables stale-disable checks.",
    )
    show.add_argument("--json", action="store_true", help="Print the full result as JSON.")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args)
    if args.command == "resolve":
        return _resolve(args)
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


def _print_problems(problems: list[dict[str, str]]) -> None:
    for problem in problems:
        print(f"{problem['severity']}: {problem['path']}: {problem['message']}", file=sys.stderr)


def _validate(args: argparse.Namespace) -> int:
    root = warehouse_root(args.path)
    if not root.is_dir():
        print(f"kitctl: warehouse is not a directory: {root}", file=sys.stderr)
        return 2
    report = validate(root)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"{report['primitives']} primitives, {report['loadouts']} loadouts, "
            f"{report['resolutions']} resolutions checked."
        )
        _print_problems(report["problems"])
    return 1 if any(p["severity"] == "error" for p in report["problems"]) else 0


def _resolve(args: argparse.Namespace) -> int:
    root = warehouse_root(args.warehouse)
    if not root.is_dir():
        print(f"kitctl: warehouse is not a directory: {root}", file=sys.stderr)
        return 2
    observed = None
    if args.observed is not None:
        try:
            data = json.loads(args.observed.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"kitctl: cannot read {args.observed}: {error}", file=sys.stderr)
            return 2
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            print("kitctl: --observed must be a JSON list of identities", file=sys.stderr)
            return 2
        observed = set(data)
    try:
        result = resolve(root, args.target, args.harness, observed)
    except ModelError as error:
        if args.json:
            print(json.dumps({"errors": [p.as_dict() for p in error.problems]}, indent=2))
        _print_problems([p.as_dict() for p in error.problems])
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    _print_resolution(result)
    return 0


def _print_resolution(result: dict[str, Any]) -> None:
    profile = f":{result['profile']}" if result["profile"] else ""
    harness = result["harness"]
    print(f"{result['loadout']}{profile} for {harness['name']} ({harness['version']})")
    print(f"layers: {' -> '.join(result['layers'])}")
    for slot, info in result["slots"].items():
        print(f"slot {slot}: {info['provider'] or 'unfilled'}")
    for effect in result["effects"]:
        print(f"  {effect['target']}  {effect['key']}  [{effect['evidence']}]")
    block = result["instructions"]
    if block:
        print(f"  {block['target']}  managed block {block['marker']}")
    for step in result["setup"]:
        print(f"  setup: {' '.join(step['run'])}  (outputs not Kit-owned)")
    for kind in ("requires", "optional"):
        for tool in result["tools"][kind]:
            print(f"  {kind}: {tool['identity']}")
    for gap in result["gaps"]:
        print(f"gap [{gap['severity']}] {gap['subject']}: {gap['reason']}")
        if gap["remedy"]:
            print(f"    remedy: {gap['remedy']}")
    _print_problems(result["warnings"])
    print("Read only; no files changed.")
