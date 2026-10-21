"""Kit command line. Every command here reads only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from kitctl import __version__, engine, starter
from kitctl import forge as forging
from kitctl import scout as scouting
from kitctl.doctor import doctor
from kitctl.inventory import inventory
from kitctl.model import DEFAULT_HARNESSES, HARNESSES, ModelError, warehouse_root
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
    start = commands.add_parser("init", help="Create a starter warehouse (default: ~/kits).")
    start.add_argument(
        "path", nargs="?", type=Path, help="Where to create it (default: KIT_WAREHOUSE or ~/kits)."
    )
    start.add_argument("--no-git", action="store_true", help="Do not run git init.")
    check = commands.add_parser("validate", help="Check warehouse documents and loadouts.")
    check.add_argument(
        "path", nargs="?", type=Path, help="Warehouse root (default: KIT_WAREHOUSE or ~/kits)."
    )
    check.add_argument("--json", action="store_true", help="Print findings as JSON.")
    show = commands.add_parser(
        "resolve", help="Print a loadout's effective config and capability gaps; reads only."
    )
    show.add_argument("target", metavar="LOADOUT[:PROFILE]")
    show.add_argument("--harness", choices=HARNESSES, required=True)
    show.add_argument("--warehouse", type=Path, help="Warehouse root (default: ~/kits).")
    show.add_argument(
        "--observed",
        type=Path,
        metavar="FILE",
        help="JSON list of identities enabled outside Kit; enables stale-disable checks.",
    )
    show.add_argument("--json", action="store_true", help="Print the full result as JSON.")
    health = commands.add_parser(
        "doctor",
        help="Report tools, MCP launchability, conflicts and pending trust; fixes nothing.",
    )
    health.add_argument("--loadout", metavar="LOADOUT[:PROFILE]", help="Check one loadout.")
    health.add_argument("--harness", choices=HARNESSES, help="Default: claude and codex.")
    health.add_argument(
        "--root",
        action="append",
        type=Path,
        dest="roots",
        metavar="PATH",
        help="Repository or home directory to inspect; repeatable. Defaults to current directory.",
    )
    health.add_argument("--warehouse", type=Path, help="Warehouse root (default: ~/kits).")
    health.add_argument("--json", action="store_true", help="Print the full report as JSON.")

    def project(sub: argparse.ArgumentParser, writes: bool) -> None:
        sub.add_argument("--repo", type=Path, default=None, help="Project root (default: cwd).")
        sub.add_argument("--warehouse", type=Path, help="Warehouse root (default: ~/kits).")
        sub.add_argument("--json", action="store_true", help="Print JSON.")
        if writes:
            sub.add_argument("--yes", action="store_true", help="Write without asking.")
            sub.add_argument("--dry-run", action="store_true", help="Show the plan only.")
            # The earlier opt-in needed --live; accepted so scripts that pass it still run.
            sub.add_argument("--live", action="store_true", help=argparse.SUPPRESS)

    project(
        commands.add_parser("plan", help="Show the diff kit.yaml would produce; reads only."), False
    )
    project(
        commands.add_parser("apply", help="Write the plan (recovering an interrupted one)."), True
    )
    use = commands.add_parser("use", help="Activate a loadout or switch its profile, then apply.")
    use.add_argument("target", metavar="LOADOUT[:PROFILE]")
    project(use, True)
    off = commands.add_parser("off", help="Remove one loadout's owned entries, then apply.")
    off.add_argument("target", metavar="LOADOUT")
    project(off, True)
    project(
        commands.add_parser("status", help="Active loadouts, owned entries, drift, trust."), False
    )
    project(
        commands.add_parser("eject", help="Keep native files; drop Kit's ledger and markers."), True
    )
    scout = commands.add_parser(
        "scout", help="Quarantine candidate repos and print facts; never runs their code."
    )
    scout.add_argument("sources", nargs="+", metavar="GIT-URL|PATH")
    scout.add_argument("--cache", type=Path, help="Cache root (default: ~/.cache/kitctl).")
    scout.add_argument("--json", action="store_true", help="Print facts as JSON.")
    vend = commands.add_parser("vendor", help="Copy one pinned path from quarantine into inbox/.")
    vend.add_argument("scouted_id", metavar="HOST/OWNER/REPO@SHA")
    vend.add_argument("--path", required=True, help="Path inside the scouted repo.")
    vend.add_argument("--pin", help="Expected commit (prefix allowed).")
    vend.add_argument("--kind", default="skill", help="Primitive kind (default: skill).")
    vend.add_argument("--name", help="Primitive name (default: the path's last part).")
    vend.add_argument("--policy", type=Path, help="License policy file (default: warehouse).")
    vend.add_argument("--warehouse", type=Path, help="Warehouse root (default: ~/kits).")
    vend.add_argument("--cache", type=Path, help="Cache root (default: ~/.cache/kitctl).")
    forge = commands.add_parser("forge", help="Brief, propose and verify a researched loadout.")
    steps = forge.add_subparsers(dest="step", required=True)
    brief = steps.add_parser("new", help="Write forge/<slug>/brief.yaml.")
    brief.add_argument("slug")
    brief.add_argument("--purpose", required=True)
    brief.add_argument("--harness", action="append", choices=HARNESSES)
    brief.add_argument("--repo", action="append", dest="repos", help="Target repo; repeatable.")
    brief.add_argument("--slot", action="append", dest="slots", help="Required slot; repeatable.")
    brief.add_argument("--exclude", action="append", help="Excluded candidate; repeatable.")
    for name, text in (
        ("propose", "Assemble inbox/loadouts/<slug>.yaml and gaps.md."),
        ("verify", "Run the brief's evals."),
    ):
        step = steps.add_parser(name, help=text)
        step.add_argument("slug")
    for step in steps.choices.values():
        step.add_argument("--warehouse", type=Path, help="Warehouse root (default: ~/kits).")
        step.add_argument("--cache", type=Path, help="Cache root (default: ~/.cache/kitctl).")
        step.add_argument("--json", action="store_true", help="Print JSON.")
    steps.choices["verify"].add_argument(
        "--with-plugin-eval", action="store_true", help="Also run `claude plugin eval` suites."
    )
    args = parser.parse_args(argv)
    if args.command == "forge":
        return _forge(args)
    if args.command == "scout":
        return _scout(args)
    if args.command == "vendor":
        return _vendor(args)
    if args.command in {"plan", "apply", "use", "off", "status", "eject"}:
        return _project_command(args)
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "init":
        return _init(args)
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


def _doctor(args: argparse.Namespace) -> int:
    harnesses = (args.harness,) if args.harness else DEFAULT_HARNESSES
    try:
        report = doctor(
            warehouse_root(args.warehouse), args.roots or [Path.cwd()], args.loadout, harnesses
        )
    except (OSError, ValueError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for tool in report["tools"]:
            print(f"{tool['status']:8}  {tool['identity']}  {tool['output']}")
        for finding in report["findings"]:
            print(
                f"{finding['severity']:7}  {finding['check']}: {finding['subject']}: "
                f"{finding['message']}"
            )
            if finding["hint"]:
                print(f"         hint: {finding['hint']}")
        print("Doctor reports only; no files changed.")
    return 1 if any(f["severity"] == "error" for f in report["findings"]) else 0


def _print_plan(plan: engine.Plan, as_json: bool) -> None:
    if as_json:
        print(json.dumps({**plan.summary(), "diff": plan.diff()}, indent=2))
        return
    diff = plan.diff()
    print(diff if diff else "No changes.")
    for op in plan.ops:
        print(f"{op['action']:12} {op['id']}")
    for gap in plan.gaps:
        print(f"gap [{gap['severity']}] {gap['harness']} {gap['subject']}: {gap['reason']}")
    for step in plan.setup:
        print(f"setup (not run; outputs are not Kit-owned): {' '.join(step['run'])}")
    for conflict in plan.conflicts:
        print(f"conflict: {conflict}", file=sys.stderr)


def _init(args: argparse.Namespace) -> int:
    root = warehouse_root(args.path)
    try:
        made = starter.init_warehouse(root, git=not args.no_git)
    except starter.InitError as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return 1
    print(f"created a warehouse at {root}:")
    for rel in made:
        print(f"  {rel}")
    default = warehouse_root(None)
    hint = "" if root == default else f" (set KIT_WAREHOUSE={root} or pass --warehouse {root})"
    print(f"next: kitctl validate {root}, then `kitctl use starter` in a repo{hint}")
    return 0


def _confirm(args: argparse.Namespace, question: str) -> bool:
    """--yes, or a y at an interactive prompt (on stderr, so --json output stays clean)."""
    if args.yes:
        return True
    if args.dry_run or not sys.stdin.isatty():
        return False
    print(f"{question} [y/N] ", end="", file=sys.stderr, flush=True)
    try:
        return input().strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def _project_command(args: argparse.Namespace) -> int:
    repo = (args.repo or Path.cwd()).resolve()
    warehouse = warehouse_root(args.warehouse)
    if getattr(args, "live", False):
        print(
            "kitctl: --live is no longer needed; writes ask first (--yes skips it)", file=sys.stderr
        )
    try:
        if args.command == "status":
            report = engine.status(repo, warehouse)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(f"loadouts: {', '.join(report['loadouts']) or 'none'}")
                if report["interrupted"]:
                    print("an apply was interrupted; run `kitctl apply --yes` to recover")
                for harness, slots in report["slots"].items():
                    for slot, provider in slots.items():
                        print(f"{harness} slot {slot}: {provider or 'unfilled'}")
                for entry in report["entries"]:
                    owners = ", ".join(
                        o["loadout"] + (f":{o['profile']}" if o["profile"] else "")
                        for o in entry["owners"]
                    )
                    print(f"{entry['state']:7} {entry['id']}  ({owners})")
                for item in report["pending_trust"]:
                    print(f"pending trust: {item}")
            drift = any(e["state"] != "ok" for e in report["entries"])
            return 1 if drift or report["interrupted"] else 0
        if args.command == "eject":
            print(
                "eject keeps native files and removes kit.yaml, kit.lock, .kit/ and block markers"
            )
            if args.dry_run:
                return 0
            engine.check_write_allowed(repo)
            if not _confirm(args, "Eject?"):
                print("Nothing written; re-run with --yes to eject.", file=sys.stderr)
                return 1
            for target in engine.eject(repo):
                print(f"ejected {target}")
            return 0
        recovered = False
        if args.command != "plan" and (repo / engine.JOURNAL).exists():
            print("an apply was interrupted")
            engine.check_write_allowed(repo)
            if not _confirm(args, "Recover it first?"):
                print("Nothing written; re-run with --yes to recover it first.", file=sys.stderr)
                return 1
            recovered = engine.recover(repo)
        intent = engine.read_intent(repo)
        if args.command == "use":
            intent = engine.with_loadout(intent, args.target)
        elif args.command == "off":
            intent = engine.without_loadout(intent, args.target)
        plan = engine.make_plan(repo, warehouse, intent)
        _print_plan(plan, args.json)
        if args.command != "plan" and (plan.changes or plan.remove_dirs) and not plan.conflicts:
            for warning in engine.write_warnings(repo):
                print(f"warning: {warning}", file=sys.stderr)
        if args.command == "plan" or args.dry_run:
            return 1 if plan.conflicts else 0
        if plan.changes or plan.remove_dirs:
            engine.check_write_allowed(repo)
            if not plan.conflicts and not _confirm(args, f"Write {len(plan.changes)} files?"):
                print("Nothing written; re-run with --yes to apply.", file=sys.stderr)
                return 1
        written = engine.apply_plan(plan)
        if recovered:
            print("recovered an interrupted apply")
        print(f"wrote {len(written)} files" if written else "already up to date")
        return 0
    except engine.EngineError as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return 2


def _scout(args: argparse.Namespace) -> int:
    cache = scouting.cache_root(args.cache)
    reports, failed = [], False
    for source in args.sources:
        try:
            reports.append(scouting.scout(source, cache))
        except (scouting.ScoutError, OSError) as error:
            print(f"kitctl: {source}: {error}", file=sys.stderr)
            failed = True
    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for report in reports:
            print(
                f"{report['id']}  license {report['license']}  last commit {report['last_commit']}"
            )
            for manifest in report["manifests"]:
                print(f"  manifest {manifest['kind']}: {manifest['file']}")
            for key in (
                "install_scripts",
                "telemetry",
                "outbound_network",
                "self_fetching",
                "aggressive_triggers",
            ):
                for item in report[key]:
                    where = f"{item['evidence']['file']}:{item['evidence']['line']}"
                    print(f"  {key}: {item['detail']}  ({where})")
    return 1 if failed else 0


def _vendor(args: argparse.Namespace) -> int:
    try:
        result = scouting.vendor(
            args.scouted_id,
            args.path,
            warehouse_root(args.warehouse),
            scouting.cache_root(args.cache),
            kind=args.kind,
            name=args.name,
            pin=args.pin,
            policy_file=args.policy,
        )
    except (scouting.ScoutError, OSError) as error:
        print(f"kitctl: {error}", file=sys.stderr)
        return 1
    print(
        f"vendored {result['primitive']} into {result['metadata']} ({result['license']}); "
        "it stays in inbox/ until triage"
    )
    return 0


def _forge(args: argparse.Namespace) -> int:
    warehouse = warehouse_root(args.warehouse)
    cache = scouting.cache_root(args.cache)
    try:
        if args.step == "new":
            path = forging.new_brief(
                warehouse,
                args.slug,
                args.purpose,
                harnesses=args.harness,
                target_repos=args.repos,
                required_slots=args.slots,
                exclusions=args.exclude,
            )
            print(f"wrote {path}; next: research with the kit-forge skill into candidates.yaml")
            return 0
        if args.step == "propose":
            result = forging.propose(warehouse, args.slug, cache)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                for path in result["written"]:
                    print(f"wrote {path}")
                for ident, reasons in result["excluded"].items():
                    print(f"excluded {ident}: {'; '.join(reasons)}")
                for slot in result["unfilled"]:
                    print(f"unfilled slot {slot}")
                for command in result["pending_vendor"]:
                    print(f"vendor first: {command}")
            return 0
        record = forging.verify(warehouse, args.slug, cache, args.with_plugin_eval)
        if args.json:
            print(json.dumps(record, indent=2))
        else:
            for item in record["results"]:
                print(f"{item['status']:7}  {item['name']}")
        return 0 if record["ok"] else 1
    except (forging.ForgeError, scouting.ScoutError) as error:
        for problem in getattr(error, "problems", [str(error)]):
            print(f"kitctl: {problem}", file=sys.stderr)
        return 1
