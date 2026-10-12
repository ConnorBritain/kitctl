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


def _descriptions(report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Drop free-text descriptions when asked (--no-descriptions or config)."""
    from kitctl import config as kit_config
    from kitctl import paths as kit_paths

    keep = not getattr(args, "no_descriptions", False)
    if keep:
        try:
            settings = kit_config.load(kit_paths.resolve().kit_config_file)
        except kit_config.ConfigError:
            settings = None
        keep = settings.export_descriptions if settings else True
    if not keep:
        for entry in report.get("entries", []):
            entry["metadata"].pop("description", None)
    return report


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
        timings: dict[str, float] | None = {} if args.timings else None
        report = _guard(
            inventory(
                args.roots,
                use_cache=not args.no_cache,
                include_worktrees=args.include_worktrees,
                timings=timings,
            )
        )
        if timings is not None:
            for name, value in timings.items():
                shown = f"{value * 1000:8.1f} ms" if isinstance(value, float) else f"{value:8d}"
                print(f"timing {name:16} {shown}", file=sys.stderr)
        report = _descriptions(report, args)
    except (OSError, ValueError, ConfigError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_inventory(report)
    return _exit_for(report)


def _usage(args: argparse.Namespace) -> int:
    from kitctl import config as kit_config
    from kitctl import paths as kit_paths
    from kitctl.inventory import resolve_repos
    from kitctl.usage.run import mine

    try:
        paths = kit_paths.resolve()
        repos, _, _ = resolve_repos(
            args.roots, kit_config.load(paths.kit_config_file), args.include_worktrees
        )
        result = _guard_any(mine(paths, repos, since=args.since, use_cache=not args.no_cache))
    except (OSError, ValueError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(result, indent=2))
        return EXIT_OK
    print(f"{'harness':8} {'kind':10} {'calls':>6} {'sessions':>8}  {'last used':10}  name  [repo]")
    for record in sorted(result["records"], key=lambda r: (-r["calls"], r["name"])):
        repo = f"  [{Path(record['repo']).name}]" if record["repo"] else ""
        print(
            f"{record['harness']:8} {record['kind']:10} {record['calls']:>6} "
            f"{record['sessions']:>8}  {record['last_used'][:10]:10}  {record['name']}{repo}"
        )
    for adapter, stats in result["adapters"].items():
        print(
            f"{adapter}: {stats['files']} logs ({stats['files_read']} read, "
            f"{stats['bytes_read']} bytes); {stats['malformed_total']} malformed, "
            f"{stats['unknown_shape_total']} unrecognized lines skipped",
            file=sys.stderr,
        )
    return EXIT_OK


def _guard_any(value: dict[str, Any]) -> dict[str, Any]:
    from kitctl.secretscan import redact_obj

    cleaned, kinds = redact_obj(value)
    if kinds:
        print(f"warning: redacted likely secrets from output ({', '.join(kinds)})", file=sys.stderr)
    result: dict[str, Any] = cleaned
    return result


def _report(args: argparse.Namespace) -> int:
    from kitctl import report as kit_report
    from kitctl.config import ConfigError
    from kitctl.secretscan import redact

    try:
        built = _guard(
            kit_report.build(
                args.roots,
                since=args.since,
                use_cache=not args.no_cache,
                include_worktrees=args.include_worktrees,
            )
        )
        built = _descriptions(built, args)
    except (OSError, ValueError, ConfigError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    target = kit_report.default_path(args.format)
    if args.out:
        out = args.out.expanduser().resolve()
        inside = [repo for repo in built["roots"]["repos"] if out.is_relative_to(Path(repo))]
        if inside and not args.force:
            print(
                f"kitctl: refusing to write a report inside repository {inside[0]} "
                "(reports reveal private paths); pass --force to override.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        target = out / target.name
    text, kinds = redact(kit_report.render(built, args.format))
    if kinds:
        print(f"warning: redacted likely secrets ({', '.join(kinds)})", file=sys.stderr)
    from kitctl import fs

    if args.out:
        fs.allow(target.parent)
    fs.write_atomic(target, text)
    print(target)
    errors = sum(f["severity"] == "error" for f in built["findings"])
    warnings = sum(f["severity"] == "warning" for f in built["findings"])
    print(
        f"{len(built['matrix'])} items, {errors} errors, {warnings} warnings. "
        "No agent configuration was changed.",
        file=sys.stderr,
    )
    return _exit_for(built)


def _doctor(args: argparse.Namespace) -> int:
    from kitctl.doctor import run

    result = _guard_any(run(probe=args.probe))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        marks = {"ok": "ok  ", "info": "info", "warn": "warn", "error": "FAIL"}
        for check in result["checks"]:
            print(f"[{marks[check['status']]}] {check['check']}: {check['detail']}")
    failed = any(c["status"] == "error" for c in result["checks"])
    return EXIT_FINDINGS if failed else EXIT_OK


def _validate(args: argparse.Namespace) -> int:
    from kitctl import config as kit_config
    from kitctl import paths as kit_paths
    from kitctl.validate import validate

    paths = kit_paths.resolve()
    warehouse = args.warehouse
    if warehouse is None:
        try:
            settings = kit_config.load(paths.kit_config_file)
        except kit_config.ConfigError as error:
            print(f"kitctl: {error}", file=sys.stderr)
            return EXIT_USAGE
        warehouse = (settings.warehouse_path() if settings else None) or paths.default_warehouse
    try:
        result = validate(warehouse.expanduser())
    except FileNotFoundError as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for finding in result["findings"]:
            print(f"{finding['severity']}: {finding['message']} [{finding['path']}]")
        if not result["primitives"] and not result["loadouts"]:
            print(f"Nothing to validate in {result['warehouse']} (0 primitives, 0 loadouts).")
        else:
            print(
                f"{result['primitives']} primitives, {result['loadouts']} loadouts, "
                f"{len(result['findings'])} problems in {result['warehouse']}"
            )
    return _exit_for(result)


def _init(args: argparse.Namespace) -> int:
    from kitctl.init import run_init

    return run_init(args)


def _common(parser: argparse.ArgumentParser, descriptions: bool) -> None:
    parser.add_argument(
        "--include-worktrees",
        action="store_const",
        const=True,
        default=None,
        help="Scan git worktrees too (skipped by default; config include_worktrees).",
    )
    if descriptions:
        parser.add_argument(
            "--no-descriptions",
            action="store_true",
            help="Omit skill/agent/command/plugin descriptions (names-only export).",
        )


def _date(value: str) -> str:
    from datetime import date

    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from error


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
    scan.add_argument("--timings", action="store_true", help="Print per-phase timings to stderr.")
    _common(scan, descriptions=True)
    scan.set_defaults(handler=_inventory)

    usage = commands.add_parser("usage", help="Mine session logs for skill/agent/MCP usage.")
    usage.add_argument(
        "--root",
        action="append",
        type=Path,
        dest="roots",
        metavar="PATH",
        help="Repositories for attribution (default: from config).",
    )
    usage.add_argument("--since", type=_date, help="Only count use on or after YYYY-MM-DD.")
    usage.add_argument("--json", action="store_true")
    usage.add_argument("--no-cache", action="store_true", help="Re-read every log in full.")
    _common(usage, descriptions=False)
    usage.set_defaults(handler=_usage)

    report = commands.add_parser("report", help="Write an inventory, usage, and findings report.")
    report.add_argument("--format", choices=("md", "json", "html"), default="md")
    report.add_argument("--out", type=Path, help="Directory (default: kitctl's report dir).")
    report.add_argument("--force", action="store_true", help="Allow --out inside a repository.")
    report.add_argument("--root", action="append", type=Path, dest="roots", metavar="PATH")
    report.add_argument("--since", type=_date, help="Only count use on or after YYYY-MM-DD.")
    report.add_argument("--no-cache", action="store_true")
    _common(report, descriptions=True)
    report.set_defaults(handler=_report)

    doctor = commands.add_parser("doctor", help="Check the environment and harness setup.")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument(
        "--probe",
        action="store_true",
        help="Run `claude/codex --version` (may update the harness's own caches or logs).",
    )
    doctor.set_defaults(handler=_doctor)

    validate = commands.add_parser("validate", help="Validate warehouse loadouts and primitives.")
    validate.add_argument("--warehouse", type=Path, help="Warehouse path (default: config).")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=_validate)

    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result
