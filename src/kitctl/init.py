"""`kitctl init`: confirm locations and write kitctl's own user config (nothing else)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from kitctl import config as kit_config
from kitctl import discover
from kitctl import paths as kit_paths

CANDIDATE_PARENTS = ("Code", "code", "src", "Projects", "projects", "dev", "repos", "git")


def _detect_parents(home: Path) -> list[str]:
    """Common repo parents under ``home``, using on-disk names (no case-insensitive dupes)."""
    wanted = {name.casefold() for name in CANDIDATE_PARENTS}
    try:
        names = sorted(entry.name for entry in os.scandir(home) if entry.is_dir())
    except OSError:
        return []
    return [str(home / name) for name in names if name.casefold() in wanted]


def _ask(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def run_init(args: argparse.Namespace) -> int:
    paths = kit_paths.resolve()
    target = paths.kit_config_file
    existing = None
    try:
        existing = kit_config.load(target)
    except kit_config.ConfigError as error:
        print(
            f"kitctl: existing config is invalid ({error}); use --force to replace.",
            file=sys.stderr,
        )
        if not args.force:
            return 2
    if existing and not args.force:
        if args.non_interactive:
            print(f"kitctl: {target} exists; pass --force to overwrite.", file=sys.stderr)
            return 2
        if _ask(f"{target} exists. Overwrite?", "n").lower() not in {"y", "yes"}:
            print("Left existing config unchanged.")
            return 0

    detected = _detect_parents(paths.home)
    parents = [str(p.expanduser()) for p in args.repo_parents or []]
    warehouse = str(args.warehouse.expanduser()) if args.warehouse else str(paths.default_warehouse)
    depth = args.depth if args.depth is not None else 3

    if not args.non_interactive:
        print("kitctl reads (never writes) these harness locations:")
        print(f"  Claude Code: {paths.claude_config_dir}  (set CLAUDE_CONFIG_DIR to change)")
        print(f"  Codex:       {paths.codex_home}  (set CODEX_HOME to change)")
        default_parents = ", ".join(parents or detected) or str(paths.home / "Code")
        answer = _ask("Repository parent directories (comma-separated)", default_parents)
        parents = [part.strip() for part in answer.split(",") if part.strip()]
        warehouse = _ask("Warehouse checkout", warehouse)
        depth = int(_ask("Discovery depth", str(depth)))
    elif not parents:
        parents = detected

    settings = kit_config.UserConfig(
        repo_parents=[str(Path(p).expanduser()) for p in parents],
        warehouse=str(Path(warehouse).expanduser()),
        discovery_depth=depth,
    )
    for parent in settings.parent_paths():
        if not parent.is_dir():
            print(f"warning: repository parent does not exist: {parent}", file=sys.stderr)
    if not Path(settings.warehouse).is_dir():
        print(f"note: warehouse not found at {settings.warehouse} (needed for triage/adopt)")
    kit_config.save(settings, target)
    repos = discover.find_repos(settings.parent_paths(), settings.discovery_depth)
    print(f"Wrote {target}. Found {len(repos)} repositories. No agent configuration changed.")
    return 0
