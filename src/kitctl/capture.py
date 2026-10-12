"""Capture hooks: ``kitctl reconcile`` (SessionStart) and ``kitctl guard`` (PreToolUse).

* **reconcile** notices skills, agents, commands, rules, and MCP servers you added by hand
  (not through kitctl) and copies them into the warehouse inbox with provenance, ready
  for ``kitctl triage``. It never activates or removes anything. A fingerprint of the
  watched locations makes the common no-change run cheap; any error exits 0 so a session
  always starts.
* **guard** denies agent edits to files kitctl owns entirely (installed skills, agents,
  rules, hook scripts, kit.lock, the ledger) and asks before edits to shared files that
  hold kitctl entries. It is a guardrail against accidental drift, not a security
  boundary: shell commands are matched by text, and tools that bypass the hook aren't seen.

``capture install`` adds both hooks through the write engine (owner ``capture``), so
``capture remove`` / ``purge --capture`` take them out exactly. Codex asks the user to
review new hooks; kitctl never marks them trusted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from kitctl.paths import Paths

OWNER = "capture"
APPLYING = "KITCTL_APPLYING"
CLAUDE_GUARD_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"
CODEX_GUARD_MATCHER = "Bash|apply_patch|Edit|Write"
CAPTURE_KINDS = ("skill", "agent", "command", "rules", "mcp_server")
_REDIRECT = re.compile(r"\d*>>?\s*/dev/null|\d*>&\d|<\(")
_WRITE_WORDS = re.compile(
    r"(^|[\s;&|(])(rm|rmdir|mv|cp|tee|truncate|chmod|ln|install|rsync|dd|unlink)\s|>>?"
    r"|\bsed\s+-i|\bperl\s+-i|\s-delete\b"
)
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


# -- capture install ------------------------------------------------------------------------


def executable(override: str | None = None) -> str:
    """Absolute path of an installed kitctl for hook commands (never npx)."""
    if override:
        return override
    found = shutil.which("kitctl")
    if not found:
        raise ValueError(
            "kitctl isn't on PATH; install it with `uv tool install kitctl` or `pipx install "
            "kitctl` before adding hooks (the npm launcher is too slow for hooks)"
        )
    resolved = str(Path(found).resolve())
    if "node_modules" in resolved or "_npx" in resolved:
        raise ValueError(
            f"{found} is the npm launcher; hooks need an installed kitctl (uv tool or pipx)"
        )
    return found if " " not in found else f'"{found}"'


def effects(paths: Paths, command: str, harnesses: tuple[str, ...]) -> list[Any]:
    from kitctl.compile import Effect, element_id

    found: list[Effect] = []

    def hook(harness: str, target: Path, event: str, group: dict[str, Any], item: str) -> None:
        identity = ("hooks", event, element_id(group))
        found.append(Effect(OWNER, item, harness, "json-element", target, identity, group))

    for harness in harnesses:
        if harness == "claude":
            target = paths.claude_config_dir / "settings.json"
            matcher = CLAUDE_GUARD_MATCHER
        else:
            target = paths.codex_home / "hooks.json"
            matcher = CODEX_GUARD_MATCHER
        reconcile = {"type": "command", "command": f"{command} reconcile --hook {harness}"}
        guard = {"type": "command", "command": f"{command} guard --harness {harness}"}
        hook(harness, target, "SessionStart", {"hooks": [reconcile]}, "capture:reconcile")
        hook(harness, target, "PreToolUse", {"matcher": matcher, "hooks": [guard]}, "capture:guard")
    return found


# -- state -----------------------------------------------------------------------------------


def _state_file(paths: Paths) -> Path:
    return paths.kit_state_dir / "reconcile.json"


def _load_state(paths: Paths) -> dict[str, Any]:
    try:
        data = json.loads(_state_file(paths).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(paths: Paths, state: dict[str, Any]) -> None:
    from kitctl import fs

    fs.write_atomic(_state_file(paths), json.dumps(state, sort_keys=True))


def _watched(paths: Paths, repo: Path | None) -> list[Path]:
    claude = paths.claude_config_dir
    found = [
        claude / "skills",
        claude / "agents",
        claude / "commands",
        claude / "rules",
        claude / "settings.json",
        paths.claude_json,
        paths.codex_home / "config.toml",
        paths.codex_home / "skills",
        paths.agents_home / "skills",
    ]
    if repo is not None:
        found += [
            repo / ".claude" / "skills",
            repo / ".claude" / "agents",
            repo / ".claude" / "commands",
            repo / ".claude" / "rules",
            repo / ".mcp.json",
            repo / ".agents" / "skills",
            repo / ".codex" / "config.toml",
        ]
    return found


def fingerprint(paths: Paths, repo: Path | None) -> list[Any]:
    """Stat-level signature of the watched locations (adding a skill changes its dir)."""
    signature: list[Any] = []
    for path in _watched(paths, repo):
        try:
            stat = path.stat()
            signature.append([str(path), stat.st_mtime_ns, stat.st_size])
        except OSError:
            signature.append([str(path), None, None])
    return signature


def repo_for(cwd: Path) -> Path | None:
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _ledger_paths(paths: Paths) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """(files kitctl owns entirely, shared files with kitctl entries, owned (file, key))."""
    try:
        records = json.loads((paths.kit_state_dir / "ledger.json").read_text("utf-8"))["effects"]
    except (OSError, ValueError, KeyError, TypeError):
        return set(), set(), set()
    owned = {str(paths.kit_state_dir / "ledger.json")}
    roots: set[str] = set()
    shared: set[str] = set()
    entries: set[tuple[str, str]] = set()
    for record in records.values():
        target = record["target"]
        if record["op"] == "tree":
            owned.update(str(Path(target) / rel) for rel in record.get("files", []))
            roots.add(target)
        elif record["op"] == "file":
            owned.add(target)
        else:
            shared.add(target)
            entries.add((target, "/".join(record["identity"])))
        if record["owner"] not in ("user", OWNER):
            owned.add(str(Path(record["owner"]) / "kit.lock"))
    return owned | roots, shared, entries


# -- reconcile -------------------------------------------------------------------------------


def _baseline_key(entry: dict[str, Any]) -> str:
    return f"{entry['kind']}:{entry['harness']}:{entry['name']}:{entry['content_hash']}"


def candidates(paths: Paths, repo: Path | None) -> list[dict[str, Any]]:
    """Hand-made items: user/project scope, not plugin-provided, not owned by kitctl."""
    from kitctl.inventory import inventory

    report = inventory([repo] if repo else [], use_cache=True)
    owned, _, entries = _ledger_paths(paths)
    found = []
    for entry in report["entries"]:
        if entry["kind"] not in CAPTURE_KINDS or entry["provided_by"]:
            continue
        # User scope only: project items belong to whichever repo you opened, possibly
        # someone else's; capture those deliberately with `kitctl adopt`.
        if entry["scope"] != "user" or entry.get("link_target"):
            continue
        path = entry["path"]
        if path in owned or any(o.startswith(path.rstrip("/") + os.sep) for o in owned):
            continue
        if entry["kind"] == "mcp_server" and any(
            target == path and key.endswith("/" + entry["name"]) for target, key in entries
        ):
            continue
        found.append(entry)
    return found


def baseline(paths: Paths, repo: Path | None) -> dict[str, Any]:
    """Record what exists now so later runs capture only new additions."""
    state = _load_state(paths)
    seen = set(state.get("seen", []))
    seen.update(_baseline_key(e) for e in candidates(paths, repo))
    state["seen"] = sorted(seen)
    state.setdefault("fingerprints", {})[str(repo or "")] = fingerprint(paths, repo)
    return state


def reconcile(paths: Paths, cwd: Path, warehouse: Path | None) -> list[str]:
    """Capture new hand-made items into the inbox; returns what was captured."""
    from kitctl import adopt

    repo = repo_for(cwd)
    state = _load_state(paths)
    key = str(repo or "")
    current = fingerprint(paths, repo)
    if state.get("fingerprints", {}).get(key) == current:
        return []
    seen = set(state.get("seen", []))
    captured: list[str] = []
    new = [e for e in candidates(paths, repo) if _baseline_key(e) not in seen]
    if warehouse is not None and new:
        from kitctl import fs

        fs.allow(warehouse)
        entries = None
        for entry in new:
            try:
                if entry["kind"] == "mcp_server":
                    if entries is None:
                        from kitctl.inventory import inventory

                        entries = inventory([repo] if repo else [])["entries"]
                    result = adopt.adopt_mcp(
                        entry["name"],
                        entries,
                        warehouse,
                        "inbox",
                        harness=entry["harness"],
                        scope=entry["scope"],
                        repo=entry["repo"],
                    )
                else:
                    kind = "rules" if entry["kind"] == "rules" else entry["kind"]
                    result = adopt.adopt_path(Path(entry["path"]), warehouse, "inbox", kind=kind)
                if result["status"] == "adopted":
                    captured.append(f"{entry['kind']} {entry['name']}")
            except (adopt.AdoptError, OSError, ValueError):
                continue  # fail-open: capture what we can
    if warehouse is not None or not new:
        seen.update(_baseline_key(e) for e in new)
    state["seen"] = sorted(seen)
    state.setdefault("fingerprints", {})[key] = fingerprint(paths, repo)
    save_state(paths, state)
    return captured


def run_reconcile(harness: str | None) -> int:
    """Hook entry point: never fails the session."""
    if os.environ.get(APPLYING):
        return 0
    try:
        from kitctl import paths as kit_paths

        payload = _stdin_json() if harness else {}
        cwd = Path(payload.get("cwd") or os.getcwd())
        paths = kit_paths.resolve()
        warehouse = _warehouse(paths)
        captured = reconcile(paths, cwd, warehouse)
        if captured:
            message = (
                f"kitctl copied {len(captured)} new item(s) into {warehouse}/inbox "
                f"({', '.join(captured[:5])}); run `kitctl triage` to decide on them."
            )
            if harness == "claude":
                print(json.dumps({"systemMessage": message}))
            else:
                print(message, file=sys.stderr)
    except Exception as error:  # noqa: BLE001 - a hook must never block a session
        print(f"kitctl reconcile skipped: {error}", file=sys.stderr)
    return 0


def _warehouse(paths: Paths) -> Path | None:
    from kitctl import config as kit_config

    try:
        settings = kit_config.load(paths.kit_config_file)
    except kit_config.ConfigError:
        return None
    root = (settings.warehouse_path() if settings else None) or paths.default_warehouse
    return root if root.is_dir() else None


# -- guard -----------------------------------------------------------------------------------


def _stdin_json() -> dict[str, Any]:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    data = sys.stdin.read(1 << 20)
    try:
        value = json.loads(data) if data.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _targets(tool: str, tool_input: dict[str, Any], cwd: Path) -> tuple[list[Path], str]:
    """Paths a tool call would write, and the shell text to scan (Bash)."""
    paths = []
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    text = ""
    if tool in ("Bash", "shell", "exec_command") or "command" in tool_input:
        command = tool_input.get("command")
        text = " ".join(command) if isinstance(command, list) else str(command or "")
    for key in ("patch", "input", "command"):
        value = tool_input.get(key)
        if isinstance(value, str) and "*** " in value:
            paths.extend(match.strip() for match in _PATCH_FILE.findall(value))
    resolved = []
    for value in paths:
        path = Path(os.path.expanduser(value))
        resolved.append(path if path.is_absolute() else cwd / path)
    return resolved, text


def _fold(text: str) -> str:
    """Shell text and paths compared with forward slashes (and case-folded on Windows)."""
    text = text.replace("\\", "/")
    return text.casefold() if os.name == "nt" else text


def _norm(path: str) -> str:
    """Comparable path: normalized separators and, on Windows, case."""
    return os.path.normcase(os.path.normpath(path))


def decide(payload: dict[str, Any], paths: Paths) -> tuple[str, str] | None:
    """("deny"|"ask", reason) for a PreToolUse payload, or None to allow."""
    tool = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    cwd = Path(payload.get("cwd") or os.getcwd())
    owned, shared, _ = _ledger_paths(paths)
    if not owned and not shared:
        return None
    targets, text = _targets(tool, tool_input, cwd)
    owned_keys = {_norm(path): path for path in owned}
    shared_keys = {_norm(path): path for path in shared}
    for target in targets:
        key = _norm(str(target))
        real = owned_keys.get(key) or shared_keys.get(key) or str(target)
        if key in owned_keys:
            return "deny", (
                f"{real} is managed by kitctl. Change the warehouse copy and run `kitctl apply`, "
                "or `kitctl eject` to stop managing it."
            )
        if key in shared_keys:
            return "ask", (
                f"{real} contains entries managed by kitctl; editing them causes drift that "
                "`kitctl apply` will report."
            )
    text = _REDIRECT.sub(" ", text)
    if text and _WRITE_WORDS.search(text):
        home = str(paths.home)
        haystack = _fold(text)
        for path in sorted(owned):
            forms = {path, path.replace(home, "~", 1), path.replace(home, "$HOME", 1)}
            if any(_fold(form) in haystack for form in forms):
                return "deny", (
                    f"This command looks like it modifies {path}, which kitctl manages. Change the "
                    "warehouse copy and run `kitctl apply` instead."
                )
    return None


def run_guard(harness: str) -> int:
    if os.environ.get(APPLYING):
        return 0
    try:
        from kitctl import paths as kit_paths

        decision = decide(_stdin_json(), kit_paths.resolve())
    except Exception as error:  # noqa: BLE001 - fail open
        print(f"kitctl guard skipped: {error}", file=sys.stderr)
        return 0
    if decision is None:
        return 0
    verdict, reason = decision
    if verdict == "ask" and harness != "claude":
        return 0  # Codex: only hard denials; asking isn't a documented outcome there
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    return 0
