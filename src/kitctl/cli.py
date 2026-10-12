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
        if not any(result[key] for key in ("primitives", "loadouts", "staged", "decisions")):
            print(f"Nothing to validate in {result['warehouse']} (0 primitives, 0 loadouts).")
        else:
            print(
                f"{result['primitives']} primitives, {result['staged']} in inbox/attic, "
                f"{result['loadouts']} loadouts, {result['decisions']} triage decisions; "
                f"{len(result['findings'])} problems in {result['warehouse']}"
            )
    return _exit_for(result)


def _conformance(args: argparse.Namespace) -> int:
    from kitctl import conformance
    from kitctl.inventory import inventory

    print(
        "conformance runs harness CLIs (claude/codex list commands); they may update their "
        "own caches or logs. kitctl changes no agent configuration.",
        file=sys.stderr,
    )
    cwd = (args.repo or Path.cwd()).expanduser()
    report = inventory([cwd] if args.repo else None)
    result = _guard_any(conformance.run(report, cwd, health_checks=args.health_checks))
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


def _triage(args: argparse.Namespace) -> int:
    from kitctl import report as kit_report
    from kitctl import triage
    from kitctl.warehouse import WarehouseMissing, resolve

    try:
        warehouse = resolve(args.warehouse, allow_writes=not args.list)
        built = _guard(kit_report.build(args.roots))
    except (WarehouseMissing, OSError, ValueError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    items = [
        item
        for item in triage.collect_items(built)
        if (not args.kind or item.kind == args.kind)
        and (not args.harness or item.harness == args.harness)
    ]
    decisions = {} if args.redo else triage.load(warehouse)
    if args.list:
        for item in items:
            proposal = triage.propose(item)
            mark = "decided" if item.key in decisions else proposal.decision
            target = f" -> {proposal.loadout}" if proposal.loadout else ""
            print(
                f"{mark:8} {item.kind:10} {item.harness:7} {item.name}{target}  ({proposal.reason})"
            )
        print(f"{len(items)} items; nothing written.", file=sys.stderr)
        return EXIT_OK
    if args.accept_proposals:
        added = 0
        for item in items:
            if item.key not in decisions:
                proposal = triage.propose(item)
                decisions[item.key] = triage.record(item, proposal, proposal.decision, "proposal")
                added += 1
        triage.save(warehouse, decisions)
        print(
            f"Recorded {added} proposals in {triage.triage_path(warehouse)}. "
            "Review or edit them, then run `kitctl plan`. No agent configuration changed."
        )
        return EXIT_OK
    decided = triage.interactive(items, decisions, warehouse)
    print(
        f"\nSaved {decided} decisions to {triage.triage_path(warehouse)}. Next: `kitctl plan`. "
        "No agent configuration changed."
    )
    return EXIT_OK


def _plan(args: argparse.Namespace) -> int:
    from kitctl import fs, triage
    from kitctl import paths as kit_paths
    from kitctl import plan as kit_plan
    from kitctl.inventory import inventory
    from kitctl.report import default_path
    from kitctl.warehouse import WarehouseMissing, resolve

    try:
        warehouse = resolve(args.warehouse)
        decisions = triage.load(warehouse)
        if not decisions:
            raise ValueError(
                f"No triage decisions in {triage.triage_path(warehouse)}; run `kitctl triage` first"
            )
        report = _guard(inventory(args.roots))
    except (WarehouseMissing, OSError, ValueError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    windows = kit_paths.resolve().platform == "windows"
    text = kit_plan.build(decisions, report["entries"], windows)
    target = default_path("md").with_name(default_path("md").name.replace("report-", "plan-"))
    if args.out:
        fs.allow(args.out.expanduser())
        target = args.out.expanduser() / target.name
    fs.write_atomic(target, text)
    print(target)
    print(
        f"{len(decisions)} decisions turned into a manual checklist; nothing was applied.",
        file=sys.stderr,
    )
    return EXIT_OK


def _adopt(args: argparse.Namespace) -> int:
    from kitctl import adopt
    from kitctl.warehouse import WarehouseMissing, resolve

    try:
        warehouse = resolve(args.warehouse, allow_writes=True)
        if args.target.startswith("mcp:"):
            from kitctl.inventory import inventory

            report = inventory([Path(args.repo)] if args.repo else None)
            result = adopt.adopt_mcp(
                args.target[4:],
                report["entries"],
                warehouse,
                args.to,
                harness=args.harness,
                scope=args.scope,
                repo=args.repo,
                force=args.force,
            )
        else:
            result = adopt.adopt_path(
                Path(args.target),
                warehouse,
                args.to,
                kind=args.kind,
                name=args.name,
                force=args.force,
            )
    except (WarehouseMissing, adopt.AdoptError, OSError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return EXIT_USAGE
    print(f"{result['status']}: {result['target']}")
    if result.get("env_references"):
        print(
            "set these environment variables where the server runs: "
            + ", ".join(result["env_references"])
        )
    print("The original was not modified.", file=sys.stderr)
    return EXIT_OK


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

    triage_cmd = commands.add_parser(
        "triage", help="Decide core/loadout/retire/merge per item; saves to the warehouse."
    )
    triage_cmd.add_argument("--list", action="store_true", help="Show proposals; write nothing.")
    triage_cmd.add_argument(
        "--accept-proposals",
        action="store_true",
        help="Record every proposal as a decision (review afterwards).",
    )
    triage_cmd.add_argument("--redo", action="store_true", help="Revisit decided items.")
    triage_cmd.add_argument("--kind", help="Only this kind (skill, mcp_server, plugin, ...).")
    triage_cmd.add_argument("--harness", choices=("claude", "codex", "shared"))
    triage_cmd.add_argument("--warehouse", type=Path)
    triage_cmd.add_argument("--root", action="append", type=Path, dest="roots", metavar="PATH")
    triage_cmd.set_defaults(handler=_triage)

    plan_cmd = commands.add_parser("plan", help="Turn triage decisions into a manual checklist.")
    plan_cmd.add_argument("--out", type=Path, help="Directory (default: kitctl's report dir).")
    plan_cmd.add_argument("--warehouse", type=Path)
    plan_cmd.add_argument("--root", action="append", type=Path, dest="roots", metavar="PATH")
    plan_cmd.set_defaults(handler=_plan)

    adopt_cmd = commands.add_parser(
        "adopt", help="Copy a primitive into the warehouse (never moves the original)."
    )
    adopt_cmd.add_argument("target", help="Path to a skill/agent/command/rule/hook, or mcp:<name>.")
    adopt_cmd.add_argument("--to", choices=("inbox", "primitives", "attic"), default="inbox")
    adopt_cmd.add_argument(
        "--kind", choices=("skill", "agent", "command", "rules", "hook", "plugin")
    )
    adopt_cmd.add_argument("--name", help="Name in the warehouse (default: from the source).")
    adopt_cmd.add_argument("--harness", choices=("claude", "codex"), help="For mcp: targets.")
    adopt_cmd.add_argument("--scope", choices=("managed", "user", "project", "local", "plugin"))
    adopt_cmd.add_argument("--repo", help="For mcp: targets defined in one repository.")
    adopt_cmd.add_argument("--force", action="store_true", help="Replace a different copy.")
    adopt_cmd.add_argument("--warehouse", type=Path)
    adopt_cmd.set_defaults(handler=_adopt)

    conform = commands.add_parser(
        "conformance", help="Compare inventory with claude/codex list commands (runs them)."
    )
    conform.add_argument("--repo", type=Path, help="Repository to compare in (default: cwd).")
    conform.add_argument(
        "--health-checks",
        action="store_true",
        help="Also run `claude mcp list`, which starts and health-checks MCP servers.",
    )
    conform.add_argument("--json", action="store_true")
    conform.set_defaults(handler=_conformance)

    validate = commands.add_parser("validate", help="Validate warehouse loadouts and primitives.")
    validate.add_argument("--warehouse", type=Path, help="Warehouse path (default: config).")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=_validate)

    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result
