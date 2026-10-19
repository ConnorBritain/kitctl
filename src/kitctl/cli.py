"""Command-line entry point: argument parsing only; handlers live in kitctl.commands."""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from kitctl import __version__


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
    init.add_argument(
        "--profile", help="Machine profile: also apply loadouts/machine-<profile>.toml."
    )
    init.set_defaults(handler="warehouse:init")

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
    scan.set_defaults(handler="read:inventory")

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
    usage.set_defaults(handler="read:usage")

    report = commands.add_parser("report", help="Write an inventory, usage, and findings report.")
    report.add_argument("--format", choices=("md", "json", "html"), default="md")
    report.add_argument("--out", type=Path, help="Directory (default: kitctl's report dir).")
    report.add_argument("--force", action="store_true", help="Allow --out inside a repository.")
    report.add_argument("--root", action="append", type=Path, dest="roots", metavar="PATH")
    report.add_argument("--since", type=_date, help="Only count use on or after YYYY-MM-DD.")
    report.add_argument("--no-cache", action="store_true")
    _common(report, descriptions=True)
    report.set_defaults(handler="read:report")

    doctor = commands.add_parser("doctor", help="Check the environment and harness setup.")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument(
        "--probe",
        action="store_true",
        help="Run `claude/codex --version` (may update the harness's own caches or logs).",
    )
    doctor.set_defaults(handler="read:doctor")

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
    triage_cmd.set_defaults(handler="warehouse:triage")

    plan_cmd = commands.add_parser("plan", help="Turn triage decisions into a manual checklist.")
    plan_cmd.add_argument("--out", type=Path, help="Directory (default: kitctl's report dir).")
    plan_cmd.add_argument("--warehouse", type=Path)
    plan_cmd.add_argument("--root", action="append", type=Path, dest="roots", metavar="PATH")
    plan_cmd.set_defaults(handler="warehouse:plan")

    promote_cmd = commands.add_parser(
        "promote", help="Adopt triaged core/loadout items and write loadouts in the warehouse."
    )
    promote_cmd.add_argument("--dry-run", action="store_true", help="Show the changes only.")
    promote_cmd.add_argument(
        "--write-kit-files", action="store_true", help="Also write kit.toml into repositories."
    )
    promote_cmd.add_argument("--warehouse", type=Path)
    promote_cmd.add_argument("--root", action="append", type=Path, dest="roots", metavar="PATH")
    promote_cmd.set_defaults(handler="warehouse:promote")

    adopt_cmd = commands.add_parser(
        "adopt", help="Copy a primitive into the warehouse (never moves the original)."
    )
    adopt_cmd.add_argument(
        "target", help="Path to a skill/agent/command/rule, or mcp:<name>, or hook:<name>."
    )
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
    adopt_cmd.set_defaults(handler="warehouse:adopt")

    ctx = commands.add_parser("context", help="Context cost: skill listing and MCP tool sizes.")
    ctx.add_argument("--root", action="append", type=Path, dest="roots", metavar="PATH")
    ctx.add_argument(
        "--probe", action="store_true", help="Start MCP servers to count their tools (opt-in)."
    )
    ctx.add_argument("--timeout", type=float, default=15.0, help="Seconds per server.")
    ctx.add_argument("--json", action="store_true")
    ctx.set_defaults(handler="read:context")

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
    conform.set_defaults(handler="read:conformance")

    validate = commands.add_parser("validate", help="Validate warehouse loadouts and primitives.")
    validate.add_argument("--warehouse", type=Path, help="Warehouse path (default: config).")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler="read:validate")

    def scope(command: argparse.ArgumentParser, capture: bool = False) -> None:
        group = command.add_mutually_exclusive_group()
        group.add_argument(
            "--global",
            dest="global_",
            action="store_true",
            help="User scope: the warehouse's core loadout.",
        )
        group.add_argument("--repo", type=Path, metavar="PATH", help="A repository with kit.toml.")
        if capture:
            group.add_argument("--capture", action="store_true", help="kitctl's own hooks.")
            group.add_argument("--all", action="store_true", help="Everything kitctl owns.")

    def mutation(command: argparse.ArgumentParser) -> None:
        command.add_argument("--dry-run", action="store_true", help="Show the plan; write nothing.")
        command.add_argument("--yes", action="store_true", help="Apply without a prompt.")

    want = commands.add_parser(
        "desired", help="Show what the warehouse says to install (read-only)."
    )
    want.add_argument("--repo", type=Path, metavar="PATH", help="A repository with kit.toml.")
    want.add_argument("--warehouse", type=Path)
    want.add_argument("--json", action="store_true")
    want.set_defaults(handler="write:desired")

    apply_cmd = commands.add_parser(
        "apply", help="Install loadouts into Claude Code and Codex config (shows a diff first)."
    )
    scope(apply_cmd)
    mutation(apply_cmd)
    apply_cmd.add_argument(
        "--replace",
        action="store_true",
        help="Take over existing entries that differ (purge restores the originals).",
    )
    apply_cmd.add_argument("--warehouse", type=Path)
    apply_cmd.add_argument("--profile", help="Machine profile (default: from kitctl config).")
    apply_cmd.set_defaults(handler="write:apply")

    sync_cmd = commands.add_parser(
        "sync", help="git pull the warehouse, then apply user scope and managed repos."
    )
    mutation(sync_cmd)
    sync_cmd.add_argument("--no-pull", action="store_true", help="Don't run git pull.")
    sync_cmd.add_argument("--warehouse", type=Path)
    sync_cmd.add_argument("--profile", help="Machine profile (default: from kitctl config).")
    sync_cmd.set_defaults(handler="write:sync")

    status_cmd = commands.add_parser("status", help="What kitctl owns and whether it drifted.")
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.set_defaults(handler="write:status")

    purge_cmd = commands.add_parser(
        "purge", help="Remove exactly what kitctl inserted, restoring what was there before."
    )
    scope(purge_cmd, capture=True)
    mutation(purge_cmd)
    purge_cmd.set_defaults(handler="write:purge")

    eject_cmd = commands.add_parser(
        "eject", help="Keep the config as plain native files and stop managing it."
    )
    mutation(eject_cmd)
    eject_cmd.set_defaults(handler="write:eject")

    recover_cmd = commands.add_parser("recover", help="Finish or undo an interrupted apply.")
    recover_cmd.add_argument("--rollback", action="store_true", help="Undo instead of finish.")
    recover_cmd.set_defaults(handler="write:recover")

    capture_cmd = commands.add_parser(
        "capture", help="Add or remove kitctl's SessionStart/PreToolUse hooks."
    )
    capture_cmd.add_argument("action", choices=("install", "remove"))
    capture_cmd.add_argument("--harness", action="append", choices=("claude", "codex"))
    capture_cmd.add_argument("--command", help="kitctl command for hooks (default: from PATH).")
    mutation(capture_cmd)
    capture_cmd.set_defaults(handler="write:capture")

    reconcile_cmd = commands.add_parser(
        "reconcile", help="Copy hand-added items into the warehouse inbox (SessionStart hook)."
    )
    reconcile_cmd.add_argument("--hook", choices=("claude", "codex"), help="Read hook JSON.")
    reconcile_cmd.set_defaults(handler="write:reconcile")

    guard_cmd = commands.add_parser(
        "guard", help="PreToolUse hook: stop agent edits to kitctl-managed files."
    )
    guard_cmd.add_argument("--harness", choices=("claude", "codex"), default="claude")
    guard_cmd.set_defaults(handler="write:guard")

    market = commands.add_parser("marketplace", help="Generate a Claude plugin marketplace.")
    market.add_argument("action", choices=("build",))
    market.add_argument("--warehouse", type=Path)
    market.add_argument("--dry-run", action="store_true")
    market.set_defaults(handler="write:marketplace")

    hook = bool(argv if argv is not None else sys.argv[1:]) and (
        (argv if argv is not None else sys.argv[1:])[0] in ("guard", "reconcile")
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        if hook:
            return 0  # exit 2 would block the session or tool call; hooks fail open
        raise
    module, name = args.handler.split(":")
    handler = getattr(importlib.import_module(f"kitctl.commands.{module}"), name)
    result: int = handler(args)
    return result
