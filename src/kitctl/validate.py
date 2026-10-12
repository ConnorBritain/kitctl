"""`kitctl validate`: check warehouse documents (TOML) without changing anything.

Layout (docs/design.md): ``loadouts/<name>.toml`` and
``primitives/<kind>/<name>/primitive.toml``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from kitctl.model import Finding

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PRIMITIVE_KINDS = {"skills": "skill", "mcp": "mcp", "hooks": "hook", "plugins": "plugin"}
LOADOUT_LISTS = {"skills": "skill", "mcp": "mcp", "hooks": "hook", "plugins": "plugin"}
LOADOUT_FIELDS = {
    "schema_version": int,
    "name": str,
    "description": str,
    "extends": list,
    **{key: list for key in LOADOUT_LISTS},
}
PRIMITIVE_FIELDS = {
    "schema_version": int,
    "kind": str,
    "name": str,
    "description": str,
    "tags": list,
    "harnesses": list,
    "source": dict,
    "content_hash": str,
}
SOURCE_FIELDS = {"origin": str, "path": str, "adopted_at": str, "original_hash": str}
HARNESSES = {"claude", "codex"}


def _safe_relative(value: str) -> bool:
    for flavor in (PurePosixPath(value), PureWindowsPath(value)):
        if flavor.is_absolute() or flavor.drive or ".." in flavor.parts:
            return False
    return True


class _Validator:
    def __init__(self, warehouse: Path) -> None:
        self.warehouse = warehouse
        self.findings: list[Finding] = []

    def error(self, path: Path, message: str, code: str = "invalid-document") -> None:
        self.findings.append(Finding("error", code, str(path), message))

    def load(self, path: Path) -> dict[str, Any] | None:
        try:
            return tomllib.loads(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            self.error(path, f"Cannot read: {error}", "unreadable")
        except tomllib.TOMLDecodeError as error:
            self.error(path, f"Invalid TOML: {error}", "invalid-toml")
        return None

    def fields(
        self, path: Path, data: dict[str, Any], spec: dict[str, type], required: tuple[str, ...]
    ) -> None:
        for key in sorted(set(data) - set(spec)):
            self.error(path, f"Unknown field {key!r}", "unknown-field")
        for key in required:
            if key not in data:
                self.error(path, f"Missing required field {key!r}", "missing-field")
        for key, expected in spec.items():
            value = data.get(key)
            if key in data and (not isinstance(value, expected) or isinstance(value, bool)):
                self.error(path, f"{key!r} must be {expected.__name__}", "wrong-type")
            if isinstance(value, list) and not all(isinstance(item, str) for item in value):
                self.error(path, f"{key!r} must contain only strings", "wrong-type")
        if data.get("schema_version", 1) != 1:
            self.error(path, f"Unsupported schema_version {data['schema_version']}")

    def name(self, path: Path, value: Any, expected: str) -> None:
        if not isinstance(value, str) or not SLUG.match(value):
            self.error(path, f"Unsafe name {value!r}; use lowercase slug characters", "unsafe-name")
        elif value != expected:
            self.error(path, f"name {value!r} does not match location {expected!r}")

    def primitives(self) -> set[tuple[str, str]]:
        known: set[tuple[str, str]] = set()
        root = self.warehouse / "primitives"
        if not root.is_dir():
            return known
        for folder, kind in PRIMITIVE_KINDS.items():
            directory = root / folder
            if not directory.is_dir():
                continue
            for item in sorted(directory.iterdir()):
                document = item / "primitive.toml"
                if not item.is_dir() or not document.is_file():
                    continue
                data = self.load(document)
                if data is None:
                    continue
                self.fields(document, data, PRIMITIVE_FIELDS, ("schema_version", "kind", "name"))
                self.name(document, data.get("name"), item.name)
                if data.get("kind") != kind:
                    self.error(document, f"kind must be {kind!r} under primitives/{folder}")
                unknown = set(data.get("harnesses", [])) - HARNESSES
                if unknown:
                    self.error(document, f"Unknown harnesses: {', '.join(sorted(unknown))}")
                if "content_hash" in data and not SHA256.match(str(data["content_hash"])):
                    self.error(document, "content_hash must be a SHA-256 hex digest")
                source = data.get("source", {})
                if isinstance(source, dict):
                    for key in sorted(set(source) - set(SOURCE_FIELDS)):
                        self.error(document, f"Unknown field source.{key}", "unknown-field")
                    if isinstance(source.get("path"), str) and not _safe_relative(source["path"]):
                        self.error(
                            document, "source.path must be relative without '..'", "path-traversal"
                        )
                known.add((kind, item.name))
        return known

    def loadouts(self, primitives: set[tuple[str, str]]) -> None:
        directory = self.warehouse / "loadouts"
        if not directory.is_dir():
            return
        graph: dict[str, list[str]] = {}
        files: dict[str, Path] = {}
        for document in sorted(directory.glob("*.toml")):
            data = self.load(document)
            if data is None:
                continue
            self.fields(document, data, LOADOUT_FIELDS, ("schema_version", "name"))
            self.name(document, data.get("name"), document.stem)
            graph[document.stem] = [x for x in data.get("extends", []) if isinstance(x, str)]
            files[document.stem] = document
            for key, kind in LOADOUT_LISTS.items():
                for reference in data.get(key, []):
                    if not isinstance(reference, str):
                        continue
                    ref_kind, _, ref_name = reference.rpartition(":")
                    if ref_kind and ref_kind != kind:
                        self.error(document, f"{key} entry {reference!r} has kind {ref_kind!r}")
                        continue
                    if not _safe_relative(ref_name) or "/" in ref_name or "\\" in ref_name:
                        self.error(
                            document,
                            f"Reference {reference!r} escapes the warehouse",
                            "path-traversal",
                        )
                    elif (kind, ref_name) not in primitives:
                        self.error(
                            document,
                            f"{key} references missing {kind} {ref_name!r}",
                            "missing-reference",
                        )
        for name, parents in graph.items():
            for parent in parents:
                if parent not in graph:
                    self.error(
                        files[name], f"extends missing loadout {parent!r}", "missing-reference"
                    )
        self._cycles(graph, files)

    def _cycles(self, graph: dict[str, list[str]], files: dict[str, Path]) -> None:
        state: dict[str, int] = {}
        reported: set[str] = set()

        def visit(node: str, stack: list[str]) -> None:
            state[node] = 1
            for parent in graph.get(node, []):
                if state.get(parent) == 1:
                    cycle = (
                        stack[stack.index(parent) :] + [parent]
                        if parent in stack
                        else [node, parent]
                    )
                    if parent not in reported:
                        reported.add(parent)
                        self.error(files[node], f"extends cycle: {' -> '.join(cycle)}", "cycle")
                elif parent in graph and state.get(parent) is None:
                    visit(parent, [*stack, parent])
            state[node] = 2

        for node in sorted(graph):
            if state.get(node) is None:
                visit(node, [node])


def validate(warehouse: Path) -> dict[str, Any]:
    if not warehouse.is_dir():
        raise FileNotFoundError(f"Warehouse not found: {warehouse}")
    checker = _Validator(warehouse)
    primitives = checker.primitives()
    checker.loadouts(primitives)
    loadouts = (
        len(list((warehouse / "loadouts").glob("*.toml")))
        if (warehouse / "loadouts").is_dir()
        else 0
    )
    return {
        "warehouse": str(warehouse),
        "primitives": len(primitives),
        "loadouts": loadouts,
        "findings": [f.to_dict() for f in checker.findings],
    }
