"""Health report for explicit roots and, optionally, one loadout. Reports; never fixes.

Doctor runs each tool's declared check command (for example `vale --version`)
and resolves MCP launch binaries on PATH, but it never starts an MCP server,
never installs anything, never writes files, and never reads a trust store.
Hook activity comes only from heartbeat logs that primitives declare.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kitctl import engine
from kitctl.cost import listing_cost, over_budget, strictest_budget
from kitctl.inventory import inventory
from kitctl.model import DEFAULT_HARNESSES, ModelError, Primitive, discover, parse_primitive
from kitctl.resolve import resolve, split_target

Runner = Callable[[list[str]], tuple[int, str]]
CHECK_TIMEOUT = 15


def run_check(command: list[str]) -> tuple[int, str]:
    """Run a tool check without a shell. Returns (exit code, first output line); 127 if absent."""
    binary = shutil.which(command[0])
    if binary is None:
        return 127, ""
    try:
        done = subprocess.run(
            [binary, *command[1:]],
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 126, type(error).__name__
    output = (done.stdout or done.stderr or "").strip().splitlines()
    return done.returncode, output[0] if output else ""


def _finding(
    severity: str, check: str, subject: str, message: str, hint: str | None = None
) -> dict[str, Any]:
    return {
        "severity": severity,
        "check": check,
        "subject": subject,
        "message": message,
        "hint": hint,
    }


class Index:
    """Native identities -> warehouse references, per harness."""

    def __init__(self, warehouse: Path) -> None:
        self.primitives: dict[str, Primitive] = {}
        refs, _, _ = discover(warehouse)
        for ref in refs:
            try:
                self.primitives[ref] = parse_primitive(warehouse, ref)
            except ModelError:
                continue  # validate reports these; doctor works with what loads.
        self.plugins: dict[tuple[str, str], str] = {}
        self.skills: dict[tuple[str, str], str] = {}
        self.mcp: dict[str, str] = {}
        for ref, primitive in self.primitives.items():
            tail = primitive.name.rsplit("/", 1)[-1]
            for harness, native in primitive.harnesses.items():
                if primitive.kind == "plugin":
                    plugin_id = native.get("id") or self._derived(primitive, harness)
                    if plugin_id:
                        self.plugins[(harness, plugin_id)] = ref
                elif primitive.kind == "skill":
                    self.skills[(harness, native.get("skill", tail))] = ref
            if primitive.kind == "mcp":
                self.mcp[tail] = ref
                pin = primitive.data.get("pin") or {}
                if pin.get("package"):
                    self.mcp[str(pin["package"])] = ref

    def _derived(self, plugin: Primitive, harness: str) -> str | None:
        market = self.primitives.get(str(plugin.data.get("marketplace", "")))
        if market is None:
            return None
        name = market.harnesses.get(harness, {}).get("id") or market.name.rsplit("/", 1)[-1]
        return f"{plugin.data.get('plugin_name', plugin.name.rsplit('/', 1)[-1])}@{name}"

    def mcp_ref(self, server: dict[str, Any]) -> str | None:
        if server["id"] in self.mcp:
            return self.mcp[server["id"]]
        for arg in server.get("args", []):
            package = re.sub(r"@[^@/]+$", "", arg) if not arg.startswith("-") else ""
            if package in self.mcp:
                return self.mcp[package]
        return None


SCOPE_ORDER = {"user": 0, "project": 1, "local": 2}


def observe(report: Any, index: Index, harness: str) -> dict[str, Any]:
    """Effective native state for one harness, mapped onto warehouse identities."""
    native = report["native"]
    final: dict[str, tuple[bool, str, str]] = {}
    for item in sorted(
        (p for p in native["plugins"] if p["harness"] == harness),
        key=lambda p: SCOPE_ORDER.get(p["scope"], 1),
    ):
        final[item["id"]] = (item["enabled"], item["scope"], item["file"])
    switches = {
        s["skill"]: s["enabled"] for s in native["skill_switches"] if s["harness"] == harness
    }
    enabled: dict[str, list[str]] = {}
    unknown: list[str] = []
    local_true: set[str] = set()

    def add(ref: str | None, label: str, where: str) -> None:
        if ref is None:
            unknown.append(label)
        else:
            enabled.setdefault(ref, []).append(where)

    for plugin_id, (on, scope, file) in sorted(final.items()):
        if on:
            add(index.plugins.get((harness, plugin_id)), f"plugin {plugin_id}", f"{scope}: {file}")
            if scope == "local":
                local_true.add(plugin_id)
    skill_dirs = {
        "claude": "/.claude/skills/",
        "codex": "/.agents/skills/",
        "cursor": "/.cursor/skills/",
    }.get(harness)
    for entry in report["entries"]:
        if (
            skill_dirs
            and entry["kind"] == "skill"
            and skill_dirs in entry["path"].replace("\\", "/")
        ):
            if switches.get(entry["name"], True):
                add(
                    index.skills.get((harness, entry["name"])),
                    f"skill {entry['name']}",
                    entry["path"],
                )
    for server in native["mcp_servers"]:
        if server["harness"] == harness and server.get("enabled", True):
            add(
                index.mcp_ref(server), f"mcp {server['id']}", f"{server['scope']}: {server['file']}"
            )
    return {"enabled": enabled, "unknown": sorted(set(unknown)), "local_true": local_true}


def _heartbeat(primitive: Primitive, projects: list[Path]) -> dict[str, Any] | None:
    spec = primitive.data.get("heartbeat")
    if not spec:
        return None
    log = Path(tempfile.gettempdir()) / spec["tmpfile"]
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    rows = [line.split("\t") for line in lines if line.count("\t") >= 2]
    result: dict[str, Any] = {"log": str(log), "projects": {}}
    for project in projects:
        prefix = str(project)
        hits = [row for row in rows if row[2].startswith(prefix)]
        result["projects"][prefix] = (
            {"last_fired": hits[-1][0], "tool": hits[-1][1], "runs": len(hits)} if hits else None
        )
    return result


def doctor(
    warehouse: Path,
    roots: list[Path],
    target: str | None = None,
    harnesses: tuple[str, ...] = DEFAULT_HARNESSES,
    runner: Runner = run_check,
) -> dict[str, Any]:
    report = inventory(roots)
    findings: list[dict[str, Any]] = []
    for warning in report["warnings"]:
        findings.append(_finding("warning", "inventory", "inventory", warning))
    for block in report["native"]["managed_blocks"]:
        if block["status"] != "ok":
            findings.append(
                _finding(
                    "error",
                    "managed-block",
                    f"{block['file']}#{block['name']}",
                    block.get("problem", block["status"]),
                    "fix the markers by hand; Kit will not guess",
                )
            )
    home = Path.home().resolve()
    projects = [root.expanduser().resolve() for root in roots]
    saw_home = home in projects
    projects = [root for root in projects if root != home]
    reported_beats: set[str] = set()
    result: dict[str, Any] = {
        "roots": [str(r) for r in roots],
        "loadout": target,
        "harnesses": {},
        "tools": [],
        "findings": findings,
    }
    active = _active_set(roots, target, harnesses, findings)
    if (target is None and not any(active.values())) or not warehouse.is_dir():
        if target is not None:
            findings.append(_finding("error", "warehouse", str(warehouse), "warehouse not found"))
        return result
    index = Index(warehouse)
    tools: dict[str, tuple[str, dict[str, Any]]] = {}
    for harness in harnesses:
        if target is None:
            break  # a kit.yaml without --loadout: only the listing cost below
        state = observe(report, index, harness)
        section: dict[str, Any] = {
            "observed": {k: v for k, v in state["enabled"].items()},
            "unmanaged_unknown": state["unknown"],
        }
        result["harnesses"][harness] = section
        try:
            plan = resolve(warehouse, target, harness, set(state["enabled"]))
        except ModelError as error:
            for problem in error.problems:
                findings.append(
                    _finding(
                        "error",
                        problem.code,
                        harness,
                        problem.message,
                        "disable one provider in the loadout or remove the unmanaged one"
                        if problem.code == "slot-conflict"
                        else None,
                    )
                )
            try:
                plan = resolve(warehouse, target, harness)
            except ModelError:
                continue
        for warning in plan["warnings"]:
            if warning["code"] == "stale-disable" and not saw_home:
                # User scope was not inspected, so absence proves nothing.
                findings.append(
                    _finding(
                        "info",
                        "disable-unobserved",
                        harness,
                        warning["message"].replace(
                            "nothing enables it", "not enabled in these roots"
                        ),
                        "add --root ~ to check user scope",
                    )
                )
            else:
                findings.append(
                    _finding(
                        "warning",
                        warning["code"],
                        harness,
                        warning["message"],
                        "remove the disable from the loadout",
                    )
                )
        disabled = {d["identity"] for d in plan["disables"]}
        for ref in sorted(disabled & set(state["enabled"])):
            findings.append(
                _finding(
                    "info",
                    "unmanaged-provider",
                    f"{harness} {ref}",
                    f"enabled outside Kit ({'; '.join(state['enabled'][ref])}); "
                    f"{target} disables it",
                )
            )
        for native_id in sorted(state["local_true"]):
            ref = index.plugins.get((harness, native_id))
            if ref in disabled:
                findings.append(
                    _finding(
                        "error",
                        "disable-ineffective",
                        f"{harness} {ref}",
                        f"{native_id} is enabled in .claude/settings.local.json, which takes "
                        "precedence over the project-scope disable",
                        "remove it from settings.local.json by hand",
                    )
                )
        for effect in plan["effects"]:
            value = effect["value"]
            if not (isinstance(value, dict) and isinstance(value.get("command"), str)):
                continue
            if effect["target"] not in (".mcp.json", ".codex/config.toml", ".cursor/mcp.json"):
                continue
            server = effect["key"].rsplit("/", 1)[-1].rsplit(".", 1)[-1]
            if shutil.which(value["command"]) is None:
                hint = next(
                    (
                        t["install_hint"]
                        for kind in ("requires", "optional")
                        for t in plan["tools"][kind]
                        if t["identity"] == f"tool:{value['command']}"
                    ),
                    None,
                )
                findings.append(
                    _finding(
                        "error",
                        "mcp-launch",
                        f"{harness} mcp {server}",
                        f"{value['command']} is not on PATH",
                        hint,
                    )
                )
            else:
                findings.append(
                    _finding(
                        "info",
                        "mcp-launch",
                        f"{harness} mcp {server}",
                        f"{value['command']} resolves; server not started",
                    )
                )
        for gap in plan["gaps"]:
            if gap["severity"] == "trust-required":
                findings.append(
                    _finding(
                        "warning",
                        "pending-trust",
                        f"{harness} {gap['subject']}",
                        "pending trust: Kit cannot see the trust store",
                        gap["remedy"],
                    )
                )
        for item in plan["primitives"]:
            primitive = index.primitives.get(item["identity"])
            if primitive is None:
                continue
            beat = _heartbeat(primitive, projects)
            if beat is not None:
                section.setdefault("heartbeats", {})[item["identity"]] = beat
            if item["identity"] in reported_beats:
                beat = None
            reported_beats.add(item["identity"])
            for project, hit in (beat or {"projects": {}})["projects"].items():
                if hit:
                    findings.append(
                        _finding(
                            "info",
                            "hook-heartbeat",
                            item["identity"],
                            f"fired at {hit['last_fired']} after {hit['tool']} "
                            f"in {project} ({hit['runs']} runs logged)",
                        )
                    )
                else:
                    findings.append(
                        _finding(
                            "warning",
                            "hook-heartbeat",
                            item["identity"],
                            f"never fired in {project}",
                            "trust the hook if asked, edit one site file, rerun",
                        )
                    )
            if harness == "claude" and primitive.kind == "plugin":
                pin = (primitive.data.get("source") or {}).get("sha")
                for install in report["native"]["installed_plugins"]:
                    native_id = next(
                        (
                            nid
                            for (h, nid), r in index.plugins.items()
                            if h == "claude" and r == item["identity"]
                        ),
                        None,
                    )
                    if pin and install["id"] == native_id and install["sha"] not in (None, pin):
                        findings.append(
                            _finding(
                                "warning",
                                "pin-drift",
                                item["identity"],
                                f"installed at {install['sha']} ({install['scope']}), "
                                f"pinned to {pin}",
                                "update the plugin after the marketplace is pinned",
                            )
                        )
        for kind in ("requires", "optional"):
            for tool in plan["tools"][kind]:
                tools.setdefault(tool["identity"], (kind, tool))
    for ref, (kind, tool) in sorted(tools.items()):
        check = tool["check"] or {}
        command = [str(part) for part in check.get("command", [])]
        code, line = runner(command) if command else (127, "")
        expect = check.get("expect")
        if code == 127:
            status = "missing"
        elif code != 0:
            status = "failed"
        elif expect and not re.search(str(expect), line):
            status = "mismatch"
        else:
            status = "ok"
        result["tools"].append({"identity": ref, "kind": kind, "status": status, "output": line})
        if status != "ok":
            severity = "error" if kind == "requires" else "warning"
            detail = {
                "missing": "not found on PATH",
                "failed": f"check exited {code}",
                "mismatch": f"{line!r} does not match {expect!r}",
            }[status]
            findings.append(_finding(severity, "tool", ref, detail, tool["install_hint"]))
    _listing(warehouse, report, index, active, result, saw_home)
    return result


def _active_set(
    roots: list[Path],
    target: str | None,
    harnesses: tuple[str, ...],
    findings: list[dict[str, Any]],
) -> dict[str, list[tuple[str, str]]]:
    """(target, scope) pairs that list in a session, per harness: --loadout plus every loadout
    in the kit.yaml of an inspected root (~/.kit/kit.yaml when the root is home)."""
    active: dict[str, list[tuple[str, str]]] = {
        h: [(target, "project")] if target else [] for h in harnesses
    }
    for root in roots:
        root = root.expanduser().resolve()
        if not root.is_dir():
            continue
        try:
            intent = engine.read_intent(root)
        except (engine.EngineError, OSError) as error:
            findings.append(_finding("warning", "intent", str(root), str(error)))
            continue
        scope = "user" if engine.is_user_scope(root) else "project"
        for item in intent["loadouts"]:
            if scope == "project" and target and split_target(item)[0] == split_target(target)[0]:
                continue  # --loadout names the one doctor checks
            for harness in harnesses:
                if harness in intent["harnesses"] and (item, scope) not in active[harness]:
                    active[harness].append((item, scope))
    return active


def _listing(
    warehouse: Path,
    report: Any,
    index: Index,
    active: dict[str, list[tuple[str, str]]],
    result: dict[str, Any],
    saw_home: bool,
) -> None:
    """Listing cost per harness, and a warning when it is over the strictest budget."""
    installed: dict[str, str] = {}
    for item in report["native"]["installed_plugins"]:
        if item.get("path") and item["id"] not in installed:
            installed[item["id"]] = str(item["path"])
    budget = strictest_budget(
        warehouse, sorted({target for pairs in active.values() for target, _ in pairs})
    )
    for harness, targets in active.items():
        if not targets:
            continue
        plugin_ids = {ref: pid for (h, pid), ref in index.plugins.items() if h == harness}
        cost = listing_cost(warehouse, targets, harness, installed, plugin_ids)
        if (
            harness == "claude"
            and not installed
            and any("plugin:" in n for n in cost["not_measured"])
        ):
            cost["not_measured"].append(
                "Claude Code's installed plugins are read only when home is a root (--root ~)"
            )
        result["harnesses"].setdefault(harness, {})["listing_cost"] = cost
        for message in over_budget(cost, budget):
            result["findings"].append(
                _finding(
                    "warning",
                    "budget",
                    harness,
                    f"always-on listing: {message}",
                    "drop or hide (disable-model-invocation) a skill, trim the instructions, "
                    "or raise the budget",
                )
            )
