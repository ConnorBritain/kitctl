"""Whole-warehouse validation: documents, references, and every loadout resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kitctl.model import (
    HARNESSES,
    Loadout,
    ModelError,
    Primitive,
    Problem,
    discover,
    parse_loadout,
    parse_primitive,
)
from kitctl.resolve import resolve


def _loadout_refs(loadout: Loadout) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for key in ("skills", "hooks", "plugins", "marketplaces", "requires", "optional", "disable"):
        refs += [(ref, f"{loadout.path}.{key}") for ref in getattr(loadout, key)]
    refs += [(e["primitive"], f"{loadout.path}.mcp.{s}") for s, e in loadout.mcp.items()]
    refs += [(ref, f"{loadout.path}.slots.{slot}") for slot, ref in loadout.slots.items()]
    return refs


def _primitive_refs(primitive: Primitive) -> list[tuple[str, str]]:
    refs = [(ref, f"{primitive.path}.conflicts") for ref in primitive.conflicts]
    for key in ("marketplace", "plugin"):
        if isinstance(primitive.data.get(key), str):
            refs.append((primitive.data[key], f"{primitive.path}.{key}"))
    return refs


def validate(root: Path) -> dict[str, Any]:
    refs, names, problems = discover(root)
    primitives: dict[str, Primitive] = {}
    for ref in refs:
        try:
            primitives[ref] = parse_primitive(root, ref)
        except ModelError as error:
            problems.extend(error.problems)
    loadouts: dict[str, Loadout] = {}
    for name in names:
        try:
            loadouts[name] = parse_loadout(root, name)
        except ModelError as error:
            problems.extend(error.problems)
    wanted = [r for p in primitives.values() for r in _primitive_refs(p)]
    wanted += [r for loadout in loadouts.values() for r in _loadout_refs(loadout)]
    for ref, where in wanted:
        if ref not in primitives and ref not in refs:
            problems.append(Problem(where, "missing", f"missing primitive {ref}"))
    resolutions = 0
    broken = {p.path for p in problems}
    for name, loadout in sorted(loadouts.items()):
        targets = [name, *(f"{name}:{profile}" for profile in sorted(loadout.profiles))]
        for target in targets:
            for harness in HARNESSES:
                resolutions += 1
                try:
                    result = resolve(root, target, harness)
                except ModelError as error:
                    problems.extend(p for p in error.problems if p.path not in broken)
                    continue
                problems.extend(
                    Problem(w["path"], w["code"], w["message"], w["severity"])
                    for w in result["warnings"]
                )
    unique = sorted(set(problems), key=lambda p: (p.severity != "error", p.path, p.code))
    return {
        "warehouse": str(root),
        "primitives": len(primitives),
        "loadouts": len(loadouts),
        "resolutions": resolutions,
        "problems": [p.as_dict() for p in unique],
    }
