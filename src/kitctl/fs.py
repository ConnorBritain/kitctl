"""The only place kitctl writes files. Every target must lie inside an allowed root.

Allowed by default: kitctl's own config, cache, and state directories. Commands that write
elsewhere (a report ``--out`` directory, the warehouse for triage/adopt) register that
root explicitly with :func:`allow`. Agent configuration is never an allowed root: the
write engine changes it only through the ``agent_*`` functions, which work inside an
:func:`agent_scope` naming the exact files a reviewed plan touches.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from kitctl import paths as kit_paths

_extra: set[Path] = set()
_agent: set[Path] = set()


class WriteRefused(PermissionError):
    pass


def allow(root: Path) -> None:
    """Permit writes below ``root`` for the rest of this process."""
    _extra.add(Path(os.path.realpath(root)))


def allowed_roots() -> tuple[Path, ...]:
    resolved = kit_paths.resolve()
    roots = {resolved.kit_config_dir, resolved.kit_cache_dir, resolved.kit_state_dir, *_extra}
    return tuple(Path(os.path.realpath(root)) for root in roots)


def check(path: Path) -> Path:
    real = Path(os.path.realpath(path))
    if not any(real == root or real.is_relative_to(root) for root in allowed_roots()):
        raise WriteRefused(f"kitctl refuses to write outside its own directories: {path}")
    return real


def write_atomic(path: Path, text: str, mode: int | None = None) -> None:
    """Write ``text`` to ``path`` via a staging file and atomic rename."""
    check(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    staging.write_text(text, "utf-8")
    if mode is not None:
        os.chmod(staging, mode)
    os.replace(staging, path)


def write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write ``data`` to ``path`` (inside an allowed root); owner-only unless ``mode``."""
    check(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, mode)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, mode)


def copy_file(source: Path, target: Path) -> None:
    """Copy bytes from ``source`` to ``target`` (inside an allowed root) atomically."""
    check(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    with source.open("rb") as reader, staging.open("wb") as writer:
        for chunk in iter(lambda: reader.read(1 << 20), b""):
            writer.write(chunk)
    os.replace(staging, target)


def remove(target: Path) -> None:
    """Delete a file inside an allowed root (used when replacing adopted warehouse copies)."""
    check(target)
    target.unlink(missing_ok=True)


# -- agent configuration (write engine only) ------------------------------------------------


@contextmanager
def agent_scope(targets: Iterable[Path]) -> Iterator[None]:
    """Permit ``agent_*`` writes to exactly ``targets`` (and their staging files)."""
    chosen = {Path(os.path.abspath(path)) for path in targets}
    _agent.update(chosen)
    try:
        yield
    finally:
        _agent.difference_update(chosen)


def _agent_check(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if absolute in _agent:
        return absolute
    name = absolute.name
    if name.startswith(".") and ".kitctl-" in name:
        if absolute.with_name(name[1:].rsplit(".kitctl-", 1)[0]) in _agent:
            return absolute
    raise WriteRefused(f"kitctl refuses to change agent configuration outside a plan: {path}")


def agent_stage(target: Path, data: bytes, token: str, mode: int | None) -> Path:
    """Write ``data`` beside ``target`` (not yet visible) and return the staging path."""
    _agent_check(target)
    staging = target.with_name(f".{target.name}.kitctl-{token}")
    with staging.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        os.chmod(staging, mode)
    return staging


def agent_mkdirs(directory: Path, under: Path) -> list[Path]:
    """Create ``directory`` (for a planned target ``under`` it); return dirs created."""
    _agent_check(under)
    created = []
    for parent in reversed([directory, *directory.parents]):
        if not parent.exists():
            parent.mkdir()
            created.append(parent)
    return created


def agent_replace(staging: Path, target: Path) -> None:
    _agent_check(target)
    _agent_check(staging)
    os.replace(staging, target)


def agent_remove(target: Path) -> None:
    _agent_check(target)
    target.unlink(missing_ok=True)


def agent_rmdir(directory: Path, under: Path) -> bool:
    """Remove an empty directory kitctl created for a planned target; False if not empty."""
    _agent_check(under)
    try:
        directory.rmdir()
    except OSError:
        return False
    return True
