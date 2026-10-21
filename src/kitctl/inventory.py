"""Read-only, explicitly scoped discovery. Never serialize secret-bearing values."""

from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TypedDict

from kitctl.model import frontmatter
from kitctl.native import read_native

CONFIG_PATHS = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude.json",
    ".mcp.json",
    ".codex/config.toml",
    ".codex/hooks.json",
    ".cursor/mcp.json",
    ".cursor/hooks.json",
    "CLAUDE.md",
    "AGENTS.md",
    "kit.yaml",
    "kit.lock",
)
SKILL_PATHS = (".claude/skills", ".agents/skills", ".codex/skills", ".cursor/skills")
AGENT_PATHS = ((".claude/agents", ".md"), (".codex/agents", ".toml"))


@dataclass(frozen=True)
class Entry:
    kind: str
    name: str
    root: str
    path: str
    content_hash: str


NATIVE_KEYS = (
    "plugins",
    "skill_switches",
    "marketplaces",
    "mcp_servers",
    "hooks",
    "installed_plugins",
    "managed_blocks",
)


class Report(TypedDict):
    schema_version: int
    entries: list[dict[str, str]]
    native: dict[str, list[dict[str, Any]]]
    warnings: list[str]


def _safe_file(path: Path, root: Path) -> bool:
    """Skip links, including a link in a parent directory, and special files."""
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return False
    return path.is_file()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skill_hash(directory: Path, root: Path) -> str:
    """Hash relative names and bytes of all regular skill assets deterministically."""
    digest = hashlib.sha256()

    def fail(error: OSError) -> None:
        raise error

    for current, dirs, files in os.walk(directory, followlinks=False, onerror=fail):
        dirs[:] = sorted(
            name for name in dirs if name != ".git" and not (Path(current) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(current) / name
            if _safe_file(path, root):
                digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
                digest.update(b"\0")
                digest.update(_file_hash(path).encode("ascii"))
                digest.update(b"\0")
    return digest.hexdigest()


def _agent_name(path: Path) -> str:
    """The name an agent file declares (frontmatter or TOML `name`), else its file name."""
    try:
        text = path.read_bytes().decode("utf-8")
        data = tomllib.loads(text) if path.suffix == ".toml" else frontmatter(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return path.stem
    name = data.get("name")
    return name if isinstance(name, str) and name else path.stem


def _agents(root: Path, entries: list[Entry], warnings: list[str]) -> None:
    for relative, suffix in AGENT_PATHS:
        directory = root / relative
        try:
            if directory.is_symlink() or any(
                parent.is_symlink() for parent in directory.parents if parent != root
            ):
                warnings.append(f"Skipped linked agent directory: {directory}")
                continue
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob(f"*{suffix}")):
                if path.is_symlink():
                    warnings.append(f"Skipped linked agent: {path}")
                elif _safe_file(path, root):
                    entries.append(
                        Entry("agent", _agent_name(path), str(root), str(path), _file_hash(path))
                    )
        except OSError:
            warnings.append(f"Cannot read: {directory}")


def inventory(roots: list[Path]) -> Report:
    entries: list[Entry] = []
    warnings: list[str] = []
    native: dict[str, list[dict[str, Any]]] = {key: [] for key in NATIVE_KEYS}
    home = Path.home().resolve()
    for root in sorted({path.expanduser().resolve() for path in roots}):
        if not root.is_dir():
            raise ValueError(f"Inventory root is not a directory: {root}")
        state = read_native(root, home)
        for key in NATIVE_KEYS:
            native[key].extend(state[key])
        warnings.extend(state["warnings"])
        for relative in CONFIG_PATHS:
            path = root / relative
            try:
                if _safe_file(path, root):
                    entries.append(
                        Entry("config", relative, str(root), str(path), _file_hash(path))
                    )
                elif path.exists() or path.is_symlink():
                    warnings.append(f"Skipped link or non-regular file: {path}")
            except OSError:
                warnings.append(f"Cannot read: {path}")
        for relative in SKILL_PATHS:
            directory = root / relative
            try:
                if directory.is_symlink() or any(
                    parent.is_symlink() for parent in directory.parents if parent != root
                ):
                    warnings.append(f"Skipped linked skill directory: {directory}")
                    continue
                if not directory.is_dir():
                    continue
                for skill in sorted(directory.iterdir()):
                    manifest = skill / "SKILL.md"
                    if skill.is_symlink():
                        warnings.append(f"Skipped linked skill: {skill}")
                    elif manifest.is_symlink():
                        warnings.append(f"Skipped linked skill manifest: {manifest}")
                    elif _safe_file(manifest, root):
                        try:
                            entries.append(
                                Entry(
                                    "skill",
                                    skill.name,
                                    str(root),
                                    str(skill),
                                    _skill_hash(skill, root),
                                )
                            )
                        except OSError:
                            warnings.append(f"Cannot read complete skill: {skill}")
            except OSError:
                warnings.append(f"Cannot read: {directory}")
        _agents(root, entries, warnings)
    return {
        "schema_version": 2,
        "entries": [asdict(e) for e in entries],
        "native": native,
        "warnings": warnings,
    }
