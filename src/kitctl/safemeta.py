"""Turn parsed configuration into safe metadata: names, shapes, and hashes, never values."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Iterator, Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    """Hash of a JSON-like value with sorted keys; identifies config without revealing it."""
    return sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))


def basename(command: str) -> str:
    name = PureWindowsPath(command).name if "\\" in command else PurePosixPath(command).name
    return name or command


def command_basename(command: str) -> str | None:
    """Basename of the program a shell command runs, skipping leading VAR=value assignments."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        if not _ASSIGNMENT.match(token) and token not in {"env", "exec", "command"}:
            return basename(token)
    return None


def _names(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return sorted(str(key) for key in value)
    return []


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str)]
    return []


def mcp_server(config: Any) -> dict[str, Any]:
    """Transport, command basename, URL host, env/header names, and tool lists only."""
    if not isinstance(config, Mapping):
        return {"invalid": True}
    url = config.get("url") if isinstance(config.get("url"), str) else None
    command = config.get("command") if isinstance(config.get("command"), str) else None
    transport = config.get("type") or config.get("transport")
    if not isinstance(transport, str):
        transport = "http" if url else "stdio"
    meta: dict[str, Any] = {"transport": transport}
    if command:
        # Harnesses pass args separately, so the whole string is the program path (which
        # may contain spaces, e.g. "/Applications/Some App.app/Contents/MacOS/server").
        meta["command"] = basename(command.strip())
    if isinstance(config.get("args"), list):
        meta["arg_count"] = len(config["args"])
    if url:
        meta["url_host"] = urlsplit(url).hostname or ""
    for key, label in (
        ("env", "env_names"),
        ("headers", "header_names"),
        ("http_headers", "header_names"),
    ):
        names = _names(config.get(key))
        if names:
            meta[label] = sorted(set(meta.get(label, [])) | set(names))
    for key in ("env_vars", "bearer_token_env_var", "env_http_headers"):
        value = config.get(key)
        if isinstance(value, str):
            meta.setdefault("env_names", []).append(value)
        elif isinstance(value, list):
            meta.setdefault("env_names", []).extend(_strings(value))
        elif isinstance(value, Mapping):
            meta.setdefault("env_names", []).extend(str(v) for v in value.values())
    if "env_names" in meta:
        meta["env_names"] = sorted(set(meta["env_names"]))
    for key in ("enabled_tools", "disabled_tools", "allowedTools", "disallowedTools"):
        tools = _strings(config.get(key))
        if tools:
            meta[key] = tools
    if config.get("enabled") is False or config.get("disabled") is True:
        meta["disabled"] = True
    return meta


def iter_hooks(hooks: Any) -> Iterator[dict[str, Any]]:
    """Yield safe metadata for each command hook in a settings-style ``hooks`` object."""
    if isinstance(hooks, Mapping) and isinstance(hooks.get("hooks"), Mapping):
        hooks = hooks["hooks"]  # hooks.json files wrap the event map
    if not isinstance(hooks, Mapping):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            matcher = group.get("matcher")
            for hook in group.get("hooks", []) if isinstance(group.get("hooks"), list) else []:
                if not isinstance(hook, Mapping):
                    continue
                command = hook.get("command")
                args = hook.get("args")
                full = command if isinstance(command, str) else ""
                if isinstance(args, list):
                    full = json.dumps([full, *map(str, args)])
                meta: dict[str, Any] = {
                    "event": str(event),
                    "matcher": matcher if isinstance(matcher, str) else "*",
                    "type": str(hook.get("type", "command")),
                }
                if isinstance(command, str):
                    meta["command"] = command_basename(command)
                    meta["command_sha256"] = sha256_text(full)
                    meta["via_npx_kitctl"] = bool(re.search(r"\bnpx\s+(-y\s+)?kitctl\b", command))
                yield meta


def settings(document: Any) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        return {"invalid": True}
    meta: dict[str, Any] = {"keys": sorted(str(key) for key in document)}
    enabled = document.get("enabledPlugins")
    if isinstance(enabled, Mapping):
        meta["enabled_plugins"] = {str(k): bool(v) for k, v in sorted(enabled.items())}
    approved = document.get("enabledMcpjsonServers")
    if isinstance(approved, list):
        meta["enabled_mcpjson_servers"] = _strings(approved)
    return meta


_FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


def frontmatter(text: str) -> dict[str, str]:
    """Extract ``name``, ``description``, and ``when_to_use`` from YAML frontmatter
    (stdlib subset)."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    lines = match.group(1).splitlines()
    result: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or line[:1].isspace() or key not in {"name", "description", "when_to_use"}:
            continue
        value = value.strip()
        if value in {"|", ">", "|-", ">-", "|+", ">+"} or not value:
            block = []
            while index < len(lines) and (lines[index][:1].isspace() or not lines[index].strip()):
                block.append(lines[index].strip())
                index += 1
            joiner = "\n" if value.startswith("|") else " "
            value = joiner.join(part for part in block if part).strip()
        elif value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        result[key] = value
    return result
