"""kitctl's own user configuration (TOML). kitctl never writes agent configuration here."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class ConfigError(ValueError):
    pass


@dataclass
class UserConfig:
    repo_parents: list[str] = field(default_factory=list)
    warehouse: str = ""
    discovery_depth: int = 3
    stale_days: int = 60
    schema_version: int = SCHEMA_VERSION

    def parent_paths(self) -> list[Path]:
        return [Path(value).expanduser() for value in self.repo_parents]

    def warehouse_path(self) -> Path | None:
        return Path(self.warehouse).expanduser() if self.warehouse else None


_TYPES: dict[str, type] = {
    "schema_version": int,
    "repo_parents": list,
    "warehouse": str,
    "discovery_depth": int,
    "stale_days": int,
}


def parse(text: str, source: str = "config") -> UserConfig:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{source}: invalid TOML ({error})") from error
    unknown = sorted(set(data) - set(_TYPES))
    if unknown:
        raise ConfigError(f"{source}: unknown keys {', '.join(unknown)}")
    for key, expected in _TYPES.items():
        value = data.get(key)
        if key in data and (
            not isinstance(value, expected) or (expected is int and isinstance(value, bool))
        ):
            raise ConfigError(f"{source}: {key} must be {expected.__name__}")
    if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ConfigError(f"{source}: unsupported schema_version {data['schema_version']}")
    parents = data.get("repo_parents", [])
    if not all(isinstance(item, str) for item in parents):
        raise ConfigError(f"{source}: repo_parents must be a list of strings")
    config = UserConfig(**data)
    if not 0 <= config.discovery_depth <= 10:
        raise ConfigError(f"{source}: discovery_depth must be between 0 and 10")
    return config


def load(path: Path) -> UserConfig | None:
    if not path.is_file():
        return None
    return parse(path.read_text("utf-8"), str(path))


def _toml_string(value: str) -> str:
    escaped = []
    for char in value:
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            escaped.append(f"\\u{ord(char):04X}")
        else:
            escaped.append(char)
    return '"' + "".join(escaped) + '"'


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "[" + ", ".join(_toml_string(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value: {value!r}")


def dumps(config: UserConfig) -> str:
    lines = [
        "# kitctl user configuration. Edit freely; unknown keys are rejected.",
        f"schema_version = {config.schema_version}",
        f"repo_parents = {_toml_value(config.repo_parents)}",
        f"warehouse = {_toml_value(config.warehouse)}",
        f"discovery_depth = {config.discovery_depth}",
        f"stale_days = {config.stale_days}",
    ]
    return "\n".join(lines) + "\n"


def save(config: UserConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.write_text(dumps(config), "utf-8")
    os.replace(staging, path)
