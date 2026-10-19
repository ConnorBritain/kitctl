"""kitctl context: skill listing budget offline, MCP tool sizes with --probe."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from kitctl import context
from kitctl import paths as kit_paths
from kitctl.cli import main
from kitctl.report import build
from tests.kits import write

TOOLS = [
    {"name": f"tool{i}", "description": "Does a thing. " * 10,
     "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}
    for i in range(3)
]  # fmt: skip

SERVER = """
import json, sys
TOOLS = json.loads(sys.argv[1])
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message["method"] == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif message["method"] == "tools/list":
        cursor = message.get("params", {}).get("cursor")
        result = {"tools": TOOLS[1:]} if cursor else {"tools": TOOLS[:1], "nextCursor": "p2"}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
"""


def test_offline_summary_and_budget_finding(harnesses: Path, monkeypatch, capsys) -> None:
    report = build([harnesses])
    rows = {row["repo"]: row for row in report["context"]["rows"]}
    repo_row = rows[str(harnesses)]
    assert repo_row["skills"] > rows[None]["skills"]  # the repo's own skills are added
    assert repo_row["listing_chars"] > 0
    assert {s["name"] for s in repo_row["mcp"]["codex"]} >= {"docs", "project-db"}
    assert not any(f["code"] == "skill-listing-over-budget" for f in report["findings"])
    monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "10")
    report = build([harnesses])
    assert any(f["code"] == "skill-listing-over-budget" for f in report["findings"])
    assert main(["context", "--root", str(harnesses)]) == 0
    out = capsys.readouterr().out
    assert "biggest descriptions" in out and "kitctl context --probe" in out


def test_probe_stdio_and_missing_env(sandbox: Path, tmp_path: Path, capsys) -> None:
    script = tmp_path / "fake_mcp.py"
    script.write_text(SERVER)
    claude_json = tmp_path / "home/.claude/.claude.json"
    write(claude_json, json.dumps({"mcpServers": {
        "fake": {"command": sys.executable, "args": [str(script), json.dumps(TOOLS)]},
        "needs-key": {"command": sys.executable, "args": [str(script), "[]"],
                      "env": {"API_KEY": "${KITCTL_TEST_UNSET_KEY}"}},
    }}))  # fmt: skip
    assert main(["context", "--probe", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    servers = {s["name"]: s for s in summary["rows"][0]["mcp"]["claude"]}
    assert servers["fake"]["tools"] == 3 and servers["fake"]["tokens"] > 100
    assert "KITCTL_TEST_UNSET_KEY" in servers["needs-key"]["status"]
    cached = context.load_probes(kit_paths.resolve())
    assert any(v.get("tools") == 3 for v in cached.values())
    assert main(["context", "--json"]) == 0  # cached results reused without probing
    assert json.loads(capsys.readouterr().out)["rows"][0]["mcp_tokens"]["claude"] > 100


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server API
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/locked":
            self.send_response(401)
            self.end_headers()
            return
        if "id" not in body:
            self.send_response(202)
            self.end_headers()
            return
        result = {"tools": TOOLS} if body["method"] == "tools/list" else {"capabilities": {}}
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Mcp-Session-Id", "s1")
        self.end_headers()
        self.wfile.write(f"event: message\ndata: {payload}\n\n".encode())

    def log_message(self, *args: object) -> None:
        pass


def test_probe_http(sandbox: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    write(tmp_path / "home/.claude/.claude.json", json.dumps({"mcpServers": {
        "remote": {"type": "http", "url": f"{base}/mcp"},
        "locked": {"type": "http", "url": f"{base}/locked"},
    }}))  # fmt: skip
    try:
        assert main(["context", "--probe", "--json", "--timeout", "5"]) == 0
    finally:
        server.shutdown()
    servers = {
        s["name"]: s for s in json.loads(capsys.readouterr().out)["rows"][0]["mcp"]["claude"]
    }
    assert servers["remote"]["tools"] == 3
    assert "sign-in" in servers["locked"]["status"]
