import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import CANARY

ROOT = Path(__file__).resolve().parents[1]


def test_capture_keeps_structure_and_drops_values(harnesses: Path, tmp_path: Path) -> None:
    out = tmp_path / "captured"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/capture_fixtures.py"), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    texts = {p: p.read_text("utf-8") for p in out.rglob("*") if p.is_file()}
    assert texts and not any(CANARY in text for text in texts.values())
    assert not any(str(tmp_path) in text for text in texts.values())
    settings = json.loads((out / "claude/home/.claude/settings.json").read_text())
    assert set(settings) == {"model", "env", "enabledPlugins", "hooks"}
    config = json.loads((out / "codex/home/.codex/config.toml.json").read_text())
    assert config["mcp_servers"]["docs"]["command"] == "<str>"
    session = (out / "codex/home/.codex/sessions/2026/10/01/rollout-2026-10-01.jsonl").read_text()
    assert '"type": "McpToolCall"' in session
    claude_json = json.loads((out / "claude/home/.claude.json").read_text())
    assert any(key.startswith("<key-") for key in claude_json["projects"])
