"""Write-engine commands: desired, apply, status, purge, eject.

Every command that changes agent configuration prints the full plan first, applies only
after confirmation (``--yes`` for scripts), and supports ``--dry-run``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from kitctl.commands.common import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE


def _fail(error: Exception) -> int:
    print(f"kitctl: {error}", file=sys.stderr)
    return EXIT_USAGE


def _owner(args: argparse.Namespace) -> tuple[str, Path | None]:
    if getattr(args, "repo", None) is not None:
        repo = Path(args.repo).expanduser().resolve()
        return str(repo), repo
    if getattr(args, "global_", False):
        return "user", None
    raise ValueError("choose a scope: --global (core loadout, user scope) or --repo PATH")


def _confirm(args: argparse.Namespace, prompt: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("kitctl: not a terminal; pass --yes to apply without a prompt", file=sys.stderr)
        return False
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _recover_first(args: argparse.Namespace, paths: Any) -> None:
    """Finish an interrupted apply before planning; a dry run only reports it."""
    from kitctl import engine

    if getattr(args, "dry_run", False):
        journal = engine.pending(paths)
        if journal:
            raise engine.EngineError(
                f"an apply for {journal['owner']} was interrupted; run `kitctl recover` first"
            )
        return
    message = engine.recover(paths)
    if message:
        print(message)


def _show(plan: Any, paths: Any, notes: list[str]) -> None:
    from kitctl import engine

    text = engine.render_plan(plan, paths.home)
    if text:
        print(text)
    for note in [*notes, *plan.notes]:
        print(f"note: {note}")


def _run(args: argparse.Namespace, plan: Any, paths: Any, notes: list[str], verb: str) -> int:
    from kitctl import engine

    _show(plan, paths, notes)
    if plan.conflicts:
        print(f"{len(plan.conflicts)} conflicts; nothing was changed.", file=sys.stderr)
        return EXIT_FINDINGS
    if plan.empty:
        print("Nothing to change.")
        return EXIT_OK
    files = len({w.path for w in plan.writes})
    if args.dry_run:
        print(f"Dry run: {len(plan.changes)} changes in {files} files; nothing was written.")
        return EXIT_OK
    if not _confirm(args, f"{verb} {len(plan.changes)} changes in {files} files?"):
        print("Nothing was changed.")
        return EXIT_OK
    engine.apply(plan, paths)
    print(f"Done: {len(plan.changes)} changes in {files} files. `kitctl status` shows them.")
    return EXIT_OK


def desired(args: argparse.Namespace) -> int:
    from kitctl import desired as kit_desired
    from kitctl.warehouse import WarehouseMissing, resolve

    try:
        warehouse = kit_desired.Warehouse(resolve(args.warehouse))
        result = (
            kit_desired.desired_repo(warehouse, Path(args.repo))
            if args.repo
            else kit_desired.desired_user(warehouse)
        )
    except (WarehouseMissing, kit_desired.DesiredError, OSError) as error:
        return _fail(error)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return EXIT_OK
    where = f"repository {result.repo}" if result.repo else "user scope (core)"
    print(f"Desired state for {where}; loadouts: {', '.join(result.loadouts) or 'none'}")
    for item in result.items:
        harnesses = ",".join(item.primitive.harnesses) if item.primitive else "claude"
        print(f"  {item.kind:8} {item.name:32} [{harnesses}]  from {item.source}")
    for note in result.notes:
        print(f"note: {note}")
    return EXIT_OK


def apply(args: argparse.Namespace) -> int:
    from kitctl import compile as kit_compile
    from kitctl import desired as kit_desired
    from kitctl import engine
    from kitctl import paths as kit_paths
    from kitctl.warehouse import WarehouseMissing, resolve

    paths = kit_paths.resolve()
    try:
        owner, repo = _owner(args)
        _recover_first(args, paths)
        warehouse = kit_desired.Warehouse(resolve(args.warehouse))
        wanted = (
            kit_desired.desired_repo(warehouse, repo)
            if repo
            else kit_desired.desired_user(warehouse)
        )
        compiled = kit_compile.compile_desired(wanted, paths, warehouse.root)
        if compiled.gaps:
            for gap in compiled.gaps:
                print(f"gap: {gap.item} ({gap.harness}): {gap.message}", file=sys.stderr)
            print("Nothing was changed: fix the gaps above (or narrow harnesses).", file=sys.stderr)
            return EXIT_FINDINGS
        lock = {"commit": warehouse.commit(), "loadouts": wanted.loadouts} if repo else None
        plan = engine.plan(owner, compiled.effects, paths, replace=args.replace, lock=lock)
        return _run(args, plan, paths, compiled.notes, "Apply")
    except (WarehouseMissing, kit_desired.DesiredError, engine.EngineError, OSError) as error:
        return _fail(error)


def purge(args: argparse.Namespace) -> int:
    from kitctl import engine
    from kitctl import paths as kit_paths

    paths = kit_paths.resolve()
    try:
        _recover_first(args, paths)
        if args.all:
            owners = engine.Ledger(paths).owners()
        elif args.capture:
            owners = ["capture"]
        else:
            owners = [_owner(args)[0]]
        status = EXIT_OK
        for owner in owners:
            print(f"[{owner}]")
            lock: dict[str, Any] | None = {} if owner not in ("user", "capture") else None
            plan = engine.plan(owner, [], paths, lock=lock)
            status = max(status, _run(args, plan, paths, [], "Remove"))
        return status
    except (engine.EngineError, OSError, ValueError) as error:
        return _fail(error)


def status(args: argparse.Namespace) -> int:
    from kitctl import engine
    from kitctl import paths as kit_paths

    paths = kit_paths.resolve()
    try:
        journal = engine.pending(paths)
        rows = engine.status(paths)
    except engine.EngineError as error:
        return _fail(error)
    if args.json:
        print(json.dumps({"pending_journal": bool(journal), "items": rows}, indent=2))
    else:
        if journal:
            print(
                f"An apply for {journal['owner']} was interrupted; the next apply, purge, or "
                "`kitctl recover` finishes it."
            )
        current = None
        for row in rows:
            if row["owner"] != current:
                current = row["owner"]
                print(f"[{current}]")
            where = ".".join(i for i in row["identity"] if not i.startswith("#"))
            adopted = " (pre-existing)" if row["preexisting"] else ""
            print(f"  {row['state']:8} {row['item']:28} {row['target']} {where}{adopted}")
        if not rows:
            print("kitctl owns nothing on this machine.")
    bad = journal or any(row["state"] != "ok" for row in rows)
    return EXIT_FINDINGS if bad else EXIT_OK


def recover(args: argparse.Namespace) -> int:
    from kitctl import engine
    from kitctl import paths as kit_paths

    try:
        message = engine.recover(kit_paths.resolve(), rollback=args.rollback)
    except engine.EngineError as error:
        return _fail(error)
    print(message or "Nothing to recover.")
    return EXIT_OK


def eject(args: argparse.Namespace) -> int:
    from kitctl import engine
    from kitctl import paths as kit_paths

    paths = kit_paths.resolve()
    try:
        _recover_first(args, paths)
        ledger = engine.Ledger(paths)
        if not ledger.owners():
            print("kitctl owns nothing on this machine; nothing to eject.")
            return EXIT_OK
        capture = engine.plan("capture", [], paths) if "capture" in ledger.owners() else None
        if capture is not None:
            print("[capture] kitctl's own hooks are removed")
            _show(capture, paths, [])
            if capture.conflicts:
                return EXIT_FINDINGS
        copy = paths.claude_config_dir / "kitctl-marketplace"
        source = _marketplace_source(ledger)
        plans = [p for p in engine.eject_plans(paths, copy) if p.owner != "capture"]
        for plan in plans:
            if plan.owner == "user" and source is not None:
                plan.writes.extend(engine.marketplace_writes(source, copy))
            print(f"[{plan.owner}] content stays; kitctl stops owning {len(plan.changes)} entries")
            _show(plan, paths, [])
        if args.dry_run:
            print("Dry run: nothing was written.")
            return EXIT_OK
        if not _confirm(args, "Eject (config stays as plain native files)?"):
            print("Nothing was changed.")
            return EXIT_OK
        if capture is not None:
            engine.apply(capture, paths)
        for plan in plans:
            engine.apply(plan, paths)
        leftovers = _references(paths, source)
        for path in leftovers:
            print(f"warning: {path} still mentions kitctl or the warehouse", file=sys.stderr)
        print("Ejected: kitctl owns nothing; your agent config no longer depends on it.")
        return EXIT_FINDINGS if leftovers else EXIT_OK
    except (engine.EngineError, OSError) as error:
        return _fail(error)


def _marketplace_source(ledger: Any) -> Path | None:
    for record in ledger.records.values():
        if record["identity"] == ["extraKnownMarketplaces", "kitctl"]:
            try:
                data = json.loads(Path(record["target"]).read_text("utf-8"))
                return Path(data["extraKnownMarketplaces"]["kitctl"]["source"]["path"])
            except (OSError, ValueError, KeyError, TypeError):
                return None
    return None


def _references(paths: Any, warehouse_marketplace: Path | None) -> list[str]:
    """Agent config files that still point at kitctl hooks or the warehouse marketplace."""
    candidates = [
        paths.claude_config_dir / "settings.json",
        paths.claude_json,
        paths.codex_home / "hooks.json",
        paths.codex_home / "config.toml",
    ]
    found = []
    for path in candidates:
        try:
            text = path.read_text("utf-8")
        except OSError:
            continue
        if (
            "kitctl reconcile" in text
            or "kitctl guard" in text
            or (warehouse_marketplace and str(warehouse_marketplace) in text)
        ):
            found.append(str(path))
    return found
