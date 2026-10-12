"""Warehouse commands: init, triage, plan, adopt (write only kitctl dirs and the warehouse)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kitctl.commands.common import (
    EXIT_OK,
    EXIT_USAGE,
    guard,
)


def init(args: argparse.Namespace) -> int:
    from kitctl.init import run_init

    return run_init(args)


def triage(args: argparse.Namespace) -> int:
    from kitctl import report as kit_report
    from kitctl import triage
    from kitctl.warehouse import WarehouseMissing, resolve

    try:
        warehouse = resolve(args.warehouse, allow_writes=not args.list)
        built = guard(kit_report.build(args.roots))
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


def plan(args: argparse.Namespace) -> int:
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
        report = guard(inventory(args.roots))
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


def adopt(args: argparse.Namespace) -> int:
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
