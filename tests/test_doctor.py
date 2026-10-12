import os
from pathlib import Path

import pytest

from kitctl.cli import main
from kitctl.doctor import harness_version, run


def shim(directory: Path, name: str, output: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (directory / f"{name}.cmd").write_text(f"@echo {output}\r\n")
    else:
        path = directory / name
        path.write_text(f"#!/bin/sh\necho '{output}'\n")
        path.chmod(0o755)


def test_versions_and_checks(
    harnesses: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    shim(bin_dir, "claude", "2.1.296 (Claude Code)")
    shim(bin_dir, "codex", "codex-cli 0.153.4")
    monkeypatch.setenv("PATH", str(bin_dir))
    assert harness_version("claude") == "2.1.296"
    result = run()
    checks = {c["check"]: c for c in result["checks"]}
    assert checks["Claude Code"]["status"] == "ok"
    assert checks["Codex"]["status"] == "info" and "0.153.4" in checks["Codex"]["detail"]
    assert checks["hook latency"]["status"] == "warn"
    assert checks["kitctl on PATH"]["status"] == "warn"


def test_missing_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert harness_version("claude") is None
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "`claude` not found" in out and "`codex` not found" in out
