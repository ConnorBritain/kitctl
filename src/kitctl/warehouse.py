"""Locate the warehouse (kitctl's only write target outside its own dirs)."""

from __future__ import annotations

from pathlib import Path

from kitctl import config as kit_config
from kitctl import fs
from kitctl import paths as kit_paths


class WarehouseMissing(FileNotFoundError):
    pass


def resolve(explicit: Path | None = None, allow_writes: bool = False) -> Path:
    paths = kit_paths.resolve()
    if explicit is not None:
        root = explicit.expanduser()
    else:
        settings = kit_config.load(paths.kit_config_file)
        root = (settings.warehouse_path() if settings else None) or paths.default_warehouse
    if not root.is_dir():
        raise WarehouseMissing(
            f"Warehouse not found at {root}; create or clone it first (e.g. git clone "
            "<your agent-kits repo> ~/kits), or set warehouse in kitctl's config."
        )
    if allow_writes:
        fs.allow(root)
    return root
