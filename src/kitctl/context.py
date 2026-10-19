"""`kitctl context`: what your setup costs in context before you type anything.

Offline (no subprocesses): Claude Code's skill listing size per repository against its
character budget (1% of the context window; descriptions are dropped from the listing
when it overflows), and how many MCP servers each repository loads.

``--probe`` (opt-in, says so): starts each configured MCP server, performs the MCP
``initialize`` + ``tools/list`` handshake (no tool is called), and records tool counts
and an estimated token cost per server in kitctl's cache. Servers whose environment
references aren't set, or that need sign-in, are skipped with a reason.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from kitctl import fs
from kitctl.paths import Paths

CONTEXT_TOKENS = 200_000  # default model context window for the budget estimate
CHARS_PER_TOKEN = 4
PROTOCOL = "2025-06-18"
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def listing_budget(env: dict[str, str] | None = None) -> tuple[int, str]:
    env = dict(os.environ) if env is None else env
    fixed = env.get("SLASH_COMMAND_TOOL_CHAR_BUDGET", "").strip()
    if fixed.isdigit():
        return int(fixed), "SLASH_COMMAND_TOOL_CHAR_BUDGET"
    budget = CONTEXT_TOKENS * CHARS_PER_TOKEN // 100
    return budget, f"1% of a {CONTEXT_TOKENS // 1000}k-token context (~{budget} characters)"


def _visible(entry: dict[str, Any], repo: str | None, disabled: set[str]) -> bool:
    if entry["provided_by"] and entry["provided_by"] in disabled:
        return False
    if entry["scope"] in ("user", "managed", "plugin"):
        return True
    return repo is not None and entry["repo"] == repo


def summary(report: dict[str, Any], probes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Per repository (plus user-only): skill listing size and MCP servers loaded."""
    entries = report["entries"]
    disabled = {
        e["name"]
        for e in entries
        if e["kind"] == "plugin" and e["metadata"].get("enabled") is False
    }
    budget, basis = listing_budget()
    probes = probes or {}
    rows = []
    for repo in [None, *report["roots"]["repos"]]:
        skills = [
            e
            for e in entries
            if e["kind"] == "skill" and e["harness"] == "claude" and _visible(e, repo, disabled)
        ]
        chars = sum(int(e["metadata"].get("listing_chars") or len(e["name"])) for e in skills)
        biggest = sorted(skills, key=lambda e: -int(e["metadata"].get("listing_chars") or 0))[:5]
        servers: dict[str, list[dict[str, Any]]] = {}
        for harness in ("claude", "codex"):
            servers[harness] = [
                {"name": e["name"], "scope": e["scope"], **probe_info(probes, e)}
                for e in entries
                if e["kind"] == "mcp_server"
                and e["harness"] == harness
                and not e["metadata"].get("disabled")
                and _visible(e, repo, disabled)
            ]
        rows.append(
            {
                "repo": repo,
                "skills": len(skills),
                "listing_chars": chars,
                "over_budget": chars > budget,
                "biggest": [
                    {"name": e["name"], "chars": e["metadata"].get("listing_chars")}
                    for e in biggest
                ],
                "mcp": servers,
                "mcp_tokens": {h: sum(s.get("tokens") or 0 for s in servers[h]) for h in servers},
            }
        )
    return {"budget_chars": budget, "budget_basis": basis, "rows": rows}


def probe_key(entry: dict[str, Any]) -> str:
    return f"{entry['harness']}|{entry['scope']}|{entry['repo'] or ''}|{entry['name']}"


def probe_info(probes: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    found = probes.get(probe_key(entry))
    return {k: found[k] for k in ("tools", "tokens", "status") if k in found} if found else {}


def cache_path(paths: Paths) -> Path:
    return paths.kit_cache_dir / "context-probes.json"


def load_probes(paths: Paths) -> dict[str, Any]:
    try:
        data = json.loads(cache_path(paths).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# -- probing ---------------------------------------------------------------------------


class ProbeError(RuntimeError):
    pass


def _expand(value: Any, env: dict[str, str], missing: set[str]) -> Any:
    if isinstance(value, str):

        def sub(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            missing.add(name)
            return ""

        return _REF.sub(sub, value)
    if isinstance(value, list):
        return [_expand(v, env, missing) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, env, missing) for k, v in value.items()}
    return value


def _messages() -> list[dict[str, Any]]:
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "kitctl-context-probe", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]  # fmt: skip


def _probe_stdio(server: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    command = [server["command"], *[str(a) for a in server.get("args", [])]]
    env = {**os.environ, **{k: str(v) for k, v in (server.get("env") or {}).items()}}
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, text=True, encoding="utf-8",
        )  # fmt: skip
    except OSError as error:
        raise ProbeError(f"can't start: {error.strerror or error}") from error
    assert process.stdin is not None and process.stdout is not None
    responses: dict[int, Any] = {}
    done = threading.Event()

    def read() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and "id" in message:
                responses[message["id"]] = message
        done.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    tools: list[dict[str, Any]] = []
    try:
        for message in _messages():
            process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        cursor = None
        request = 2
        while True:
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            message = {"jsonrpc": "2.0", "id": request, "method": "tools/list", "params": params}
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            while request not in responses:
                if time.monotonic() > deadline or done.is_set():
                    raise ProbeError("no tools/list answer (timed out or exited)")
                time.sleep(0.02)
            result = responses[request].get("result") or {}
            if "error" in responses[request]:
                raise ProbeError(f"tools/list failed: {responses[request]['error'].get('message')}")
            tools += result.get("tools", [])
            cursor = result.get("nextCursor")
            request += 1
            if not cursor:
                return tools
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def _post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float,
          session: dict[str, str]) -> Any:  # fmt: skip
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream", **headers, **session},
    )  # fmt: skip
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            if response.headers.get("Mcp-Session-Id"):
                session["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
            text = response.read().decode("utf-8", "replace")
            kind = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ProbeError(f"needs sign-in (HTTP {error.code}); skipped") from error
        raise ProbeError(f"HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ProbeError(f"unreachable: {getattr(error, 'reason', error)}") from error
    if "id" not in body:
        return None
    if "text/event-stream" in kind:
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    message = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("id") == body["id"]:
                    return message
        raise ProbeError("no answer in the event stream")
    return json.loads(text) if text.strip() else None


def _probe_http(server: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    headers = {k: str(v) for k, v in (server.get("headers") or {}).items()}
    session: dict[str, str] = {}
    first, initialized = _messages()
    _post(server["url"], headers, first, timeout, session)
    _post(server["url"], headers, initialized, timeout, session)
    tools: list[dict[str, Any]] = []
    cursor = None
    request = 2
    while True:
        params: dict[str, Any] = {"cursor": cursor} if cursor else {}
        body = {"jsonrpc": "2.0", "id": request, "method": "tools/list", "params": params}
        answer = _post(server["url"], headers, body, timeout, session) or {}
        if "error" in answer:
            raise ProbeError(f"tools/list failed: {answer['error'].get('message')}")
        result = answer.get("result") or {}
        tools += result.get("tools", [])
        cursor = result.get("nextCursor")
        request += 1
        if not cursor:
            return tools


def probe(entries: list[dict[str, Any]], paths: Paths, timeout: float = 15.0,
          env: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """Probe every enabled, non-plugin MCP server; returns and caches the results."""
    from kitctl.adopt import _server_config
    from kitctl.compile import _neutral

    env = dict(os.environ) if env is None else env
    results = load_probes(paths)
    for entry in entries:
        if entry["kind"] != "mcp_server" or entry["metadata"].get("disabled"):
            continue
        key = probe_key(entry)
        try:
            config = _server_config(entry)
            if not isinstance(config, dict):
                raise ProbeError("definition not found")
            missing: set[str] = set()
            server = _expand(_neutral(config), env, missing)
            if missing:
                raise ProbeError(f"needs {', '.join(sorted(missing))} set; skipped")
            tools = (
                _probe_stdio(server, timeout)
                if server["type"] == "stdio"
                else _probe_http(server, timeout)
            )
            size = len(json.dumps(tools, separators=(",", ":")))
            results[key] = {
                "status": "ok",
                "tools": len(tools),
                "tokens": size // CHARS_PER_TOKEN,
                "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        except (ProbeError, OSError, ValueError, KeyError) as error:
            results[key] = {"status": str(error)}
    fs.write_atomic(cache_path(paths), json.dumps(results, indent=2, sort_keys=True))
    return results
