"""The only place kitctl writes files. Every target must lie inside an allowed root.

Allowed by default: kitctl's own config and cache directories. Commands that write
elsewhere (a report ``--out`` directory, the warehouse for triage/adopt) register that
root explicitly with :func:`allow`. Agent configuration is never an allowed root.
"""

from __future__ import annotations

import os
from pathlib import Path

from kitctl import paths as kit_paths

_extra: set[Path] = set()


class WriteRefused(PermissionError):
    pass


def allow(root: Path) -> None:
    """Permit writes below ``root`` for the rest of this process."""
    _extra.add(Path(os.path.realpath(root)))


def allowed_roots() -> tuple[Path, ...]:
    resolved = kit_paths.resolve()
    roots = {resolved.kit_config_dir, resolved.kit_cache_dir, *_extra}
    return tuple(Path(os.path.realpath(root)) for root in roots)


def check(path: Path) -> Path:
    real = Path(os.path.realpath(path))
    if not any(real == root or real.is_relative_to(root) for root in allowed_roots()):
        raise WriteRefused(f"kitctl refuses to write outside its own directories: {path}")
    return real


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a staging file and atomic rename."""
    check(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    staging.write_text(text, "utf-8")
    os.replace(staging, path)
