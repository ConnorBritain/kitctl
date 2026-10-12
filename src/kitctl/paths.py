"""Platform and environment resolution. Every harness and Kit location comes from here."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def _platform(system: str | None) -> str:
    value = system or sys.platform
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "macos"
    return "linux"


@dataclass(frozen=True)
class Paths:
    """Resolved locations for one environment. Construct with :func:`resolve`."""

    platform: str
    home: Path
    claude_config_dir: Path
    claude_json: Path
    codex_home: Path
    agents_home: Path
    kit_config_dir: Path
    kit_cache_dir: Path
    default_warehouse: Path
    claude_managed: tuple[Path, ...]
    codex_managed: tuple[Path, ...]

    @property
    def harness_dirs(self) -> tuple[Path, ...]:
        """Directories whose contents Kit may read through links."""
        return (self.claude_config_dir, self.codex_home, self.agents_home)

    @property
    def kit_config_file(self) -> Path:
        return self.kit_config_dir / "config.toml"

    @property
    def report_dir(self) -> Path:
        return self.kit_cache_dir / "reports"


def _env_path(env: Mapping[str, str], name: str) -> Path | None:
    value = env.get(name, "").strip()
    return Path(value).expanduser() if value else None


def resolve(env: Mapping[str, str] | None = None, system: str | None = None) -> Paths:
    """Resolve paths from ``env`` (default ``os.environ``) for ``system`` (default host)."""
    env = os.environ if env is None else env
    platform = _platform(system)
    if platform == "windows":
        home = _env_path(env, "USERPROFILE") or _env_path(env, "HOME")
    else:
        home = _env_path(env, "HOME")
    home = home or Path.home()

    claude_override = _env_path(env, "CLAUDE_CONFIG_DIR")
    claude_dir = claude_override or home / ".claude"
    # With CLAUDE_CONFIG_DIR set, Claude Code keeps .claude.json inside that directory.
    claude_json = (claude_override or home) / ".claude.json"
    codex_home = _env_path(env, "CODEX_HOME") or home / ".codex"

    if platform == "windows":
        appdata = _env_path(env, "APPDATA") or home / "AppData" / "Roaming"
        local = _env_path(env, "LOCALAPPDATA") or home / "AppData" / "Local"
        kit_config = appdata / "kitctl"
        kit_cache = local / "kitctl" / "Cache"
        program_files = _env_path(env, "ProgramFiles") or Path("C:/Program Files")
        program_data = _env_path(env, "ProgramData") or Path("C:/ProgramData")
        claude_managed: tuple[Path, ...] = (
            program_files / "ClaudeCode",
            program_data / "ClaudeCode",  # legacy location, still read by older versions
        )
        codex_managed: tuple[Path, ...] = ()
    else:
        kit_config = (_env_path(env, "XDG_CONFIG_HOME") or home / ".config") / "kitctl"
        xdg_cache = _env_path(env, "XDG_CACHE_HOME")
        if xdg_cache:
            kit_cache = xdg_cache / "kitctl"
        elif platform == "macos":
            kit_cache = home / "Library" / "Caches" / "kitctl"
        else:
            kit_cache = home / ".cache" / "kitctl"
        if platform == "macos":
            claude_managed = (Path("/Library/Application Support/ClaudeCode"),)
        else:
            claude_managed = (Path("/etc/claude-code"),)
        codex_managed = (Path("/etc/codex"),)

    return Paths(
        platform=platform,
        home=home,
        claude_config_dir=claude_dir,
        claude_json=claude_json,
        codex_home=codex_home,
        agents_home=home / ".agents",
        kit_config_dir=kit_config,
        kit_cache_dir=kit_cache,
        default_warehouse=home / "kits",
        claude_managed=claude_managed,
        codex_managed=codex_managed,
    )
