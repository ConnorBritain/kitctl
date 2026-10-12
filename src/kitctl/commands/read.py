"""Read-only commands: inventory, usage, report, doctor, validate, conformance."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from kitctl.commands.common import (
    EXIT_FINDINGS,
    EXIT_OK,
    EXIT_USAGE,
    descriptions,
    exit_for,
    guard,
    guard_any,
)


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


def inventory(args: argparse.Namespace) -> int:
    from kitctl.config import ConfigError
    from kitctl.inventory import inventory

    try:
        timings: dict[str, float] | None = {} if args.timings else None
        report = guard(
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
        report = descriptions(report, args)
    except (OSError, ValueError, ConfigError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_inventory(report)
    return exit_for(report)


def usage(args: argparse.Namespace) -> int:
    from kitctl import config as kit_config
    from kitctl import paths as kit_paths
    from kitctl.inventory import resolve_repos
    from kitctl.usage.run import mine

    try:
        paths = kit_paths.resolve()
        repos, _, _ = resolve_repos(
            args.roots, kit_config.load(paths.kit_config_file), args.include_worktrees
        )
        result = guard_any(mine(paths, repos, since=args.since, use_cache=not args.no_cache))
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


def report(args: argparse.Namespace) -> int:
    from kitctl import report as kit_report
    from kitctl.config import ConfigError
    from kitctl.secretscan import redact

    try:
        built = guard(
            kit_report.build(
                args.roots,
                since=args.since,
                use_cache=not args.no_cache,
                include_worktrees=args.include_worktrees,
            )
        )
        built = descriptions(built, args)
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
    return exit_for(built)


def doctor(args: argparse.Namespace) -> int:
    from kitctl.doctor import run

    result = guard_any(run(probe=args.probe))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        marks = {"ok": "ok  ", "info": "info", "warn": "warn", "error": "FAIL"}
        for check in result["checks"]:
            print(f"[{marks[check['status']]}] {check['check']}: {check['detail']}")
    failed = any(c["status"] == "error" for c in result["checks"])
    return EXIT_FINDINGS if failed else EXIT_OK


def validate(args: argparse.Namespace) -> int:
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
        if not any(result[key] for key in ("primitives", "loadouts", "staged", "decisions")):
            print(f"Nothing to validate in {result['warehouse']} (0 primitives, 0 loadouts).")
        else:
            print(
                f"{result['primitives']} primitives, {result['staged']} in inbox/attic, "
                f"{result['loadouts']} loadouts, {result['decisions']} triage decisions; "
                f"{len(result['findings'])} problems in {result['warehouse']}"
            )
    return exit_for(result)


def conformance(args: argparse.Namespace) -> int:
    from kitctl import conformance
    from kitctl.inventory import inventory

    print(
        "conformance runs harness CLIs (claude/codex list commands); they may update their "
        "own caches or logs. kitctl changes no agent configuration.",
        file=sys.stderr,
    )
    cwd = (args.repo or Path.cwd()).expanduser()
    report = inventory([cwd] if args.repo else None)
    result = guard_any(conformance.run(report, cwd, health_checks=args.health_checks))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for check in result["checks"]:
            count = "" if check["native_count"] is None else f" ({check['native_count']} listed)"
            print(f"{check['check']:28} {check['status']}{count}")
        for row in result["mismatches"]:
            note = f"  [{row['note']}]" if row["note"] else ""
            print(f"  {row['check']}: {row['item']} missing from {row['missing_from']}{note}")
        print(f"{len(result['mismatches'])} differences, {result['unexplained']} unexplained.")
    return EXIT_FINDINGS if result["unexplained"] else EXIT_OK
