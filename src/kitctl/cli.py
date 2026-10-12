"""Command-line entry point: argument parsing only; handlers live in kitctl.commands."""

from __future__ import annotations

import argparse
import importlib
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
    adopt_cmd.set_defaults(handler="warehouse:adopt")

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

    args = parser.parse_args(argv)
    module, name = args.handler.split(":")
    handler = getattr(importlib.import_module(f"kitctl.commands.{module}"), name)
    result: int = handler(args)
    return result
