"""`kitctl doctor`: environment health. Read-only; runs `--version` on the harness CLIs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from kitctl import __version__
from kitctl import config as kit_config
from kitctl import paths as kit_paths

_VERSION = re.compile(r"(\d+\.\d+\.\d+)")


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"check": name, "status": status, "detail": detail}


def harness_version(executable: str, timeout: float = 10.0) -> str | None:
    path = shutil.which(executable)
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = _VERSION.search(result.stdout or result.stderr or "")
    return match.group(1) if match else ""


def _writable(directory: Path) -> bool:
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        return False
    try:
        if directory.exists():
            with tempfile.NamedTemporaryFile(dir=directory):
                pass
    except OSError:
        return False
    return True


def run(with_inventory: bool = True) -> dict[str, Any]:
    from kitctl.sources import claude as claude_source
    from kitctl.sources import codex as codex_source

    paths = kit_paths.resolve()
    checks: list[dict[str, str]] = [
        _check("kitctl", "ok", f"{__version__} on Python {sys.version.split()[0]}"),
    ]
    for executable, adapter, label in (
        ("claude", claude_source, "Claude Code"),
        ("codex", codex_source, "Codex"),
    ):
        version = harness_version(executable)
        if version is None:
            checks.append(_check(label, "info", f"`{executable}` not found on PATH"))
        elif not version:
            checks.append(_check(label, "warn", f"`{executable} --version` gave no version"))
        elif version in adapter.VERIFIED_VERSIONS:
            checks.append(_check(label, "ok", f"{version} (verified adapter)"))
        else:
            verified = ", ".join(adapter.VERIFIED_VERSIONS) or "none yet"
            checks.append(_check(label, "info", f"{version}; adapter verified against: {verified}"))
    for label, location in (
        ("Claude config dir", paths.claude_config_dir),
        ("Claude user MCP (.claude.json)", paths.claude_json),
        ("Claude plugins dir", paths.claude_plugins_dir),
        ("Codex home", paths.codex_home),
        ("Shared skills (~/.agents)", paths.agents_home),
    ):
        status = "ok" if location.exists() else "info"
        checks.append(
            _check(label, status, f"{location}{'' if location.exists() else ' (absent)'}")
        )
    try:
        settings = kit_config.load(paths.kit_config_file)
        if settings is None:
            checks.append(
                _check(
                    "kitctl config", "info", f"{paths.kit_config_file} absent; run `kitctl init`"
                )
            )
        else:
            checks.append(_check("kitctl config", "ok", str(paths.kit_config_file)))
            warehouse = settings.warehouse_path()
            if warehouse and not warehouse.is_dir():
                checks.append(_check("warehouse", "warn", f"{warehouse} not found"))
            elif warehouse:
                checks.append(_check("warehouse", "ok", str(warehouse)))
    except kit_config.ConfigError as error:
        checks.append(_check("kitctl config", "error", str(error)))
    checks.append(
        _check(
            "cache dir",
            "ok" if _writable(paths.kit_cache_dir) else "error",
            str(paths.kit_cache_dir),
        )
    )
    installed = shutil.which("kitctl")
    if installed:
        checks.append(_check("kitctl on PATH", "ok", installed))
    else:
        checks.append(
            _check(
                "kitctl on PATH",
                "warn",
                "not installed as a command; hooks would need `npx kitctl` (too slow for "
                "hooks). Install with `uv tool install kitctl` or `pipx install kitctl`.",
            )
        )
    findings: list[dict[str, str]] = []
    if with_inventory:
        from kitctl.inventory import inventory

        report = inventory(None, settings=None)
        findings = report["findings"]
        hooks = [e for e in report["entries"] if e["kind"] == "hook"]
        npx = [e for e in hooks if e["metadata"].get("via_npx_kitctl")]
        checks.append(
            _check(
                "hook latency",
                "warn" if npx else "ok",
                f"{len(npx)} of {len(hooks)} hooks call `npx kitctl`"
                if npx
                else f"{len(hooks)} hooks; none call `npx kitctl`",
            )
        )
        trusted = [
            e
            for e in report["entries"]
            if e["kind"] == "settings" and e["harness"] == "codex" and e["scope"] == "user"
        ]
        count = sum(len(e["metadata"].get("trusted_projects", [])) for e in trusted)
        checks.append(_check("Codex trust", "info", f"{count} trusted projects in user config"))
        severities = {
            s: sum(f["severity"] == s for f in findings) for s in ("error", "warning", "info")
        }
        checks.append(
            _check(
                "inventory findings",
                "error" if severities["error"] else ("warn" if severities["warning"] else "ok"),
                ", ".join(f"{n} {s}" for s, n in severities.items()),
            )
        )
    return {"checks": checks, "findings": findings}
